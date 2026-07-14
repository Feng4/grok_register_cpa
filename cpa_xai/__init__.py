"""CPA xAI (Grok) auth helpers — device code + browser consent.

Replicates CPA's internal --xai-login flow:
  SSO cookie → device code → browser consent → token → xai-<email>.json
Token has no referrer claim — exactly what CPA expects for api.x.ai/v1.
"""

from .accounts import AccountLine, existing_cpa_emails, parse_accounts_file
from .browser_mint import mint_via_browser

__all__ = [
    "AccountLine",
    "existing_cpa_emails",
    "mint_via_browser",
    "parse_accounts_file",
]
