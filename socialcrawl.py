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


def _epoch_iso(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def _lookback_delta(lookback):
    if lookback.endswith("h"):
        return timedelta(hours=int(lookback[:-1]))
    if lookback.endswith("d"):
        return timedelta(days=int(lookback[:-1]))
    return timedelta(days=1)


def _normalize_post(item):
    """Unwrap the {post, computed} envelope and return a unified record."""
    p = item.get("post") or item
    c = item.get("computed") or {}
    a = p.get("author") or {}
    e = p.get("engagement") or {}
    return {
        "post_id": p.get("id"), "url": p.get("url"),
        "text": (p.get("content") or {}).get("text", ""),
        "author": {"handle": a.get("username"), "followers": a.get("followers", 0)},
        "created": _epoch_iso(p["published_at"]) if p.get("published_at") else None,
        "likes": e.get("likes") or 0, "comments": e.get("comments") or 0,
        "shares": e.get("shares") or 0, "views": e.get("views") or 0,
        "reach": c.get("estimated_reach") or e.get("views") or 0,
        "language": (c.get("language") or "").lower(),
    }


def normalize_threads(item):
    return _normalize_post(item)


def normalize_tiktok(item):
    return _normalize_post(item)


def normalize_reddit(item):
    return _normalize_post(item)


def _results(envelope):
    data = envelope.get("data") or {}
    return data.get("items") or data.get("results") or []


_TIKTOK_LOOKBACK = {"24h": "yesterday", "7d": "this-week", "30d": "this-month"}
_REDDIT_LOOKBACK = {"24h": "day", "7d": "week", "30d": "month"}


def search_threads(query, lookback, region=None):
    start = (datetime.now(timezone.utc) - _lookback_delta(lookback)).date().isoformat()
    env = _sc_get("/threads/search", {"query": query, "start_date": start})
    return [normalize_threads(i) for i in _results(env)], env.get("credits_remaining")


def search_tiktok(query, lookback, region=None):
    params = {"query": query, "date_posted": _TIKTOK_LOOKBACK.get(lookback, "this-week"),
              "sort_by": "most-liked"}
    if region:
        params["region"] = region          # uppercase ISO; soft proxy signal
    env = _sc_get("/tiktok/search", params)
    return [normalize_tiktok(i) for i in _results(env)], env.get("credits_remaining")


def search_reddit(query, lookback, region=None):
    env = _sc_get("/reddit/search", {
        "query": query, "sort": "top", "timeframe": _REDDIT_LOOKBACK.get(lookback, "week")})
    return [normalize_reddit(i) for i in _results(env)], env.get("credits_remaining")


SEARCH_ADAPTERS = {
    "threads": search_threads,
    "tiktok": search_tiktok,
    "reddit": search_reddit,
}
