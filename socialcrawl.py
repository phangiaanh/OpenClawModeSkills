"""SocialCrawl client: per-platform keyword search + unified normalizers."""
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SC_BASE = "https://www.socialcrawl.dev/v1"


class SocialCrawlError(Exception):
    """Network/HTTP/envelope failure talking to SocialCrawl."""


def _api_key():
    key = os.environ.get("SOCIALCRAWL_API_KEY")
    if not key:
        raise SocialCrawlError("SOCIALCRAWL_API_KEY not set")
    return key


def _sc_get(path, params):
    """GET {SC_BASE}{path} with x-api-key; return the parsed JSON envelope.

    None-valued params are dropped. Raises SocialCrawlError on network/HTTP/
    JSON failure or a `success: false` envelope.
    """
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{SC_BASE}{path}?{qs}" if qs else f"{SC_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={"x-api-key": _api_key(), "Accept": "application/json",
                 "User-Agent": "epaphras/1.0"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            envelope = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise SocialCrawlError(f"request failed: {e}")
    if not envelope.get("success", False):
        raise SocialCrawlError(f"api error: {envelope.get('error') or envelope}")
    return envelope
