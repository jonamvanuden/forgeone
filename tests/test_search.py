"""Brave Search backend: parses results, and turns API errors into a clear SearchUnavailable."""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from forgeone.tools import browser


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_brave_results(monkeypatch):
    payload = {"web": {"results": [
        {"title": "WHO <strong>sugar</strong> guideline", "url": "https://who.int/x", "description": "Less than 10&#39;%"},
        {"title": "no url"},
    ]}}
    seen = {}

    def fake(req, timeout):
        seen["url"], seen["key"] = req.full_url, req.get_header("X-subscription-token")
        return _Resp(json.dumps(payload).encode())

    monkeypatch.setattr(browser, "urlopen", fake)
    out = browser.brave_search("who free sugars", "k123", 5)
    assert out == [{"title": "WHO sugar guideline", "url": "https://who.int/x", "snippet": "Less than 10'%"}]
    assert "q=who+free+sugars" in seen["url"] and "count=5" in seen["url"] and seen["key"] == "k123"


def test_brave_error_is_clear(monkeypatch):
    def fake(req, timeout):
        raise HTTPError(req.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(browser, "urlopen", fake)
    with pytest.raises(browser.SearchUnavailable, match="429"):
        browser.brave_search("q", "k")


def test_search_uses_brave_when_key_set(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    monkeypatch.setattr(browser, "brave_search", lambda q, k, n: [{"title": "t", "url": "https://a.b", "snippet": ""}])
    assert browser.PlaywrightBrowser().search("x")[0]["url"] == "https://a.b"
