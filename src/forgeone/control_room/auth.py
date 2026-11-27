"""Login for the Control Room: one user, a password hash, a signed session cookie.

Settings (.env):
    FORGEONE_LOGIN_USER            the username
    FORGEONE_LOGIN_PASSWORD_HASH   made with:  python -m forgeone.control_room.auth
    FORGEONE_SESSION_SECRET        32+ random characters (openssl rand -hex 32)

Without all three the Control Room stays locked: every page asks for a login that cannot succeed.
Only the standard library is used, so no new dependencies.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import html
import os
import secrets
import sys
import time
from urllib.parse import parse_qs, quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

COOKIE = "fo_session"
SESSION_SECONDS = 12 * 3600
OPEN_PATHS = {"/login", "/logout"}
OPEN_PREFIXES = ("/static/",)   # CSS, JS and icons only: no data, needed to draw any page
MAX_FAILURES = 5            # per client address ...
LOCKOUT_SECONDS = 10 * 60   # ... within this window

_failures: dict[str, list[float]] = {}


# ---------------------------------------------------------------- password hashing
def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"scrypt${n}${r}${p}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(want)))
        return hmac.compare_digest(dk, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- settings
def _conf() -> tuple[str, str, bytes] | None:
    user = os.environ.get("FORGEONE_LOGIN_USER", "").strip()
    pw_hash = os.environ.get("FORGEONE_LOGIN_PASSWORD_HASH", "").strip()
    secret = os.environ.get("FORGEONE_SESSION_SECRET", "").strip()
    if not user or not pw_hash.startswith("scrypt$") or len(secret) < 32:
        return None
    return user, pw_hash, secret.encode()


def configured() -> bool:
    return _conf() is not None


# ---------------------------------------------------------------- session cookie
def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def make_session(user: str, secret: bytes, now: float | None = None) -> str:
    exp = int((now or time.time()) + SESSION_SECONDS)
    payload = f"{user}|{exp}|{secrets.token_hex(8)}"
    return f"{payload}|{_sign(secret, payload)}"


def session_user(request: Request) -> str | None:
    conf = _conf()
    raw = request.cookies.get(COOKIE)
    if not conf or not raw:
        return None
    user, _, secret = conf
    try:
        name, exp, nonce, sig = raw.split("|")
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(secret, f"{name}|{exp}|{nonce}")):
        return None
    if not exp.isdigit() or int(exp) < time.time() or not hmac.compare_digest(name, user):
        return None
    return name


# ---------------------------------------------------------------- lockout
def _locked(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failures[ip] = recent
    return len(recent) >= MAX_FAILURES


def _fail(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------- pages
def _safe_next(target: str | None) -> str:
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _page(message: str = "", next_url: str = "/", status: int = 200) -> HTMLResponse:
    note = f'<p class="msg">{html.escape(message)}</p>' if message else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · ForgeOne</title>
<style>
  *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f5fb;
  font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1b1838}}
  form{{width:min(360px,92vw);background:#fff;border:1px solid #e4e1f3;border-radius:14px;padding:30px 28px;
  box-shadow:0 12px 30px #2b1d6e14}}
  h1{{font-size:20px;margin:0 0 4px}}.sub{{color:#6b6888;font-size:13px;margin:0 0 22px}}
  label{{display:block;font-size:13px;font-weight:600;margin:14px 0 6px}}
  input{{width:100%;padding:11px 12px;border:1px solid #d8d4ec;border-radius:8px;font-size:15px}}
  input:focus{{outline:2px solid #7044ff55;border-color:#7044ff}}
  button{{width:100%;margin-top:22px;padding:12px;border:0;border-radius:8px;background:#7044ff;color:#fff;
  font-size:15px;font-weight:600;cursor:pointer}}button:hover{{background:#5d33ee}}
  .msg{{background:#fdecec;color:#a12626;border-radius:8px;padding:9px 12px;font-size:13px;margin:0 0 6px}}
</style></head><body>
<form method="post" action="/login">
  <h1>ForgeOne Control Room</h1><p class="sub">Sign in to continue</p>
  {note}
  <input type="hidden" name="next" value="{html.escape(next_url)}">
  <label for="u">Username</label><input id="u" name="username" autocomplete="username" required autofocus>
  <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
</form></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


async def login(request: Request) -> Response:
    if request.method == "GET":
        if session_user(request):
            return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
        msg = "" if configured() else "Login is not set up on the server yet."
        return _page(msg, _safe_next(request.query_params.get("next")))

    ip = request.client.host if request.client else "?"
    raw = (await request.body())[:4096].decode(errors="replace")
    form = {k: v[0] for k, v in parse_qs(raw).items()}
    next_url = _safe_next(form.get("next"))
    conf = _conf()
    if conf is None:
        return _page("Login is not set up on the server yet.", next_url, status=503)
    if _locked(ip):
        return _page("Too many failed attempts. Try again in 10 minutes.", next_url, status=429)
    user, pw_hash, secret = conf
    ok_user = hmac.compare_digest(form.get("username", ""), user)
    ok_pw = verify_password(form.get("password", ""), pw_hash)   # always run, so timing does not reveal the user
    if not (ok_user and ok_pw):
        _fail(ip)
        return _page("Wrong username or password.", next_url, status=401)
    _failures.pop(ip, None)
    resp = RedirectResponse(next_url, status_code=303)
    resp.set_cookie(COOKIE, make_session(user, secret), max_age=SESSION_SECONDS, httponly=True,
                    secure=request.url.scheme == "https", samesite="lax", path="/")
    return resp


async def logout(request: Request) -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


async def require_login(request: Request, call_next):
    """Middleware: everything except the login pages needs a valid session."""
    path = request.url.path
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES) or session_user(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "Sign in first.", "login": "/login"}, status_code=401,
                            headers={"Cache-Control": "no-store"})
    if request.method != "GET":
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    return RedirectResponse(f"/login?next={quote(path)}", status_code=303)


# ---------------------------------------------------------------- CLI: make a hash
def main() -> None:
    pw = getpass.getpass("New Control Room password: ")
    if len(pw) < 8:
        sys.exit("Use at least 8 characters.")
    if getpass.getpass("Repeat it: ") != pw:
        sys.exit("The two passwords differ.")
    print(hash_password(pw))


if __name__ == "__main__":
    main()
