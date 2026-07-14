"""Minimal browser-based device code consent for xAI OAuth.

CPA uses device code flow internally. Consent REQUIRES browser interaction
(Next.js server actions). This module opens a Chromium tab, injects SSO
cookie, clicks "Continue" → "Allow", then returns the clean token.

Clean token = no referrer, no bot_flag_source — exactly what CPA expects.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

import urllib.request
import urllib.error

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _request_device_code(proxy: str = "", log: LogFn = _noop) -> dict | None:
    data = f"client_id={CLIENT_ID}&scope={SCOPE}".encode()
    req = urllib.request.Request(
        DEVICE_CODE_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    opener = urllib.request.build_opener()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    try:
        with opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log(f"[ERR] device code: {e}")
        return None


def _poll_device_token(device_code: str, expiry: int = 1800,
                       proxy: str = "", log: LogFn = _noop) -> dict | None:
    deadline = time.time() + min(expiry - 5, 300)
    interval = 5
    while time.time() < deadline:
        data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": CLIENT_ID,
        }).encode()
        req = urllib.request.Request(
            TOKEN_URL, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        opener = urllib.request.build_opener()
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        try:
            with opener.open(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
                if body.get("access_token"):
                    log("[OK] device token obtained")
                    return body
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode())
            err = body.get("error", "")
            if err in ("authorization_pending", "slow_down"):
                if err == "slow_down":
                    interval = min(interval + 5, 30)
                time.sleep(interval)
                continue
            log(f"[ERR] device token: {err}: {body.get('error_description','')}")
            return None
        except Exception as e:
            log(f"[DEVICE] poll error: {e}")
            time.sleep(interval)
    log("[ERR] device token timeout")
    return None


def browser_device_consent(
    page: Any,
    verification_uri_complete: str,
    user_code: str,
    *,
    log: LogFn | None = None,
) -> bool:
    """Open device page in browser, click through to approve.

    page: DrissionPage ChromiumPage or tab object.
    Returns True if consent was completed successfully.
    """
    log = log or _noop

    def _text() -> str:
        try:
            return (page.raw_text or "")[:500]
        except Exception:
            return ""
    def _url() -> str:
        try:
            return page.url or ""
        except Exception:
            return ""

    try:
        log(f"[BROWSER] opening {verification_uri_complete[:100]}")
        page.get(verification_uri_complete, timeout=60)
        time.sleep(3.0)

        deadline = time.time() + 120
        while time.time() < deadline:
            url = _url()
            text = _text()
            log(f"[BROWSER] url={url[:120]}")

            # Done?
            if "device/done" in url or "done" in url:
                log("[BROWSER] done page reached")
                return True

            # Consent page → click Allow
            if "consent" in url or "授权" in text:
                # dismiss cookie banner first
                for btn_text in ("全部允许", "Accept all", "Accept All", "全部拒绝", "Reject all"):
                    try:
                        btn = page.ele(f"xpath://button[normalize-space(.)='{btn_text}']", timeout=0.5)
                        if btn:
                            btn.click(by_js=True)
                            log(f"[BROWSER] dismissed cookie banner: {btn_text}")
                            time.sleep(1.0)
                    except Exception:
                        pass
                # Click Allow (REAL click for React)
                for label in ("允许", "Allow", "Authorize", "Approve"):
                    try:
                        btn = page.ele(f"xpath://button[normalize-space(.)='{label}']", timeout=1.0)
                        if btn:
                            btn.click()  # real click
                            log(f"[BROWSER] clicked {label}")
                            time.sleep(3.0)
                            break
                    except Exception:
                        continue
                continue

            # Device page → click Continue
            if "device" in url and "consent" not in url:
                for label in ("继续", "Continue", "Next"):
                    try:
                        btn = page.ele(f"xpath://button[normalize-space(.)='{label}']", timeout=1.0)
                        if btn:
                            btn.click(by_js=True)
                            log(f"[BROWSER] clicked {label}")
                            time.sleep(2.0)
                            break
                    except Exception:
                        continue
                # Try filling user_code if visible
                try:
                    inp = page.ele("css:input[name='user_code']", timeout=0.3)
                    if inp:
                        cur = (inp.value or "").replace("-","")
                        uc = user_code.replace("-","")
                        if uc not in cur:
                            inp.clear()
                            inp.input(user_code)
                            log("[BROWSER] filled user_code")
                except Exception:
                    pass
                continue

            time.sleep(1.5)

        log("[BROWSER] consent timeout")
        return False
    except Exception as e:
        log(f"[ERR] browser: {e}")
        return False


def mint_via_browser(
    email: str,
    sso_cookie: str,
    auth_dir: str,
    *,
    proxy: str = "",
    page: Any = None,
    log: LogFn | None = None,
) -> dict | None:
    """Full device code → browser consent → token → CPA file."""
    log = log or _noop

    # Step 1: get device code
    dc = _request_device_code(proxy=proxy, log=log)
    if not dc:
        return None
    device_code = dc["device_code"]
    user_code = dc["user_code"]
    vuri = dc.get("verification_uri_complete", "")
    expiry = int(dc.get("expires_in", 1800))
    log(f"[MINT] user_code={user_code}")

    # Step 2: browser consent — reuse existing page or create standalone
    owned_browser = None
    if page is None:
        log("[MINT] creating standalone browser for consent...")
        try:
            from DrissionPage import Chromium, ChromiumOptions
            opts = ChromiumOptions()
            opts.auto_port()
            if proxy:
                # Chromium --proxy-server only takes host:port, no http://
                p = proxy.replace("http://", "").replace("https://", "").split("/")[0]
                opts.set_argument(f"--proxy-server={p}")
            owned_browser = Chromium(opts)
            page = owned_browser.latest_tab
            log("[MINT] standalone browser started")
        except Exception as e:
            log(f"[ERR] cannot create browser: {e}")
            return None
    else:
        log("[MINT] reusing existing browser page")

    # Inject SSO cookie for the consent domain
    try:
        page.set.cookies([{
            "name": "sso", "value": sso_cookie, "domain": ".x.ai", "path": "/",
            "secure": True, "httpOnly": True,
        }, {
            "name": "sso-rw", "value": sso_cookie, "domain": ".x.ai", "path": "/",
            "secure": True, "httpOnly": True,
        }])
        log("[MINT] SSO cookie injected into page")
    except Exception as e:
        log(f"[MINT] cookie inject failed (non-fatal): {e}")

    # Start polling in background
    token_box: dict = {}
    stop = threading.Event()
    def _poll():
        time.sleep(3)
        tok = _poll_device_token(device_code, expiry, proxy=proxy, log=log)
        token_box["token"] = tok
        stop.set()
    t = threading.Thread(target=_poll, daemon=True)
    t.start()

    ok = browser_device_consent(page, vuri, user_code, log=log)
    stop.set()
    t.join(timeout=30)

    # Only quit if we created the browser; leave caller's browser alone
    if owned_browser is not None:
        try:
            owned_browser.quit()
            log("[MINT] standalone browser closed")
        except Exception:
            pass

    if not ok or "token" not in token_box or not token_box["token"]:
        return None

    token = token_box["token"]

    # Build CPA record
    import base64
    from datetime import datetime, timezone

    access = token["access_token"]
    sub = ""
    try:
        seg = access.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        sub = json.loads(base64.urlsafe_b64decode(seg)).get("sub", "")
    except Exception:
        pass

    record = {
        "type": "xai",
        "auth_kind": "oauth",
        "email": email,
        "sub": sub,
        "access_token": access,
        "refresh_token": token.get("refresh_token", ""),
        "id_token": token.get("id_token", ""),
        "token_type": token.get("token_type", "Bearer"),
        "expires_in": token.get("expires_in", 21600),
        "expired": datetime.fromtimestamp(
            int(time.time()) + int(token.get("expires_in", 21600)), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "base_url": "https://api.x.ai/v1",
        "disabled": False,
    }

    import os
    from pathlib import Path
    out_dir = Path(auth_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in email)
    path = out_dir / f"xai-{safe}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    log(f"[MINT] wrote {path}")
    return record
