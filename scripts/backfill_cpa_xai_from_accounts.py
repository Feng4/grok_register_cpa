#!/usr/bin/env python3
"""Batch mint CPA xai-*.json via device code + browser consent.

Replicates CPA's internal --xai-login: opens browser for consent.
Requires SSO cookie in accounts file. Browser popup is required.

Example:
  uv run python -u scripts/backfill_cpa_xai_from_accounts.py --limit 1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai import existing_cpa_emails, parse_accounts_file, AccountLine
from cpa_xai.browser_mint import mint_via_browser


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--accounts", default=str(_ROOT / "accounts_cli.txt"))
    ap.add_argument("--out-dir", default=str(_ROOT / "cpa_auths"))
    ap.add_argument("--cpa-dir", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--email", default="")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", action="store_false", dest="skip_existing")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--config", default=str(_ROOT / "config.json"))
    ap.add_argument("--sleep", type=float, default=5.0)
    args = ap.parse_args()

    if not args.proxy:
        try:
            cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                cfg = {k: v for k, v in cfg.items()
                       if not (isinstance(k, str) and (k.startswith("//") or k.startswith("#")))}
                args.proxy = (cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip()
                if not args.cpa_dir:
                    args.cpa_dir = (cfg.get("cpa_hotload_dir") or "").strip()
        except Exception:
            pass
    if not args.proxy:
        args.proxy = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
                      or os.environ.get("http_proxy") or "").strip()
    print(f"proxy={args.proxy or '(none)'}", flush=True)

    accounts = parse_accounts_file(args.accounts)
    if args.email:
        accounts = [a for a in accounts if a.email.lower() == args.email.lower()]

    have = set()
    if args.skip_existing:
        have |= {e.lower() for e in existing_cpa_emails(args.out_dir)}
        if args.cpa_dir:
            have |= {e.lower() for e in existing_cpa_emails(args.cpa_dir)}

    todo = []
    for a in accounts:
        if args.skip_existing and a.email.lower() in have:
            continue
        todo.append(a)
        if args.limit and len(todo) >= args.limit:
            break

    print(f"accounts total={len(accounts)} todo={len(todo)} out={args.out_dir}")
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    if args.cpa_dir:
        Path(args.cpa_dir).mkdir(parents=True, exist_ok=True)

    ok_n = fail_n = 0
    for i, acc in enumerate(todo, 1):
        print(f"\n=== [{i}/{len(todo)}] {acc.email} ===", flush=True)
        if not acc.sso:
            print("  SKIP: no SSO", flush=True)
            fail_n += 1
            continue

        record = mint_via_browser(
            email=acc.email,
            sso_cookie=acc.sso,
            auth_dir=args.out_dir,
            proxy=args.proxy,
            log=lambda m: print(f"  {m}", flush=True),
        )

        if record:
            ok_n += 1
            path = Path(args.out_dir) / f"xai-{''.join(ch if ch.isalnum() or ch in '._-@' else '_' for ch in acc.email)}.json"
            print(f"  OK: {path}", flush=True)
            if args.cpa_dir:
                dst = Path(args.cpa_dir) / path.name
                shutil.copy2(path, dst)
                os.chmod(dst, 0o600)
                print(f"  copied -> {dst}", flush=True)
        else:
            fail_n += 1
            print(f"  FAIL", flush=True)

        if args.sleep and i < len(todo):
            time.sleep(args.sleep)

    print(f"\n=== done ok={ok_n} fail={fail_n} ===", flush=True)
    return 0 if ok_n > 0 or not todo else 1


if __name__ == "__main__":
    raise SystemExit(main())
