# SocialCrawl Interval-Polling Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the zernio webhook data-fetching stage with SocialCrawl per-platform keyword-search interval polling: each tick searches the active mode's active topics across its searchable platforms, scores results with a local trend score, gates them against absolute floors + top-N per (topic × platform), re-logs trajectories to JSONL, and removes all zernio/webhook code.

**Architecture:** A thin JS timer (gateway patch) shells out to `python3 engine.py poll` on an interval. All logic lives in Python: a new `socialcrawl.py` client (`_sc_get` + per-platform search adapters + unified normalizers) and new pure scoring/gating/state functions plus a `run_poll` orchestrator in `engine.py`. The picker switches from zernio accounts to a static capability map; the panel's `🔔 Notifications` button becomes `📡 Polling` (toggles `poll.enabled`).

**Tech Stack:** Python 3.13 standard library only (`urllib`, `ssl`, `json`, `datetime`, `zoneinfo`, `copy`, `statistics`), pytest, and a Python-authored JS patch (`scripts/full_patch_v2.py`).

**Spec:** `docs/superpowers/specs/2026-06-13-socialcrawl-interval-polling-design.md`

---

## File structure

- **Create `socialcrawl.py`** — SocialCrawl HTTP client. `_sc_get(path, params)` (auth + envelope parse), per-platform `search_threads/search_tiktok/search_reddit` adapters returning `(records, credits_remaining)`, pure `normalize_*` functions producing the unified record, and the `SEARCH_ADAPTERS` capability map. Imports nothing from `engine` (no circular dependency).
- **Modify `engine.py`** — add `import socialcrawl`; new pure functions (`topic_query`, `raw_engagement`, `platform_baseline`, `magnitude`, `velocity`, `recency`, `trend_score`, `passes_floor`, `_hours_since`); state-store IO (`load_state`, `save_state`, `update_state`, `age_out_state`); poll gate/config (`DEFAULT_POLL`, `poll_config`, `in_window`, `poll_gate`); the `run_poll` orchestrator; the `cli_poll` wrapper + `poll` CLI command; rewired `cb_notif`→polling toggle and `render_modes` button; rewritten capability-map picker (`render_platforms`, `pick_platform`, `create_mode`, `submit_name`). Remove all webhook/zernio code.
- **Modify `tests/test_engine.py`** — add tests for the new client/scoring/poll code; delete webhook/zernio/account tests; rewrite picker + render_modes tests.
- **Create `tests/fixtures/{threads,tiktok,reddit}_search.sample.json`** — sample SocialCrawl envelopes for adapter/normalizer tests.
- **Modify `tests/fixtures/modes.sample.json`** — drop the `webhook` block (keep multi-mode for coverage; platforms stay strings); add a `query` to one topic.
- **Modify `templates/modes.default.json`** — reseed to a single `culture_drama` mode (5 topics with `query`, lowercase platform names) + a `poll` block; remove the `webhook` block.
- **Modify `scripts/full_patch_v2.py`** — remove the webhook receiver patch; add the poll-timer patch (`_EPAPHRAS_POLL_V1`).
- **Modify `.gitignore`** — replace `webhook_events.jsonl` with `trending_posts.jsonl`, `poll_state.json`, `poll.lock`.
- **Modify `SKILL.md` / `README.md`** — rewrite for discovery/polling.

**Reconciliation note:** the spec illustrates the Reddit floor as `{"upvotes": 500}`. Reddit upvotes normalize to the unified `likes` field, so floors reference unified field names — the Reddit floor is `{"likes": 500}`.

---

## Task 0: Spike — confirm SocialCrawl response shapes

The exact JSON field paths for the three search endpoints are behind a JS-rendered docs page. Before coding normalizers, confirm them against the live API (or the `/v1/openapi.json` spec) and adjust the fixtures in Task 2 to match real responses. This is the one real unknown.

- [ ] **Step 1: Make one real call per platform**

Run (requires a real key):
```bash
export SOCIALCRAWL_API_KEY=sc_xxx
curl -s -H "x-api-key: $SOCIALCRAWL_API_KEY" \
  "https://www.socialcrawl.dev/v1/threads/search?query=esports&start_date=2026-06-12" | python3 -m json.tool | head -60
curl -s -H "x-api-key: $SOCIALCRAWL_API_KEY" \
  "https://www.socialcrawl.dev/v1/tiktok/search?query=esports&date_posted=yesterday&sort_by=most-liked" | python3 -m json.tool | head -60
curl -s -H "x-api-key: $SOCIALCRAWL_API_KEY" \
  "https://www.socialcrawl.dev/v1/reddit/search?query=esports&sort=top&timeframe=day" | python3 -m json.tool | head -60
```
Expected: a `{"success": true, "data": {...}, "credits_remaining": N}` envelope per call.

- [ ] **Step 2: Record the real field paths**

For each platform note: the array path inside `data` (this plan assumes `data.results`), and the per-post keys for id, url, text, author handle + followers, created timestamp (ISO vs epoch), likes, comments, shares, views. If they differ from the assumptions in Tasks 1–2, update the fixtures (Task 2) and normalizers (Task 2) to match the real shapes. **No commit** — this is investigation; the assumptions are encoded by the Task 2 fixtures.

---

## Task 1: SocialCrawl client — `_sc_get` + auth

**Files:**
- Create: `socialcrawl.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:
```python
import socialcrawl


def test_sc_get_parses_envelope(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return (b'{"success": true, "platform": "threads", '
                    b'"data": {"results": [{"id": "x"}]}, "credits_remaining": 940}')

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["key"] = req.headers.get("X-api-key")
        return FakeResp()

    monkeypatch.setattr(socialcrawl.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "sc_test")
    env = socialcrawl._sc_get("/threads/search", {"query": "esports", "start_date": None})
    assert env["credits_remaining"] == 940
    assert env["data"]["results"] == [{"id": "x"}]
    assert "query=esports" in captured["url"]
    assert "start_date" not in captured["url"]   # None params dropped
    assert captured["key"] == "sc_test"


def test_sc_get_missing_key_raises(monkeypatch):
    monkeypatch.delenv("SOCIALCRAWL_API_KEY", raising=False)
    with pytest.raises(socialcrawl.SocialCrawlError, match="SOCIALCRAWL_API_KEY"):
        socialcrawl._sc_get("/threads/search", {"query": "x"})


def test_sc_get_unsuccessful_envelope_raises(monkeypatch):
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"success": false, "error": "bad query"}'
    monkeypatch.setattr(socialcrawl.urllib.request, "urlopen",
                        lambda req, timeout=None, context=None: FakeResp())
    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "sc_test")
    with pytest.raises(socialcrawl.SocialCrawlError, match="api error"):
        socialcrawl._sc_get("/threads/search", {"query": "x"})
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k sc_get -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'socialcrawl'`.

- [ ] **Step 3: Create `socialcrawl.py` with the client**

```python
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
    url = f"{SC_BASE}{path}?{qs}"
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k sc_get -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add socialcrawl.py tests/test_engine.py
git commit -m "feat: add SocialCrawl _sc_get client with auth + envelope parse"
```

---

## Task 2: Normalizers + search adapters + capability map

**Files:**
- Modify: `socialcrawl.py`
- Create: `tests/fixtures/threads_search.sample.json`, `tests/fixtures/tiktok_search.sample.json`, `tests/fixtures/reddit_search.sample.json`
- Test: `tests/test_engine.py` (append)

> If Task 0 found different real shapes, adjust the three fixtures **and** the matching `normalize_*` getters together so the tests still encode the real API.

- [ ] **Step 1: Create the three fixtures**

`tests/fixtures/threads_search.sample.json`:
```json
{
  "success": true,
  "platform": "threads",
  "data": {
    "results": [
      {
        "id": "th_1",
        "url": "https://www.threads.net/@gamer/post/1",
        "text": "huge esports drama unfolding right now",
        "author": {"username": "gamer", "followers": 12000},
        "published_on": "2026-06-13T02:00:00+00:00",
        "likes": 820, "replies": 140, "reposts": 260, "views": 50000
      }
    ]
  },
  "credits_remaining": 941
}
```

`tests/fixtures/tiktok_search.sample.json`:
```json
{
  "success": true,
  "platform": "tiktok",
  "data": {
    "results": [
      {
        "id": "tt_1",
        "url": "https://www.tiktok.com/@creator/video/1",
        "description": "esports meltdown caught on stream",
        "author": {"unique_id": "creator", "follower_count": 900000},
        "create_time": 1749780000,
        "stats": {"play_count": 1500000, "digg_count": 120000,
                  "comment_count": 8000, "share_count": 30000}
      }
    ]
  },
  "credits_remaining": 940
}
```

`tests/fixtures/reddit_search.sample.json`:
```json
{
  "success": true,
  "platform": "reddit",
  "data": {
    "results": [
      {
        "id": "rd_1",
        "url": "https://www.reddit.com/r/esports/comments/1",
        "title": "Esports org implodes",
        "selftext": "full breakdown of the drama",
        "author": "redditor",
        "subreddit": "esports",
        "created_utc": 1749780000,
        "score": 2400,
        "num_comments": 540
      }
    ]
  },
  "credits_remaining": 939
}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_engine.py`:
```python
def _sc_fixture(name):
    return _json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_normalize_threads_maps_unified_fields():
    item = _sc_fixture("threads_search.sample.json")["data"]["results"][0]
    rec = socialcrawl.normalize_threads(item)
    assert rec["post_id"] == "th_1"
    assert rec["author"] == {"handle": "gamer", "followers": 12000}
    assert rec["likes"] == 820 and rec["comments"] == 140 and rec["shares"] == 260
    assert rec["created"] == "2026-06-13T02:00:00+00:00"


def test_normalize_tiktok_maps_stats_and_epoch():
    item = _sc_fixture("tiktok_search.sample.json")["data"]["results"][0]
    rec = socialcrawl.normalize_tiktok(item)
    assert rec["likes"] == 120000 and rec["comments"] == 8000 and rec["shares"] == 30000
    assert rec["views"] == 1500000 and rec["reach"] == 1500000
    assert rec["created"].startswith("2025-")  # epoch 1749780000 -> ISO UTC


def test_normalize_reddit_maps_score_and_joins_text():
    item = _sc_fixture("reddit_search.sample.json")["data"]["results"][0]
    rec = socialcrawl.normalize_reddit(item)
    assert rec["likes"] == 2400 and rec["comments"] == 540 and rec["shares"] == 0
    assert rec["text"] == "Esports org implodes full breakdown of the drama"
    assert rec["author"] == {"handle": "redditor", "followers": 0}


def test_search_adapter_returns_records_and_credits(monkeypatch):
    monkeypatch.setattr(socialcrawl, "_sc_get",
                        lambda path, params: _sc_fixture("reddit_search.sample.json"))
    records, credits = socialcrawl.search_reddit("esports", "24h")
    assert credits == 939
    assert len(records) == 1 and records[0]["post_id"] == "rd_1"


def test_search_adapters_capability_map_keys():
    assert set(socialcrawl.SEARCH_ADAPTERS) == {"threads", "tiktok", "reddit"}
```

- [ ] **Step 3: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "normalize or search_adapter or capability" -v`
Expected: FAIL — `AttributeError: module 'socialcrawl' has no attribute 'normalize_threads'`.

- [ ] **Step 4: Add normalizers, adapters, and the map**

Append to `socialcrawl.py`:
```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "normalize or search_adapter or capability" -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add socialcrawl.py tests/fixtures/threads_search.sample.json tests/fixtures/tiktok_search.sample.json tests/fixtures/reddit_search.sample.json tests/test_engine.py
git commit -m "feat: add per-platform search adapters, normalizers, capability map"
```

---

## Task 3: Scoring primitives — `topic_query` + `raw_engagement`

**Files:**
- Modify: `engine.py` (add functions; add `import socialcrawl` near the top imports)
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_topic_query_prefers_query_then_label():
    assert engine.topic_query({"label": "Art", "query": "digital art"}) == "digital art"
    assert engine.topic_query({"label": "Esports"}) == "Esports"


def test_raw_engagement_weights_comments_and_shares():
    rec = {"likes": 100, "comments": 10, "shares": 5, "reach": 1000}
    w = {"w_like": 1, "w_comment": 2, "w_share": 2, "w_reach": 1}
    # 100*1 + 10*2 + 5*2 + 1000*1 = 1130
    assert engine.raw_engagement(rec, w) == 1130
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "topic_query or raw_engagement" -v`
Expected: FAIL — `AttributeError: module 'engine' has no attribute 'topic_query'`.

- [ ] **Step 3: Add `import socialcrawl` and the two functions**

At the top of `engine.py`, add to the imports (after `from pathlib import Path`):
```python
import copy
import socialcrawl
from datetime import time as _time
from zoneinfo import ZoneInfo
```

Add near the other small helpers (e.g. after `gen_id`):
```python
def topic_query(topic):
    """The string sent to SocialCrawl for a topic: its `query`, else its label."""
    return topic.get("query") or topic.get("label", "")


def raw_engagement(record, weights):
    """Weighted raw engagement for one unified record."""
    return (weights["w_like"] * record.get("likes", 0)
            + weights["w_comment"] * record.get("comments", 0)
            + weights["w_share"] * record.get("shares", 0)
            + weights["w_reach"] * record.get("reach", 0))
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "topic_query or raw_engagement" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add topic_query and raw_engagement scoring primitives"
```

---

## Task 4: Scoring primitives — baseline, magnitude, velocity, recency, trend_score

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_platform_baseline_is_median_with_guard():
    assert engine.platform_baseline([10, 20, 30]) == 20
    assert engine.platform_baseline([10, 20, 30, 40]) == 25
    assert engine.platform_baseline([]) == 1.0       # empty guard
    assert engine.platform_baseline([0, 0]) == 1.0    # zero-median guard


def test_magnitude_divides_by_baseline():
    assert engine.magnitude(100, 20) == 5.0
    assert engine.magnitude(100, 0) == 100            # baseline 0 -> raw


def test_velocity_is_clamped_nonnegative_rate():
    assert engine.velocity(300, 100, 2.0) == 100.0    # (300-100)/2
    assert engine.velocity(50, 100, 2.0) == 0.0       # falling -> 0
    assert engine.velocity(300, 100, 0) == 0.0        # no elapsed time -> 0


def test_recency_decays_with_age():
    fresh = engine.recency(0, 1.5)
    old = engine.recency(48, 1.5)
    assert fresh > old > 0


def test_trend_score_blends_magnitude_and_velocity():
    # (0.6*10 + 0.4*5) * 1.0 = 8.0
    assert engine.trend_score(10, 5, 0.6, 1.0) == 8.0
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "platform_baseline or magnitude or velocity or recency or trend_score" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement the functions**

Add to `engine.py`:
```python
def platform_baseline(raws):
    """Median of a platform's raw-engagement batch; never returns 0."""
    vals = sorted(v for v in raws if v is not None)
    if not vals:
        return 1.0
    n = len(vals)
    mid = n // 2
    med = vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2
    return med or 1.0


def magnitude(raw, baseline):
    return raw / baseline if baseline else raw


def velocity(raw_now, last_raw, dhours):
    """Non-negative engagement-gain rate since the last sighting."""
    if not dhours or dhours <= 0:
        return 0.0
    return max(0.0, (raw_now - last_raw) / dhours)


def recency(age_hours, gravity):
    return 1.0 / (age_hours + 2.0) ** gravity


def trend_score(magnitude_val, velocity_norm, beta, recency_factor):
    return (beta * magnitude_val + (1 - beta) * velocity_norm) * recency_factor
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "platform_baseline or magnitude or velocity or recency or trend_score" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add baseline/magnitude/velocity/recency/trend_score functions"
```

---

## Task 5: Floor filter + age helper

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone


def test_passes_floor_or_semantics():
    floor = {"views": 100000, "likes": 10000}
    assert engine.passes_floor({"views": 150000, "likes": 0}, floor) is True   # views clears
    assert engine.passes_floor({"views": 0, "likes": 12000}, floor) is True    # likes clears
    assert engine.passes_floor({"views": 5, "likes": 5}, floor) is False
    assert engine.passes_floor({"likes": 1}, {}) is True                        # no floor -> pass


def test_hours_since_parses_iso_and_z():
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    assert engine._hours_since("2026-06-13T10:00:00+00:00", now) == 2.0
    assert engine._hours_since("2026-06-13T10:00:00Z", now) == 2.0
    assert engine._hours_since(None, now) == 0.0      # missing -> 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "passes_floor or hours_since" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add to `engine.py`:
```python
def passes_floor(record, floor):
    """True if the record meets/exceeds ANY configured floor metric (OR semantics)."""
    if not floor:
        return True
    return any(record.get(metric, 0) >= threshold for metric, threshold in floor.items())


def _hours_since(iso_str, now):
    """Hours between an ISO timestamp and `now` (>= 0). 0 if unparseable/missing."""
    if not iso_str:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 3600.0)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "passes_floor or hours_since" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add passes_floor filter and _hours_since age helper"
```

---

## Task 6: State store IO + age-out

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_load_state_missing_returns_empty(tmp_path):
    assert engine.load_state(tmp_path / "nope.json") == {"posts": {}}


def test_load_state_corrupt_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert engine.load_state(p) == {"posts": {}}


def test_update_state_inserts_then_tracks_peak():
    state = {"posts": {}}
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    e1 = engine.update_state(state, "tiktok:1", 500, now, 0.4, "esports")
    assert e1["first_seen"] == now.isoformat() and e1["last_raw"] == 500
    later = datetime(2026, 6, 13, 13, 0, tzinfo=timezone.utc)
    e2 = engine.update_state(state, "tiktok:1", 900, later, 0.2, "esports")
    assert e2["first_seen"] == now.isoformat()       # unchanged
    assert e2["last_raw"] == 900
    assert e2["peak_score"] == 0.4                    # max kept


def test_age_out_state_drops_stale_entries():
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    state = {"posts": {
        "tiktok:fresh": {"last_seen": "2026-06-13T11:00:00+00:00"},
        "tiktok:stale": {"last_seen": "2026-06-11T11:00:00+00:00"},
    }}
    engine.age_out_state(state, now, max_age_hours=24)
    assert "tiktok:fresh" in state["posts"]
    assert "tiktok:stale" not in state["posts"]


def test_save_then_load_state_roundtrips(tmp_path):
    p = tmp_path / "state.json"
    engine.save_state(p, {"posts": {"x:1": {"last_raw": 5}}})
    assert engine.load_state(p)["posts"]["x:1"]["last_raw"] == 5
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "state" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add to `engine.py`:
```python
def load_state(path):
    """Load the poll state store; empty/corrupt -> a fresh empty store."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"posts": {}}
    data.setdefault("posts", {})
    return data


def save_state(path, state):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    tmp.replace(path)


def update_state(state, key, raw, now, score, topic):
    """Insert or refresh a tracked post; keep first_seen and peak_score."""
    posts = state.setdefault("posts", {})
    nowiso = now.isoformat()
    entry = posts.get(key)
    if entry is None:
        entry = {"first_seen": nowiso, "topic": topic, "peak_score": score}
        posts[key] = entry
    entry["last_seen"] = nowiso
    entry["last_raw"] = raw
    entry["peak_score"] = max(entry.get("peak_score", 0.0), score)
    return entry


def age_out_state(state, now, max_age_hours=24):
    posts = state.get("posts", {})
    for key in [k for k, e in posts.items()
                if _hours_since(e.get("last_seen"), now) > max_age_hours]:
        del posts[key]
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "state" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add poll state store IO, update, and age-out"
```

---

## Task 7: Poll config + window gate

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_poll_config_installs_defaults():
    data = {"modes": {}}
    pc = engine.poll_config(data)
    assert pc["interval_minutes"] == 60
    assert pc["top_n_per_platform_topic"] == 3
    assert pc["window"]["tz"] == "Asia/Ho_Chi_Minh"
    assert data["poll"] is pc                       # installed onto data
    # defaults are independent copies, not the shared module constant
    pc["interval_minutes"] = 5
    assert engine.DEFAULT_POLL["interval_minutes"] == 60


def test_in_window_respects_local_time():
    win = {"start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh"}  # UTC+7
    # 02:00 UTC == 09:00 ICT -> inside
    assert engine.in_window(datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc), win) is True
    # 14:00 UTC == 21:00 ICT -> outside
    assert engine.in_window(datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc), win) is False


def test_poll_gate_blocks_disabled_and_no_mode():
    now = datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc)   # 09:00 ICT, inside window
    data = {"modes": {}, "poll": {"enabled": False,
            "window": {"start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh"}}}
    assert engine.poll_gate(data, now)["reason"] == "disabled"
    data["poll"]["enabled"] = True
    data["current_active_mode"] = None
    assert engine.poll_gate(data, now)["reason"] == "no active mode"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "poll_config or in_window or poll_gate" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add to `engine.py`:
```python
DEFAULT_POLL = {
    "enabled": True,
    "interval_minutes": 60,
    "window": {"start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh"},
    "lookback": "24h",
    "top_n_per_platform_topic": 3,
    "score": {"w_like": 1, "w_comment": 2, "w_share": 2, "w_reach": 1,
              "beta": 0.6, "gravity": 1.5},
    "floors": {"tiktok": {"views": 100000, "likes": 10000},
               "reddit": {"likes": 500},
               "threads": {"likes": 500}},
}


def poll_config(data):
    if "poll" not in data:
        data["poll"] = copy.deepcopy(DEFAULT_POLL)
    return data["poll"]


def _parse_hhmm(s):
    h, m = s.split(":")
    return _time(int(h), int(m))


def in_window(now, window):
    tz = ZoneInfo(window.get("tz", "UTC"))
    local = now.astimezone(tz).time()
    return _parse_hhmm(window["start"]) <= local <= _parse_hhmm(window["end"])


def poll_gate(data, now):
    """Return a skip dict if polling should not run now, else None."""
    pcfg = poll_config(data)
    if not pcfg.get("enabled", True):
        return {"skipped": True, "reason": "disabled"}
    if not in_window(now, pcfg["window"]):
        return {"skipped": True, "reason": "outside window"}
    if not data.get("modes", {}).get(data.get("current_active_mode")):
        return {"skipped": True, "reason": "no active mode"}
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "poll_config or in_window or poll_gate" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add poll config defaults, window gate, and poll_gate"
```

---

## Task 8: `run_poll` orchestrator

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def _poll_data():
    return {
        "current_active_mode": "culture_drama",
        "modes": {"culture_drama": {
            "name": "Drama & Cultural Pulse", "icon": "🎭",
            "platforms": ["tiktok", "reddit"],
            "topics": {"esports": {"label": "Esports", "query": "esports", "active": True},
                       "music": {"label": "Music", "query": "music", "active": False}},
        }},
        "poll": copy.deepcopy(engine.DEFAULT_POLL),
    }


def _now_inside():
    return datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc)  # 09:00 ICT


def test_run_poll_skips_outside_window(tmp_path):
    data = _poll_data()
    out = engine.run_poll(
        data, now=datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc),
        search_fn=lambda *a: ([], 100), capable_platforms={"tiktok", "reddit"},
        state={"posts": {}}, log_path=tmp_path / "log.jsonl")
    assert out["skipped"] is True and out["reason"] == "outside window"


def test_run_poll_logs_top_n_per_platform_and_applies_floor(tmp_path):
    data = _poll_data()
    data["poll"]["top_n_per_platform_topic"] = 1
    # tiktok: one post clears the 100k-views floor, one does not
    tiktok = [
        {"post_id": "tt_big", "url": "u", "text": "t", "author": {"handle": "a", "followers": 1},
         "created": "2026-06-13T01:00:00+00:00", "likes": 50000, "comments": 9000,
         "shares": 9000, "views": 2000000, "reach": 2000000},
        {"post_id": "tt_small", "url": "u", "text": "t", "author": {"handle": "b", "followers": 1},
         "created": "2026-06-13T01:00:00+00:00", "likes": 1, "comments": 1,
         "shares": 1, "views": 10, "reach": 10},
    ]
    reddit = [
        {"post_id": "rd_1", "url": "u", "text": "t", "author": {"handle": "c", "followers": 0},
         "created": "2026-06-13T01:00:00+00:00", "likes": 3000, "comments": 800,
         "shares": 0, "views": 0, "reach": 0},
    ]

    def search_fn(platform, query, lookback):
        return ({"tiktok": tiktok, "reddit": reddit}[platform], 500)

    log = tmp_path / "log.jsonl"
    out = engine.run_poll(data, now=_now_inside(), search_fn=search_fn,
                          capable_platforms={"tiktok", "reddit"},
                          state={"posts": {}}, log_path=log)
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    ids = {l["post_id"] for l in lines}
    assert ids == {"tt_big", "rd_1"}          # tt_small filtered by floor; top-1 each platform
    assert all(l["topic"] == "esports" for l in lines)   # music inactive, not polled
    assert out["logged"] == 2 and out["polled"] == 2     # 1 topic x 2 platforms


def test_run_poll_continues_when_one_platform_fails(tmp_path):
    data = _poll_data()
    reddit = [{"post_id": "rd_1", "url": "u", "text": "t",
               "author": {"handle": "c", "followers": 0},
               "created": "2026-06-13T01:00:00+00:00", "likes": 3000,
               "comments": 800, "shares": 0, "views": 0, "reach": 0}]

    def search_fn(platform, query, lookback):
        if platform == "tiktok":
            raise socialcrawl.SocialCrawlError("boom")
        return (reddit, 500)

    log = tmp_path / "log.jsonl"
    out = engine.run_poll(data, now=_now_inside(), search_fn=search_fn,
                          capable_platforms={"tiktok", "reddit"},
                          state={"posts": {}}, log_path=log)
    assert any("tiktok" in m for m in out["markers"])
    assert out["logged"] == 1                  # reddit still logged


def test_run_poll_computes_velocity_from_state(tmp_path):
    data = _poll_data()
    data["modes"]["culture_drama"]["platforms"] = ["reddit"]
    prev = datetime(2026, 6, 13, 1, 0, tzinfo=timezone.utc)
    state = {"posts": {"reddit:rd_1": {"first_seen": prev.isoformat(),
             "last_seen": prev.isoformat(), "last_raw": 100.0, "peak_score": 0.1,
             "topic": "esports"}}}
    reddit = [{"post_id": "rd_1", "url": "u", "text": "t",
               "author": {"handle": "c", "followers": 0},
               "created": "2026-06-13T01:00:00+00:00", "likes": 3000,
               "comments": 800, "shares": 0, "views": 0, "reach": 0}]
    log = tmp_path / "log.jsonl"
    engine.run_poll(data, now=_now_inside(), search_fn=lambda *a: (reddit, 500),
                    capable_platforms={"reddit"}, state=state, log_path=log)
    rec = json.loads(log.read_text().splitlines()[0])
    assert rec["velocity"] > 0                 # raw grew vs last_raw over 1h
    assert rec["hours_trending"] == 1.0        # first_seen 1h before now
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "run_poll" -v`
Expected: FAIL — `AttributeError: module 'engine' has no attribute 'run_poll'`.

- [ ] **Step 3: Implement `run_poll`**

Add to `engine.py`:
```python
def run_poll(data, *, now, search_fn, capable_platforms, state, log_path,
             low_credit_threshold=0):
    """One poll tick. Searches active topics x searchable platforms, scores,
    floors, caps top-N per (topic x platform), re-logs to JSONL. Never raises
    on a single platform failure."""
    gate = poll_gate(data, now)
    if gate:
        return gate
    pcfg = poll_config(data)
    mode = data["modes"][data["current_active_mode"]]
    platforms = [p for p in mode.get("platforms", []) if p in capable_platforms]
    active_topics = {tid: t for tid, t in mode.get("topics", {}).items() if t.get("active")}
    if not platforms or not active_topics:
        return {"skipped": True, "reason": "nothing to poll"}

    score_cfg, floors = pcfg["score"], pcfg["floors"]
    top_n, lookback = pcfg["top_n_per_platform_topic"], pcfg["lookback"]
    state.setdefault("posts", {})
    log_lines, markers = [], []
    polled = found = logged = 0
    credits_remaining = None

    for tid, topic in active_topics.items():
        query = topic_query(topic)
        for platform in platforms:
            if credits_remaining is not None and credits_remaining <= low_credit_threshold:
                markers.append("low credits")
                break
            polled += 1
            try:
                records, credits_remaining = search_fn(platform, query, lookback)
            except Exception as e:  # SocialCrawlError or any adapter failure
                markers.append(f"{platform} fetch failed: {e}")
                continue
            found += len(records)
            eligible = [r for r in records if passes_floor(r, floors.get(platform, {}))]
            if not eligible:
                continue
            baseline = platform_baseline([raw_engagement(r, score_cfg) for r in eligible])
            scored = []
            for r in eligible:
                raw = raw_engagement(r, score_cfg)
                key = f"{platform}:{r['post_id']}"
                prev = state["posts"].get(key)
                dhours = _hours_since(prev["last_seen"], now) if prev else 0.0
                vel = velocity(raw, prev["last_raw"], dhours) if prev else 0.0
                mag = magnitude(raw, baseline)
                vel_norm = vel / baseline if baseline else 0.0
                age_h = _hours_since(r.get("created"), now)
                sc = trend_score(mag, vel_norm, score_cfg["beta"],
                                 recency(age_h, score_cfg["gravity"]))
                scored.append((sc, raw, mag, vel, r, key))
            scored.sort(key=lambda x: x[0], reverse=True)
            for rank, (sc, raw, mag, vel, r, key) in enumerate(scored[:top_n], 1):
                entry = update_state(state, key, raw, now, sc, tid)
                log_lines.append({
                    "ts": now.isoformat(), "topic": tid, "platform": platform,
                    "post_id": r["post_id"], "url": r.get("url"), "text": r.get("text", ""),
                    "author": r.get("author", {}), "created": r.get("created"),
                    "likes": r.get("likes", 0), "comments": r.get("comments", 0),
                    "shares": r.get("shares", 0), "reach": r.get("reach", 0),
                    "magnitude": round(mag, 4), "velocity": round(vel, 4),
                    "score": round(sc, 4), "rank": rank,
                    "hours_trending": round(_hours_since(entry["first_seen"], now), 2),
                })
                logged += 1

    age_out_state(state, now)
    if log_lines or markers:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for line in log_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            for m in markers:
                f.write(json.dumps({"ts": now.isoformat(), "marker": m},
                                   ensure_ascii=False) + "\n")
    return {"polled": polled, "found": found, "logged": logged,
            "credits_remaining": credits_remaining, "markers": markers}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "run_poll" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add run_poll orchestrator (search, score, floor, top-N, log)"
```

---

## Task 9: `poll` CLI command + lockfile + state/log wiring

**Files:**
- Modify: `engine.py` (add `cli_poll`, `_poll_log_path`, `_state_path`, `_poll_lock_path`; add `poll` to the argparse `choices`; add the `main()` branch)
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_poll_skips_when_disabled(cfg, monkeypatch):
    # disable polling in the live config, then run the CLI: no network, rc 0
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = False
    engine.save_config(cfg, data)
    rc, out = run_cli(cfg, "poll")
    assert rc == 0
    assert out["skipped"] is True and out["reason"] == "disabled"


def test_cli_poll_missing_key_errors_when_work_due(cfg, monkeypatch):
    data = engine.load_config(cfg)
    pc = engine.poll_config(data)
    pc["enabled"] = True
    pc["window"] = {"start": "00:00", "end": "23:59", "tz": "UTC"}  # always inside
    engine.save_config(cfg, data)
    env = dict(os.environ); env.pop("SOCIALCRAWL_API_KEY", None)
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "engine.py"), "poll", "--file", str(cfg)],
        capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    assert "SOCIALCRAWL_API_KEY" in json.loads(proc.stdout)["error"]


def test_cli_poll_lock_blocks_second_run(cfg, monkeypatch):
    data = engine.load_config(cfg)
    pc = engine.poll_config(data)
    pc["window"] = {"start": "00:00", "end": "23:59", "tz": "UTC"}
    engine.save_config(cfg, data)
    lock = engine._poll_lock_path()
    lock.write_text("999999")
    try:
        rc, out = run_cli(cfg, "poll")
        assert rc == 0 and out["reason"] == "locked"
    finally:
        lock.unlink(missing_ok=True)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "cli_poll" -v`
Expected: FAIL — argparse rejects `poll` / `AttributeError: _poll_lock_path`.

- [ ] **Step 3: Implement the CLI wiring**

Add to `engine.py`:
```python
def _poll_log_path():
    env = os.environ.get("EPAPHRAS_POLL_LOG")
    return Path(env) if env else Path(__file__).parent / "trending_posts.jsonl"


def _state_path():
    return Path(__file__).parent / "poll_state.json"


def _poll_lock_path():
    return Path(__file__).parent / "poll.lock"


def cli_poll(data):
    """Drive run_poll with real adapters, state store, log, and a lockfile."""
    now = datetime.now(timezone.utc)
    gate = poll_gate(data, now)
    if gate:
        return gate
    if not os.environ.get("SOCIALCRAWL_API_KEY"):
        return {"error": "SOCIALCRAWL_API_KEY not set"}
    lock = _poll_lock_path()
    if lock.exists():
        return {"skipped": True, "reason": "locked"}
    lock.write_text(str(os.getpid()))
    try:
        state = load_state(_state_path())
        summary = run_poll(
            data, now=now,
            search_fn=lambda platform, q, lb: socialcrawl.SEARCH_ADAPTERS[platform](q, lb),
            capable_platforms=set(socialcrawl.SEARCH_ADAPTERS),
            state=state, log_path=_poll_log_path())
        save_state(_state_path(), state)
        return summary
    finally:
        lock.unlink(missing_ok=True)
```

In `main()`, add `"poll"` to the `choices=[...]` list, and add this branch (after the `get-msgid` branch, before the `except`):
```python
        elif args.command == "poll":
            out = cli_poll(data)
            _emit(out)
            return 1 if "error" in out else 0
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "cli_poll" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add poll CLI command with lockfile and state/log wiring"
```

---

## Task 10: Repurpose the panel toggle — `📡 Polling` On/Off

**Files:**
- Modify: `engine.py` (`render_modes` button; `toggle_notifications`→`toggle_polling`; `handle_callback` `cb_notif` route)
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_render_modes_shows_polling_off_by_default(cfg):
    data = engine.load_config(cfg)
    flat = [b for row in engine.render_modes(data)["buttons"] for b in row]
    poll_btn = next(b for b in flat if b["callback_data"] == "cb_notif")
    assert "📡" in poll_btn["text"] and "Off" in poll_btn["text"]


def test_render_modes_shows_polling_on_when_enabled(cfg):
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = True
    flat = [b for row in engine.render_modes(data)["buttons"] for b in row]
    poll_btn = next(b for b in flat if b["callback_data"] == "cb_notif")
    assert "On" in poll_btn["text"]


def test_cb_notif_toggles_poll_enabled(cfg):
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = False
    engine.handle_callback(data, "cb_notif")
    assert engine.poll_config(data)["enabled"] is True
    engine.handle_callback(data, "cb_notif")
    assert engine.poll_config(data)["enabled"] is False
```

(Note: the default `poll.enabled` is `True`, but these tests set it explicitly first, so they pass regardless of the default.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "polling or cb_notif_toggles" -v`
Expected: FAIL — the button still reads `🔔 Notifications`; `cb_notif` still calls webhook code.

- [ ] **Step 3: Update `render_modes` and the toggle**

In `engine.py`, in `render_modes`, replace the notifications row:
```python
    on = poll_config(data).get("enabled", True)
    rows.append([{"text": f"📡 Polling: {'On' if on else 'Off'}",
                  "callback_data": "cb_notif"}])
```

Replace `toggle_notifications` with:
```python
def toggle_polling(data):
    pc = poll_config(data)
    pc["enabled"] = not pc.get("enabled", True)
    return render_modes(data)
```

In `handle_callback`, change the `cb_notif` route:
```python
    if cb == "cb_notif":
        return toggle_polling(data)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "polling or cb_notif_toggles" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: repurpose panel toggle to Polling On/Off (poll.enabled)"
```

---

## Task 11: Capability-map platform picker (replace zernio accounts)

**Files:**
- Modify: `engine.py` (`render_platforms`, `pick_platform`, `submit_name`, `create_mode`)
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def _picking_data(name="My Mode"):
    return {"current_active_mode": "x", "modes": {},
            "wizard": {"step": "pick_platforms", "draft": {"name": name, "platforms": []}}}


def test_render_platforms_lists_capability_map():
    data = _picking_data()
    flat = [b for row in engine.render_platforms(data)["buttons"] for b in row]
    cbs = {b["callback_data"] for b in flat}
    assert "cb_pickplat:threads" in cbs
    assert "cb_pickplat:tiktok" in cbs
    assert "cb_pickplat:reddit" in cbs
    assert "cb_createmode" in cbs
    assert flat[-1]["callback_data"] == "cb_cancel"


def test_pick_platform_toggles_string_names():
    data = _picking_data()
    engine.pick_platform(data, "tiktok")
    engine.pick_platform(data, "reddit")
    assert data["wizard"]["draft"]["platforms"] == ["tiktok", "reddit"]
    engine.pick_platform(data, "tiktok")            # toggle off
    assert data["wizard"]["draft"]["platforms"] == ["reddit"]


def test_pick_platform_rejects_uncapable():
    data = _picking_data()
    with pytest.raises(engine.ConfigError):
        engine.pick_platform(data, "facebook")


def test_create_mode_stores_string_platforms_and_activates():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Crypto Watch")
    engine.pick_platform(data, "reddit")
    engine.create_mode(data)
    new_id = data["current_active_mode"]
    assert new_id == "crypto_watch"
    assert data["modes"][new_id]["platforms"] == ["reddit"]
    assert data["modes"][new_id]["topics"] == {}


def test_create_mode_requires_a_platform():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Empty")
    engine.create_mode(data)                         # none picked
    assert data["wizard"]["step"] == "pick_platforms"
    assert "Empty" not in [m.get("name") for m in data["modes"].values()]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "render_platforms_lists_capability or pick_platform_toggles_string or pick_platform_rejects or create_mode_stores_string or create_mode_requires_a_platform" -v`
Expected: FAIL — current picker fetches zernio accounts and stores objects.

- [ ] **Step 3: Rewrite the picker functions**

In `engine.py`, replace `render_platforms`, `pick_platform`, `submit_name`, and `create_mode` with:
```python
def render_platforms(data):
    wiz = get_wizard(data)
    draft = wiz.get("draft", {})
    selected = set(draft.get("platforms", []))
    rows = []
    for name in sorted(socialcrawl.SEARCH_ADAPTERS):
        mark = "✅" if name in selected else "⬜"
        emoji = PLATFORM_EMOJI.get(name, "🌐")
        rows.append([{"text": f"{mark} {emoji} {name}",
                      "callback_data": f"cb_pickplat:{name}"}])
    rows.append([{"text": f"✅ Create ({len(selected)})", "callback_data": "cb_createmode"}])
    rows.append([{"text": "✖ Cancel", "callback_data": "cb_cancel"}])
    text = f"New mode: {draft.get('name', '?')}\nPick searchable platforms:"
    return {"text": text, "buttons": rows, "inline_keyboard": rows}


def pick_platform(data, platform):
    wiz = get_wizard(data)
    if wiz.get("step") != "pick_platforms":
        raise ConfigError("not picking platforms")
    if platform not in socialcrawl.SEARCH_ADAPTERS:
        raise ConfigError(f"platform not searchable: {platform}")
    plats = wiz.setdefault("draft", {}).setdefault("platforms", [])
    if platform in plats:
        plats.remove(platform)
    else:
        plats.append(platform)


def submit_name(data, text):
    wiz = get_wizard(data)
    name = text.strip()
    if not (1 <= len(name) <= 40):
        rows = [[{"text": "✖ Cancel", "callback_data": "cb_cancel"}]]
        return {"text": "⚠️ Name must be 1–40 characters.\nSend a name for the new mode:",
                "buttons": rows, "inline_keyboard": rows}
    wiz["draft"] = {"name": name, "platforms": []}
    wiz["step"] = "pick_platforms"
    return render_platforms(data)


def create_mode(data):
    wiz = get_wizard(data)
    draft = wiz.get("draft", {})
    plats = draft.get("platforms", [])
    if not plats:
        return render_platforms(data)  # need at least one platform; stay on picker
    mode_id = gen_id(set(data["modes"].keys()), _slugify(draft["name"]))
    data["modes"][mode_id] = {
        "name": draft["name"], "icon": DEFAULT_ICON,
        "platforms": list(plats), "topics": {},
    }
    data["current_active_mode"] = mode_id
    reset_wizard(data)
    return render_topics(data, mode_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "render_platforms_lists_capability or pick_platform_toggles_string or pick_platform_rejects or create_mode_stores_string or create_mode_requires_a_platform" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: replace zernio account picker with capability-map platform picker"
```

---

## Task 12: Remove all webhook/zernio code from `engine.py`

**Files:**
- Modify: `engine.py`

- [ ] **Step 1: Delete the zernio/webhook functions and constants**

Remove these symbols from `engine.py` entirely:
- MCP client: `DEFAULT_MCP_GATEWAY_URL`, `DEFAULT_PUBLIC_URL`, `_mcp_call`, `_get_accounts_payload`
- Accounts picker: `fetch_accounts`, `_ensure_accounts`
- Webhook constants: `WEBHOOK_EVENTS`, `WEBHOOK_NAME`
- Webhook config/helpers: `webhook_config`, `_gen_secret`, `webhook_url`
- Matcher: `TEXT_KEYS`, `_gather_text`, `_event_platform`, `_topic_matches`, `match_event`, `_webhook_log_path`, `handle_webhook`
- Registration: `_list_webhooks`, `_find_webhook_by_url`, `_wh_id`, `_now_iso`, `enable_webhook`, `disable_webhook`, `sync_webhook`, `webhook_status`
- Sync hook: `_maybe_sync`, and the old `toggle_notifications` (already replaced in Task 10)

- [ ] **Step 2: Remove webhook CLI wiring from `main()`**

In `main()`:
- Remove `"webhook-status"`, `"webhook-enable"`, `"webhook-disable"`, `"webhook-sync"`, `"handle-webhook"` from `choices=[...]`.
- Delete their five `elif` branches.
- In the `handle-callback` branch, remove the `_maybe_sync(data)` call and the `if args.arg != "cb_notif":` guard around it (just `save_config(path, data)` after `handle_callback`).
- In the `handle-text` branch, remove the `_maybe_sync(data)` call (keep the `save_config` on `handled`).

- [ ] **Step 3: Remove now-unused imports**

If no remaining code uses them, delete `import ast`, `import secrets`, and `import ssl` from `engine.py`. (Keep `urllib`/`json`/`datetime` etc.) Verify with:
```bash
grep -nE '\b(ast|secrets|ssl)\.' engine.py
```
Expected: no matches → safe to remove those three imports.

- [ ] **Step 4: Verify the module imports cleanly**

Run: `python3 -c "import engine; print('ok')"`
Expected: `ok` (no NameError/ImportError).

- [ ] **Step 5: Commit**

```bash
git add engine.py
git commit -m "refactor: remove all zernio/webhook code from engine"
```

---

## Task 13: Remove webhook/zernio tests; fix the multi-mode fixture

**Files:**
- Modify: `tests/test_engine.py`
- Modify: `tests/fixtures/modes.sample.json`
- Delete: `tests/fixtures/accounts.sample.json`, `tests/fixtures/comment_received.sample.json`

- [ ] **Step 1: Delete obsolete tests and helpers**

In `tests/test_engine.py`, delete:
- The `WH_FIXTURE`, `ACCOUNTS_FIXTURE`, `_patch_payload` definitions.
- All `test_fetch_accounts_*`, `test_render_platforms_lists_accounts`, `test_render_platforms_network_error_shows_warning`, `test_pick_platform_toggles_and_caps_at_two`, `test_submit_name_advances_to_pick_platforms`, `test_create_mode_persists_and_activates`, `test_create_mode_requires_at_least_one_platform` (superseded by Task 11 tests), `test_handle_text_await_name_handled` (used `_patch_payload`).
- All `test_mcp_call_*`, `test_webhook_*`, `test_match_event_*`, `test_handle_webhook_*`, `test_enable_webhook_*`, `test_disable_webhook_*`, `test_sync_webhook_*`, `test_cli_handle_webhook`, `test_cli_webhook_status`, `test_maybe_sync_*`.
- The old `test_render_modes_shows_notifications_*` and `test_cb_notif_enables_then_disables` (superseded by Task 10).
- `test_default_template_uses_object_platforms_and_idle_wizard` (template changes in Task 14; replaced there).
- The `FakeGateway` class and the `gw` fixture.

- [ ] **Step 2: Rewrite `test_handle_text_await_name_handled` without accounts**

Add back a network-free version:
```python
def test_handle_text_await_name_handled():
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_text(data, "Crypto Watch")
    assert out["handled"] is True
    assert data["wizard"]["step"] == "pick_platforms"
    assert "buttons" in out and "inline_keyboard" in out
```

- [ ] **Step 3: Fix `render_modes` row-count assertions**

The fixture has 4 modes; the toggle button is now `📡 Polling`. Update `test_render_modes_marks_active` final assertions:
```python
    assert rows[-2][0]["callback_data"] == "cb_newmode"
    assert rows[-1][0]["callback_data"] == "cb_notif"   # now the 📡 Polling button
```
(Row count stays 6: 4 modes + New mode + Polling.) Similarly in `test_perform_delete_mode_removes_and_reassigns_active`, the last row is still `cb_notif` — no change needed there.

- [ ] **Step 4: Drop the `webhook` block from the fixture; add a `query`**

Edit `tests/fixtures/modes.sample.json`: there is no `webhook` block there today (it's in the template), so just add a `query` to one topic to exercise `topic_query`. Change the `esports` topic of `culture_drama` to:
```json
        "esports": { "label": "Esports Drama", "query": "esports", "active": true },
```

- [ ] **Step 5: Delete the obsolete fixtures**

```bash
git rm tests/fixtures/accounts.sample.json tests/fixtures/comment_received.sample.json
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — all green, no references to removed symbols.

- [ ] **Step 7: Commit**

```bash
git add tests/test_engine.py tests/fixtures/modes.sample.json
git commit -m "test: remove webhook/zernio tests; update fixtures for polling"
```

---

## Task 14: Reseed the production template + `.gitignore`

**Files:**
- Modify: `templates/modes.default.json`
- Modify: `.gitignore`
- Test: `tests/test_engine.py` (append a template-shape test)

- [ ] **Step 1: Write the failing template test**

```python
def test_default_template_is_single_mode_with_poll_block():
    tmpl = _json.loads(
        (Path(__file__).parent.parent / "templates" / "modes.default.json").read_text())
    assert list(tmpl["modes"]) == ["culture_drama"]
    mode = tmpl["modes"]["culture_drama"]
    assert mode["platforms"] == ["threads", "tiktok", "reddit"]
    assert set(mode["topics"]) == {"esports", "showbiz", "music", "art", "technology"}
    assert mode["topics"]["showbiz"]["query"] == "celebrity"
    assert mode["topics"]["art"]["active"] is False
    assert tmpl["poll"]["interval_minutes"] == 60
    assert tmpl["poll"]["window"]["tz"] == "Asia/Ho_Chi_Minh"
    assert "webhook" not in tmpl
    # the seeded template renders cleanly
    out = engine.render_topics(tmpl, "culture_drama")
    assert "Platforms:" in out["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "default_template_is_single_mode" -v`
Expected: FAIL — current template is multi-mode with object platforms + a `webhook` block.

- [ ] **Step 3: Overwrite the template**

Replace `templates/modes.default.json` with:
```json
{
  "current_active_mode": "culture_drama",
  "wizard": { "step": "idle" },
  "poll": {
    "enabled": true,
    "interval_minutes": 60,
    "window": { "start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh" },
    "lookback": "24h",
    "top_n_per_platform_topic": 3,
    "score": { "w_like": 1, "w_comment": 2, "w_share": 2, "w_reach": 1, "beta": 0.6, "gravity": 1.5 },
    "floors": {
      "tiktok": { "views": 100000, "likes": 10000 },
      "reddit": { "likes": 500 },
      "threads": { "likes": 500 }
    }
  },
  "modes": {
    "culture_drama": {
      "name": "Drama & Cultural Pulse",
      "icon": "🎭",
      "platforms": ["threads", "tiktok", "reddit"],
      "topics": {
        "esports":    { "label": "Esports",    "query": "esports",    "active": true },
        "showbiz":    { "label": "Showbiz",    "query": "celebrity",  "active": true },
        "music":      { "label": "Music",      "query": "music",      "active": true },
        "art":        { "label": "Art",        "query": "art",        "active": false },
        "technology": { "label": "Technology", "query": "technology", "active": true }
      }
    }
  }
}
```

- [ ] **Step 4: Update `.gitignore`**

In `.gitignore`, replace the line `webhook_events.jsonl` with:
```
trending_posts.jsonl
poll_state.json
poll.lock
```

- [ ] **Step 5: Run to verify pass + full suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (including the new template test).

- [ ] **Step 6: Commit**

```bash
git add templates/modes.default.json .gitignore tests/test_engine.py
git commit -m "feat: reseed template to single polling mode; ignore poll runtime files"
```

---

## Task 15: Swap the gateway patch — remove webhook receiver, add poll timer

**Files:**
- Modify: `scripts/full_patch_v2.py`

This patch runs inside the OpenClaw gateway and is not unit-testable; it is verified manually (Task 17). Keep the JS injected by the patch dead-thin: a timer that shells out to `engine.py poll`.

- [ ] **Step 1: Remove the webhook receiver patch**

In `scripts/full_patch_v2.py`, delete the block that mounts `POST /zernio/webhook` (the `_EPAPHRAS_WEBHOOK_V1` marker patch) and any helper that computes the HMAC / dedup set for it. Leave the Gemma tool-call and fast-callback patches intact.

- [ ] **Step 2: Add the poll-timer patch**

Add a new idempotent, marker-guarded patch (`_EPAPHRAS_POLL_V1`) that injects, once, into the gateway bootstrap a timer that shells out to the engine. Inject this JS (adjust the anchor to the same location the other patches use, and `SKILL_DIR` to the deployed skill path):
```js
(function _registerEpaphrasPoll() {
  if (globalThis.__epaphrasPollV1) return;          // idempotent
  globalThis.__epaphrasPollV1 = true;
  const { spawn } = require("child_process");
  const SKILL_DIR = process.env.EPAPHRAS_SKILL_DIR || "/app/skills/OpenClawModeSkills";
  const INTERVAL_MS = 60 * 60 * 1000;               // hourly; engine enforces the 08–20 window
  function tick() {
    const p = spawn("python3", [SKILL_DIR + "/engine.py", "poll"],
                    { cwd: SKILL_DIR, env: process.env });
    let out = "";
    p.stdout.on("data", (d) => (out += d));
    p.on("close", () => { try { console.log("[epaphras poll]", out.trim()); } catch (e) {} });
  }
  setInterval(tick, INTERVAL_MS);
})();
```

- [ ] **Step 3: Verify the patch script parses + is idempotent**

Run: `python3 -c "import ast; ast.parse(open('scripts/full_patch_v2.py').read()); print('ok')"`
Expected: `ok`. (Re-running the patch must not double-inject — the `_EPAPHRAS_POLL_V1` marker check guards it, same pattern as the other patches.)

- [ ] **Step 4: Commit**

```bash
git add scripts/full_patch_v2.py
git commit -m "feat: swap gateway webhook receiver for hourly poll timer patch"
```

---

## Task 16: Rewrite `SKILL.md` and `README.md`

**Files:**
- Modify: `SKILL.md`, `README.md`

- [ ] **Step 1: Update `SKILL.md`**

Replace the "Notifications (zernio webhook)" section, the webhook callback row, the webhook engine-commands list, and the zernio platform-picker notes with:
- A **Polling** section: `📡 Polling: On/Off` toggles `poll.enabled`; an hourly gateway timer (08:00–20:00 ICT) runs `engine.py poll`, which searches the active mode's active topics across its platforms via SocialCrawl, scores/filters, and appends to `trending_posts.jsonl`.
- The `poll` command in the engine command table.
- The wizard note: "pick ≤2 platforms (live from zernio)" → "pick searchable platforms (Threads/TikTok/Reddit)".
- Env vars: add `SOCIALCRAWL_API_KEY` (required); replace `EPAPHRAS_WEBHOOK_LOG` with `EPAPHRAS_POLL_LOG`; remove `EPAPHRAS_MCP_GATEWAY_URL` and `EPAPHRAS_PUBLIC_URL`.
- Update the `cb_notif` row meaning to "toggle polling on/off".

- [ ] **Step 2: Update `README.md`**

Replace the zernio/webhook install notes (steps 4–5) and the notifications paragraph with SocialCrawl + polling equivalents: set `SOCIALCRAWL_API_KEY`; the `📡 Polling` button enables an hourly windowed poll; discoveries land in `trending_posts.jsonl` (`EPAPHRAS_POLL_LOG`); Telegram push is not yet implemented.

- [ ] **Step 3: Verify no stale references remain**

Run: `grep -niE "zernio|webhook|EPAPHRAS_PUBLIC_URL|EPAPHRAS_MCP_GATEWAY_URL|EPAPHRAS_WEBHOOK_LOG" SKILL.md README.md`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add SKILL.md README.md
git commit -m "docs: rewrite SKILL/README for SocialCrawl interval polling"
```

---

## Task 17: Full regression + manual gateway verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS, no skips referencing removed code.

- [ ] **Step 2: Confirm no stale symbols remain in code**

Run: `grep -nE "_mcp_call|webhook|zernio|fetch_accounts|match_event" engine.py`
Expected: no matches.

- [ ] **Step 3: Smoke-test the poll CLI locally**

Run (real key, window forced open via a temp config or the seeded 08–20 window if currently inside):
```bash
SOCIALCRAWL_API_KEY=sc_xxx python3 engine.py poll
```
Expected: a JSON summary `{"polled": N, "found": …, "logged": …, "credits_remaining": …, "markers": []}`, and new lines in `trending_posts.jsonl`. Outside 08–20 ICT you'll instead see `{"skipped": true, "reason": "outside window"}`.

- [ ] **Step 4: Apply and verify the gateway patch (manual)**

Apply `scripts/full_patch_v2.py` to the live pod (per `docs/gateway-patch-notes.md` re-apply steps), restart the gateway, and confirm:
- the timer logs `[epaphras poll] …` hourly inside the window and skips outside it,
- `trending_posts.jsonl` accrues lines on the runtime,
- a second overlapping invocation logs `{"reason": "locked"}`,
- tapping `📡 Polling` in Telegram flips `poll.enabled` in `modes.json`.

- [ ] **Step 5: Commit (if any doc fixes were needed)**

```bash
git commit -am "chore: regression pass for SocialCrawl polling" --allow-empty
```

---

## Self-review

**Spec coverage:**
- §3 SocialCrawl full replacement → Tasks 1–2 (client), 12 (removal). ✓
- §3 per-platform search, capability map → Tasks 2, 11. ✓
- §4/§6 trend score (magnitude/velocity/recency), floors, top-N per (topic × platform) → Tasks 3–8. ✓
- §5 data model (`poll` block, `query` field, string platforms, state store, log line incl. `author`) → Tasks 6, 8, 14. ✓
- §5 UI toggle repurpose → Task 10. ✓
- §7 `socialcrawl.py` + `_sc_get` + adapters; `poll` command; env vars → Tasks 1–2, 9, 16. ✓
- §8 error handling (partial poll, credits, lockfile, missing key, corrupt state) → Tasks 8, 9, 6. ✓
- §9 test plan (scoring/adapters/orchestration unit; manual gateway) → Tasks 3–11, 17. ✓
- §10 cleanup → Tasks 12–13, 15–16. ✓
- §13 wave seam (log `author`, per-platform records) → Task 8 log line + Task 6 state keys. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; deletion tasks name exact symbols. ✓

**Type/name consistency:** `run_poll(data, *, now, search_fn, capable_platforms, state, log_path, low_credit_threshold)` used identically in Tasks 8 and 9; adapters return `(records, credits_remaining)` in Tasks 2, 8, 9; `poll_config`/`poll_gate`/`in_window`/`DEFAULT_POLL` consistent across Tasks 7–10; `cb_notif` retained as the callback id throughout (Tasks 10, 13). ✓

**Known unknown:** exact SocialCrawl response field paths — addressed by the Task 0 spike and isolated to the three fixtures + `normalize_*` getters, so a shape difference is a localized fixture/getter edit, not a structural change.
