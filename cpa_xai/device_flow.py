"""xAI OAuth Device Code flow — pure HTTP via SSO cookie.

CPA's internal --xai-login uses device code + browser. We replicate this
without browser: SSO cookie from registration authenticates the consent page.

Device code tokens have NO referrer claim and NO bot_flag_source, matching
exactly what CPA expects for api.x.ai/v1 routing.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from curl_cffi import requests

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
NEXT_ACTION_ID = "4005315a1d7e426de592990bb54bb37471f39dd6d2"

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def device_code_flow(
    sso_cookie: str,
    *,
    proxy: str = "",
    poll_interval: int = 5,
    expiry: int = 1800,
    timeout: float = 30.0,
    log: LogFn | None = None,
) -> dict | None:
    """SSO cookie → device code → consent → token. Pure HTTP, no browser.

    Returns dict with access_token, refresh_token, id_token?, expires_in.
    """
    log = log or _noop
    sso_cookie = (sso_cookie or "").strip()
    if not sso_cookie:
        log("[ERR] SSO empty")
        return None

    proxies = {"http": proxy, "https": proxy} if proxy else None
    s = requests.Session()
    if proxies:
        s.proxies = proxies
    for domain in (".x.ai", "accounts.x.ai", "auth.x.ai"):
        s.cookies.set("sso", sso_cookie, domain=domain)
        s.cookies.set("sso-rw", sso_cookie, domain=domain)

    # ── Step 1: request device code ──────────────────────────────────────
    log("[DEVICE] requesting device code...")
    try:
        r = s.post(
            DEVICE_CODE_URL,
            data={"client_id": CLIENT_ID, "scope": SCOPE},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            impersonate="chrome",
            timeout=timeout,
        )
    except Exception as e:
        log(f"[ERR] device code request: {e}")
        return None

    if r.status_code != 200:
        log(f"[ERR] device code HTTP {r.status_code}: {r.text[:200]}")
        return None
    try:
        dc = r.json()
    except Exception:
        log(f"[ERR] device code non-JSON: {r.text[:200]}")
        return None

    device_code = dc.get("device_code")
    user_code = dc.get("user_code")
    vuri_complete = dc.get("verification_uri_complete") or ""
    if not device_code or not user_code:
        log(f"[ERR] device code missing fields: {dc}")
        return None

    poll_interval = max(int(dc.get("interval", poll_interval)), 1)
    expiry = min(int(dc.get("expires_in", expiry)), expiry)
    log(f"[DEVICE] user_code={user_code} uri={vuri_complete[:100]}")

    # ── Step 2: open verification URI → consent page ─────────────────────
    log("[DEVICE] opening verification page...")
    try:
        r = s.get(
            vuri_complete or f"https://accounts.x.ai/oauth2/device?user_code={user_code}",
            impersonate="chrome",
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as e:
        log(f"[ERR] verification page: {e}")
        return None

    url = str(r.url)
    log(f"[DEVICE] landed: {url[:120]}")

    # Navigate to consent if needed (device page → login → consent)
    if "/oauth2/device/consent" in url or "/consent" in url:
        consent_url = url
    elif "/device" in url:
        # Click "继续" / "Continue" to move to consent
        try:
            r = s.get(url, impersonate="chrome", timeout=timeout, allow_redirects=True)
        except Exception:
            pass
        consent_url = str(r.url)
        log(f"[DEVICE] post-click: {consent_url[:120]}")
        # Try clicking "继续" button via form submit
        if "/device/consent" not in consent_url and "/consent" not in consent_url:
            try:
                # Direct consent: try going to authorize page first
                auth_url = f"https://auth.x.ai/oauth2/authorize?client_id={CLIENT_ID}&response_type=code&scope={urllib.parse.quote(SCOPE)}&redirect_uri=http://127.0.0.1:56121/callback"
                r = s.get(auth_url, impersonate="chrome", timeout=timeout, allow_redirects=True)
                consent_url = str(r.url)
                log(f"[DEVICE] authorize redirect: {consent_url[:120]}")
            except Exception:
                pass
    else:
        # May be auth page or login page
        consent_url = url

    # ── Step 3: submit device consent ────────────────────────────────────
    if "/consent" in consent_url or "/device/consent" in consent_url:
        log("[DEVICE] submitting device consent...")

        # Build consent payload for device flow
        consent_payload = json.dumps([{
            "action": "allow",
            "clientId": CLIENT_ID,
            "redirectUri": "http://127.0.0.1:56121/callback",
            "scope": SCOPE,
            "state": "",
            "codeChallenge": "",
            "codeChallengeMethod": "",
            "nonce": "",
            "principalType": "User",
            "principalId": "",
        }])

        try:
            r = s.post(
                consent_url,
                data=consent_payload,
                headers={
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Accept": "text/x-component",
                    "Origin": "https://accounts.x.ai",
                    "Referer": consent_url,
                    "Next-Action": NEXT_ACTION_ID,
                },
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            )
            log(f"[DEVICE] consent response: HTTP {r.status_code} url={str(r.url)[:100]}")
        except Exception as e:
            log(f"[ERR] consent: {e}")
            return None
    else:
        log(f"[DEVICE] no consent page found, trying direct token poll...")

    # ── Step 4: poll for token ───────────────────────────────────────────
    log("[DEVICE] polling for token...")
    deadline = time.time() + expiry - 5
    sleep_for = poll_interval

    while time.time() < deadline:
        try:
            r = s.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": CLIENT_ID,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                impersonate="chrome",
                timeout=timeout,
            )
        except Exception as e:
            log(f"[DEVICE] poll error: {e}, retrying in {sleep_for}s")
            time.sleep(sleep_for)
            continue

        if r.status_code == 200:
            try:
                token = r.json()
            except Exception:
                time.sleep(sleep_for)
                continue
            if token.get("access_token"):
                if not token.get("expires_in"):
                    token["expires_in"] = 21600
                if not token.get("token_type"):
                    token["token_type"] = "Bearer"
                log(f"[OK] device token obtained (scope={token.get('scope','')[:80]})")
                return token

        try:
            body = r.json()
            err = body.get("error", "")
            desc = body.get("error_description", "")
        except Exception:
            body = {}
            err = ""
            desc = ""

        if err in ("authorization_pending", "slow_down"):
            if err == "slow_down":
                sleep_for = min(sleep_for + 5, 30)
            log(f"[DEVICE] {err}, sleeping {sleep_for}s")
            time.sleep(sleep_for)
            continue
        if err in ("expired_token", "access_denied"):
            log(f"[ERR] device auth: {err}: {desc}")
            return None
        time.sleep(sleep_for)

    log("[ERR] device auth timed out")
    return None


def device_token_to_cpa_record(token: dict, email: str = "") -> dict:
    """Device code token → CPA-compatible xai auth record (api.x.ai/v1)."""
    import base64
    from datetime import datetime, timezone

    access = token.get("access_token") or ""
    refresh = token.get("refresh_token") or ""
    id_token = token.get("id_token") or ""

    # Decode JWT for email/sub
    sub = ""
    jwt_email = ""
    try:
        seg = access.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        payload = json.loads(base64.urlsafe_b64decode(seg))
        sub = str(payload.get("sub") or "")
        jwt_email = payload.get("email") or ""
    except Exception:
        pass
    if id_token:
        try:
            seg = id_token.split(".")[1]
            seg += "=" * (-len(seg) % 4)
            id_payload = json.loads(base64.urlsafe_b64decode(seg))
            jwt_email = id_payload.get("email") or jwt_email
        except Exception:
            pass

    if not email:
        email = jwt_email or ""

    expired = ""
    if token.get("expires_in"):
        try:
            expired = datetime.fromtimestamp(
                int(time.time()) + int(token["expires_in"]), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    return {
        "type": "xai",
        "auth_kind": "oauth",
        "email": email or "",
        "sub": sub,
        "access_token": access,
        "refresh_token": refresh,
        "id_token": id_token,
        "token_type": token.get("token_type", "Bearer"),
        "expires_in": token.get("expires_in", 21600),
        "expired": expired,
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "base_url": "https://api.x.ai/v1",
        "disabled": False,
    }
