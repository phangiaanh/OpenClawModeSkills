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


def normalize_threads(item):
    a = item.get("author") or {}
    return {
        "post_id": item.get("id"), "url": item.get("url"),
        "text": item.get("text", ""),
        "author": {"handle": a.get("username"), "followers": a.get("followers", 0)},
        "created": item.get("published_on"),
        "likes": item.get("likes", 0), "comments": item.get("replies", 0),
        "shares": item.get("reposts", 0), "views": item.get("views", 0),
        "reach": item.get("views", 0),
    }


def normalize_tiktok(item):
    a = item.get("author") or {}
    s = item.get("stats") or {}
    return {
        "post_id": item.get("id"), "url": item.get("url"),
        "text": item.get("description", ""),
        "author": {"handle": a.get("unique_id"), "followers": a.get("follower_count", 0)},
        "created": _epoch_iso(item["create_time"]) if item.get("create_time") else None,
        "likes": s.get("digg_count", 0), "comments": s.get("comment_count", 0),
        "shares": s.get("share_count", 0), "views": s.get("play_count", 0),
        "reach": s.get("play_count", 0),
    }


def normalize_reddit(item):
    text = " ".join(filter(None, [item.get("title", ""), item.get("selftext", "")])).strip()
    return {
        "post_id": item.get("id"), "url": item.get("url"), "text": text,
        "author": {"handle": item.get("author"), "followers": 0},
        "created": _epoch_iso(item["created_utc"]) if item.get("created_utc") else None,
        "likes": item.get("score", 0), "comments": item.get("num_comments", 0),
        "shares": 0, "views": 0, "reach": 0,
    }


def _results(envelope):
    return (envelope.get("data") or {}).get("results", [])


_TIKTOK_LOOKBACK = {"24h": "yesterday", "7d": "this-week", "30d": "this-month"}
_REDDIT_LOOKBACK = {"24h": "day", "7d": "week", "30d": "month"}


def search_threads(query, lookback):
    start = (datetime.now(timezone.utc) - _lookback_delta(lookback)).date().isoformat()
    env = _sc_get("/threads/search", {"query": query, "start_date": start})
    return [normalize_threads(i) for i in _results(env)], env.get("credits_remaining")


def search_tiktok(query, lookback):
    env = _sc_get("/tiktok/search", {
        "query": query, "date_posted": _TIKTOK_LOOKBACK.get(lookback, "this-week"),
        "sort_by": "most-liked"})
    return [normalize_tiktok(i) for i in _results(env)], env.get("credits_remaining")


def search_reddit(query, lookback):
    env = _sc_get("/reddit/search", {
        "query": query, "sort": "top", "timeframe": _REDDIT_LOOKBACK.get(lookback, "week")})
    return [normalize_reddit(i) for i in _results(env)], env.get("credits_remaining")


SEARCH_ADAPTERS = {
    "threads": search_threads,
    "tiktok": search_tiktok,
    "reddit": search_reddit,
}
