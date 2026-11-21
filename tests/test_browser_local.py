"""Real Playwright against a local http.server. Skipped when Playwright/Chromium is not installed."""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    (root / "a.html").write_text('<html><head><title>A</title></head><body><p>Free sugars below 10% of energy.</p>'
                                 '<a href="/b.html">Next page</a></body></html>')
    (root / "b.html").write_text("<html><head><title>B</title></head><body>Page B text.</body></html>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    from forgeone.tools.browser import PlaywrightBrowser
    b = PlaywrightBrowser()
    try:
        b._run(b._ensure)
    except Exception as e:  # chromium not installed
        pytest.skip(f"chromium unavailable: {e}")
    yield b
    b.close()


def test_open_find_click(site, browser, monkeypatch):
    monkeypatch.setenv("FORGEONE_ALLOW_PRIVATE_URLS", "1")
    snap = browser.open(f"{site}/a.html")
    assert snap["title"] == "A" and "10%" in snap["text"] and snap["links"][0]["url"].endswith("/b.html")
    assert any("10%" in h for h in browser.find("sugars"))
    assert browser.click("Next page")["title"] == "B"
    with pytest.raises(RuntimeError, match="HTTP 404"):
        browser.open(f"{site}/missing.html")


def test_local_addresses_blocked_by_default(site, browser, monkeypatch):
    monkeypatch.delenv("FORGEONE_ALLOW_PRIVATE_URLS", raising=False)
    with pytest.raises(ValueError, match="blocked"):
        browser.open(f"{site}/a.html")
