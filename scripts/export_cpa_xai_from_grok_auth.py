#!/usr/bin/env python3
"""Convert ~/.grok/auth.json (Grok Build CLI) -> CPA xai-<email>.json.

Usage:
  uv run python scripts/export_cpa_xai_from_grok_auth.py \\
    --auth-json ~/.grok/auth.json \\
    --out-dir ./cpa_auths
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai import token_to_cpa_record, write_cpa_auth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-json", default=str(Path.home() / ".grok" / "auth.json"))
    ap.add_argument("--out-dir", default=str(_ROOT / "cpa_auths"))
    ap.add_argument("--base-url", default="https://cli-chat-proxy.grok.com/v1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = json.loads(Path(args.auth_json).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise SystemExit(f"empty auth.json: {args.auth_json}")
    entry = next(iter(raw.values()))
    if not isinstance(entry, dict):
        raise SystemExit("auth.json entry is not an object")

    access = entry.get("key") or entry.get("access_token") or ""
    refresh = entry.get("refresh_token") or ""
    email = entry.get("email") or ""

    # Build a token dict compatible with token_to_cpa_record
    token = {"access_token": access, "refresh_token": refresh}
    if entry.get("id_token"):
        token["id_token"] = entry["id_token"]
    if entry.get("expires_in"):
        token["expires_in"] = int(entry["expires_in"])

    record = token_to_cpa_record(token, email=email)
    for _k in ("headers", "sso", "redirect_uri"):
        record.pop(_k, None)

    if args.dry_run:
        redacted = dict(record)
        for k in ("access_token", "refresh_token", "id_token"):
            if k in redacted and isinstance(redacted[k], str) and redacted[k]:
                redacted[k] = redacted[k][:16] + f"...(len={len(redacted[k])})"
        print(json.dumps(redacted, indent=2, ensure_ascii=False))
        return 0

    path = write_cpa_auth(args.out_dir, record)
    print(f"wrote {path}")
    print(f"email={record.get('email')} base_url={record.get('base_url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
