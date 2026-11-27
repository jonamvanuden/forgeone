"""Control Room login: locked by default, signs in with the right password, locks out guessers."""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from forgeone.control_room import auth

PASSWORD = "correct horse battery staple"


def _app() -> Starlette:
    app = Starlette(routes=[
        Route("/", lambda r: PlainTextResponse("dashboard")),
        Route("/api/home", lambda r: JSONResponse({"ok": True})),
        Route("/login", auth.login, methods=["GET", "POST"]),
        Route("/logout", auth.logout, methods=["GET", "POST"]),
    ])
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth.require_login)
    return app


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FORGEONE_LOGIN_USER", "jonam")
    monkeypatch.setenv("FORGEONE_LOGIN_PASSWORD_HASH", auth.hash_password(PASSWORD, n=2**10))
    monkeypatch.setenv("FORGEONE_SESSION_SECRET", "x" * 40)
    auth._failures.clear()


def test_hash_roundtrip():
    h = auth.hash_password("secret-password", n=2**10)
    assert auth.verify_password("secret-password", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("x", "garbage")


def test_locked_when_not_configured(monkeypatch):
    for k in ("FORGEONE_LOGIN_USER", "FORGEONE_LOGIN_PASSWORD_HASH", "FORGEONE_SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(_app())
    assert c.get("/", follow_redirects=False).status_code == 303
    assert c.get("/api/home").status_code == 401
    r = c.post("/login", data={"username": "a", "password": "b"})
    assert r.status_code == 503


def test_login_flow(configured):
    c = TestClient(_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.get("/api/home").status_code == 401

    r = c.post("/login", data={"username": "jonam", "password": "nope"}, follow_redirects=False)
    assert r.status_code == 401 and "Wrong username or password" in r.text

    r = c.post("/login", data={"username": "jonam", "password": PASSWORD, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/").text == "dashboard"
    assert c.get("/api/home").json() == {"ok": True}

    c.get("/logout")
    assert c.get("/api/home").status_code == 401


def test_tampered_cookie_rejected(configured):
    c = TestClient(_app())
    c.post("/login", data={"username": "jonam", "password": PASSWORD}, follow_redirects=False)
    good = c.cookies.get(auth.COOKIE)
    name, exp, nonce, sig = good.split("|")
    c.cookies.set(auth.COOKIE, f"{name}|{int(exp) + 99999}|{nonce}|{sig}")
    assert c.get("/api/home").status_code == 401


def test_open_redirect_blocked(configured):
    c = TestClient(_app())
    r = c.post("/login", data={"username": "jonam", "password": PASSWORD, "next": "//evil.example"},
               follow_redirects=False)
    assert r.headers["location"] == "/"


def test_lockout_after_failures(configured):
    c = TestClient(_app())
    for _ in range(auth.MAX_FAILURES):
        c.post("/login", data={"username": "jonam", "password": "wrong"})
    r = c.post("/login", data={"username": "jonam", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 429
