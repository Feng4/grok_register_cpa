"""Register-machine hook: mint CPA xai auth via device code + browser consent.

Replicates CPA's internal --xai-login: device code → browser consent → token.
Uses SSO cookie from registration to auto-authenticate the consent page.
Browser popup is required (same as CPA's OAuth login).
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

_REG_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT = _REG_DIR / "cpa_auths"


def export_cookies_from_page(page: Any) -> list[dict]:
    """Best-effort export of cookies from a DrissionPage tab/browser."""
    if page is None:
        return []
    cookies = None
    for getter in (
        lambda: page.cookies(all_domains=True, all_info=True),
        lambda: page.cookies(all_domains=True),
        lambda: page.cookies(),
    ):
        try:
            cookies = getter()
            if cookies:
                break
        except (TypeError, Exception):
            continue
    if not cookies:
        try:
            browser = getattr(page, "browser", None)
            if browser is not None:
                cookies = browser.cookies()
        except Exception:
            cookies = None
    if isinstance(cookies, list):
        return [c for c in cookies if isinstance(c, dict)]
    return []


def export_cpa_xai_for_account(
    email: str,
    password: str,
    *,
    page: Any | None = None,
    cookies: Any | None = None,
    sso: str | None = None,
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    cfg = config or {}
    log = log_callback or (lambda m: print(m, flush=True))

    if not cfg.get("cpa_export_enabled", True):
        log("[cpa] export disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}

    out_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_OUT).expanduser()
    if not out_dir.is_absolute():
        out_dir = (_REG_DIR / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    hotload_raw = (cfg.get("cpa_hotload_dir") or "").strip()
    cpa_dir = Path(hotload_raw).expanduser() if hotload_raw else None
    if cpa_dir and not cpa_dir.is_absolute():
        cpa_dir = (_REG_DIR / cpa_dir).resolve()

    proxy = (cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip()
    if not proxy:
        proxy = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
                 or os.environ.get("http_proxy") or "").strip()

    sso_val = (sso or "").strip()
    if not sso_val and isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                sso_val = str(c.get("value"))
                break
    if not sso_val:
        log("[cpa] no SSO, skip")
        return {"ok": False, "error": "no SSO cookie"}

    from cpa_xai.browser_mint import mint_via_browser

    log(f"[cpa] device flow + browser: {email}")
    record = mint_via_browser(
        email=email,
        sso_cookie=sso_val,
        auth_dir=str(out_dir),
        proxy=proxy,
        page=page,
        log=lambda m: log(f"[cpa] {m}"),
    )

    if not record:
        log("[cpa] mint failed")
        return {"ok": False, "email": email, "error": "mint_via_browser failed"}

    path = out_dir / f"xai-{''.join(ch if ch.isalnum() or ch in '._-@' else '_' for ch in email)}.json"

    if cfg.get("cpa_copy_to_hotload", False) and cpa_dir:
        try:
            cpa_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, cpa_dir / path.name)
            os.chmod(cpa_dir / path.name, 0o600)
            log(f"[cpa] hotload -> {cpa_dir / path.name}")
        except Exception as e:
            log(f"[cpa] hotload failed: {e}")

    return {"ok": True, "email": email, "path": str(path)}
