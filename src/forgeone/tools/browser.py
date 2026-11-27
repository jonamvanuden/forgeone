"""Playwright browser on ONE dedicated thread.

Playwright's sync API is bound to the thread that created it, so every operation is
submitted to a single-worker executor. As root (our server) Chromium needs --no-sandbox.
Setup on a new machine: `pip install playwright && playwright install --with-deps chromium`.
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urljoin, urlparse

NAV_TIMEOUT_MS = 30_000
MAX_LINKS = 40
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0 Safari/537.36 ForgeOne-VoyagerOne")


def is_private_url(url: str) -> bool:
    """True for localhost, private, link-local (cloud metadata) and reserved addresses.
    Agents must not reach the server itself or the internal network (SSRF)."""
    host = urlparse(url).hostname or ""
    if host in ("localhost", "") or host.endswith(".local"):
        return True
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False  # let the browser report the DNS error
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def check_url(url: str) -> None:
    # http(s) only: file:// would let a manipulated agent read local files (.env!)
    if not re.match(r"^https?://", url):
        raise ValueError("url must start with http:// or https://")
    if os.getenv("FORGEONE_ALLOW_PRIVATE_URLS") != "1" and is_private_url(url):
        raise ValueError("blocked: private or local network address")


_DDG_BLOCKED = re.compile(r"anomaly|captcha|bots use DuckDuckGo|challenge-form", re.I)
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class SearchUnavailable(RuntimeError):
    """The search backend refused or failed: the agent should say so, not report 'no results'."""


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def brave_search(query: str, key: str, max_results: int = 8, timeout: float = 20) -> list[dict]:
    """Brave Search API (https://brave.com/search/api/), plain HTTPS, no browser needed."""
    url = f"{BRAVE_URL}?q={quote_plus(query)}&count={max(1, min(max_results, 20))}"
    req = Request(url, headers={"Accept": "application/json", "X-Subscription-Token": key,
                                "User-Agent": "ForgeOne-VoyagerOne"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except HTTPError as e:
        hint = {401: "the API key is wrong", 403: "the API key is not allowed", 429: "rate or quota limit reached"}
        raise SearchUnavailable(f"Brave Search returned HTTP {e.code}: {hint.get(e.code, 'request failed')}") from e
    except (URLError, TimeoutError, ValueError) as e:
        raise SearchUnavailable(f"Brave Search could not be reached: {e}") from e
    out = []
    for r in (data.get("web") or {}).get("results", [])[:max_results]:
        if r.get("url"):
            out.append({"title": _plain(r.get("title", "")), "url": r["url"], "snippet": _plain(r.get("description", ""))})
    return out


class PlaywrightBrowser:
    def __init__(self, headless: bool = True):
        self._headless = headless
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forgeone-browser")
        self._pw = self._browser = self._page = self._context = None

    def _run(self, fn, *args):
        return self._exec.submit(fn, *args).result()

    def _ensure(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright

            args = ["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self._headless, args=args)
            self._context = self._browser.new_context(user_agent=USER_AGENT)
            self._page = self._context.new_page()
            self._page.set_default_timeout(NAV_TIMEOUT_MS)
        return self._page

    def _fresh_page(self):
        """After a failed load Chromium moves the tab to an error page a moment later, which
        interrupts the next load. Continue in a new tab instead."""
        old, self._page = self._page, self._context.new_page()
        self._page.set_default_timeout(NAV_TIMEOUT_MS)
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        return self._page

    def _goto(self, url: str):
        page = self._ensure()
        try:
            return page, page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            page = self._fresh_page()
            if "interrupted by another navigation" in str(e):     # the error page of an earlier failure
                return page, page.goto(url, wait_until="domcontentloaded")
            raise

    @staticmethod
    def _snapshot(page) -> dict:
        text = page.inner_text("body") if page.query_selector("body") else ""
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        links = []
        for a in page.query_selector_all("a[href]")[: MAX_LINKS * 3]:
            label = (a.inner_text() or "").strip().replace("\n", " ")
            href = a.get_attribute("href") or ""
            if label and href and not href.startswith(("javascript:", "#", "mailto:")):
                links.append({"text": label[:80], "url": urljoin(page.url, href)})
            if len(links) >= MAX_LINKS:
                break
        return {"url": page.url, "title": page.title(), "text": text, "links": links}

    def open(self, url: str) -> dict:
        def op():
            check_url(url)
            page, resp = self._goto(url)
            if resp is not None and resp.status >= 400:   # an error page is a failed tool call, not content
                raise RuntimeError(f"HTTP {resp.status} for {url}")
            return self._snapshot(page)
        return self._run(op)

    def click(self, text: str) -> dict:
        def op():
            page = self._ensure()
            page.get_by_text(text, exact=False).first.click()
            page.wait_for_load_state("domcontentloaded")
            try:
                check_url(page.url)
            except ValueError:
                page.go_back()
                raise
            return self._snapshot(page)
        return self._run(op)

    def find(self, query: str, context_chars: int = 300) -> list[str]:
        def op():
            page = self._ensure()
            body = page.inner_text("body") if page.query_selector("body") else ""
            low, q, hits, start = body.lower(), query.lower(), [], 0
            while len(hits) < 10 and (i := low.find(q, start)) >= 0:
                hits.append(body[max(0, i - context_chars): i + len(q) + context_chars].strip())
                start = i + len(q)
            return hits
        return self._run(op)

    def search(self, query: str, max_results: int = 8) -> list[dict]:
        """Web search: the Brave Search API when BRAVE_API_KEY is set, else DuckDuckGo's HTML page."""
        key = os.getenv("BRAVE_API_KEY", "").strip()
        if key:
            return brave_search(query, key, max_results)

        def op():
            page, _ = self._goto(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
            out = []
            for r in page.query_selector_all(".result")[:max_results]:
                a, snip = r.query_selector("a.result__a"), r.query_selector(".result__snippet")
                if a:
                    out.append({"title": a.inner_text().strip(), "url": a.get_attribute("href"),
                                "snippet": snip.inner_text().strip() if snip else ""})
            if not out and _DDG_BLOCKED.search(page.content()):
                raise SearchUnavailable("web search is blocked: DuckDuckGo answered with a bot check. "
                                        "Set BRAVE_API_KEY to use the Brave Search API instead.")
            return out
        return self._run(op)

    def close(self) -> None:
        def op():
            try:
                if self._browser:
                    self._browser.close()
                if self._pw:
                    self._pw.stop()
            finally:
                self._pw = self._browser = self._page = self._context = None
        try:
            self._run(op)
        finally:
            self._exec.shutdown(wait=False)
