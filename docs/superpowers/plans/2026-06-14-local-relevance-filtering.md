# Local Relevance & Spam Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each SocialCrawl poll tick return locally-relevant, readable, non-spam posts by adding a vi+en language filter, a TikTok `region=VN` proxy param, and a multi-signal spam drop-filter.

**Architecture:** Two filter predicates (`keep_language`, `is_spam`) live in `engine.py` beside the existing `passes_floor`; they run in `run_poll` immediately after each platform fetch, before scoring. The fetch-side `region` param lives in the `socialcrawl.py` adapters (uniform signature; only TikTok uses it). Language arrives pre-computed from the API as `item["computed"]["language"]`. Two config keys (`languages`, `tiktok_region`) are read with safe defaults so old configs keep working.

**Tech Stack:** Python 3 (stdlib only), pytest. Files: `socialcrawl.py`, `engine.py`, `templates/modes.default.json`, `tests/test_engine.py`, `tests/fixtures/*.json`.

**Spec:** `docs/superpowers/specs/2026-06-14-local-relevance-filtering-design.md`

---

## Context the engineer needs first

- **The repo root is** `/Users/lap15626/source/agents/epaphras/OpenClawModeSkills`. Run all commands from there.
- **Run tests with:** `python3 -m pytest tests/test_engine.py -q` (run a single test with `python3 -m pytest tests/test_engine.py::NAME -v`).
- **Current baseline is RED.** Four tests already fail because `socialcrawl._normalize_post` was rewritten in a prior change to unwrap the real `{post, computed}` API envelope, but the fixtures + tests still use the old flat `data.results` shape. **Task 1 repairs that first** — do not add new behavior until the suite is green.
- **The real SocialCrawl response shape** (verified live), per item: `{"post": {"id","url","content":{"text"},"author":{"username"},"engagement":{"views","likes","comments","shares","saves"},"published_at": <epoch int>}, "computed": {"language","estimated_reach"}}`, and the list lives at `data.items` (not `data.results`).
- **Current `socialcrawl._normalize_post`** (already in the file — Task 2 adds one line to it):
  ```python
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
      }
  ```
- **Current adapters** in `socialcrawl.py` have signature `search_X(query, lookback)`; `normalize_threads/tiktok/reddit` all just `return _normalize_post(item)`. `_results(env)` returns `env["data"]["items"]`.

---

### Task 1: Repair normalizer fixtures + tests to the real `{post, computed}` envelope

This gets the suite green. No production code changes — only fixtures and their tests, to match the normalizer that already exists.

**Files:**
- Modify: `tests/fixtures/threads_search.sample.json`
- Modify: `tests/fixtures/tiktok_search.sample.json`
- Modify: `tests/fixtures/reddit_search.sample.json`
- Modify: `tests/test_engine.py` (the 4 failing tests around lines 512–542)

- [ ] **Step 1: Confirm the 4 tests are failing**

Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -8`
Expected: `4 failed, 94 passed` — failures are `test_normalize_threads_maps_unified_fields`, `test_normalize_tiktok_maps_stats_and_epoch`, `test_normalize_reddit_maps_score_and_joins_text`, `test_search_adapter_returns_records_and_credits`.

- [ ] **Step 2: Rewrite `tests/fixtures/threads_search.sample.json`**

```json
{
  "success": true,
  "platform": "threads",
  "data": {
    "items": [
      {
        "post": {
          "id": "th_1",
          "url": "https://www.threads.net/@gamer/post/1",
          "content": {"text": "huge esports drama unfolding right now",
                      "media_urls": null, "thumbnail_url": null, "duration_seconds": null},
          "author": {"username": "gamer", "followers": 12000,
                     "display_name": "Gamer", "verified": false},
          "engagement": {"views": 50000, "likes": 820, "comments": 140,
                         "shares": 260, "saves": null},
          "published_at": 1749780000
        },
        "computed": {"language": "en", "estimated_reach": null, "content_category": "other"}
      }
    ]
  },
  "credits_remaining": 941
}
```
(Note: the real API author block has no `followers`; we include a synthetic one here so the followers→record mapping stays under test.)

- [ ] **Step 3: Rewrite `tests/fixtures/tiktok_search.sample.json`**

```json
{
  "success": true,
  "platform": "tiktok",
  "data": {
    "items": [
      {
        "post": {
          "id": "tt_1",
          "url": "https://www.tiktok.com/@creator/video/1",
          "content": {"text": "esports meltdown caught on stream"},
          "author": {"username": "creator"},
          "engagement": {"views": 1500000, "likes": 120000,
                         "comments": 8000, "shares": 30000, "saves": 1000},
          "published_at": 1749780000
        },
        "computed": {"language": "en", "estimated_reach": 1500000}
      }
    ]
  },
  "credits_remaining": 940
}
```

- [ ] **Step 4: Rewrite `tests/fixtures/reddit_search.sample.json`**

```json
{
  "success": true,
  "platform": "reddit",
  "data": {
    "items": [
      {
        "post": {
          "id": "rd_1",
          "url": "https://www.reddit.com/r/esports/comments/1",
          "content": {"text": "Esports org implodes full breakdown of the drama"},
          "author": {"username": "redditor"},
          "engagement": {"views": null, "likes": 2400, "comments": 540,
                         "shares": null, "saves": null},
          "published_at": 1749780000,
          "ext": {"subreddit": "esports"}
        },
        "computed": {"language": "en", "estimated_reach": null}
      }
    ]
  },
  "credits_remaining": 939
}
```

- [ ] **Step 5: Replace the 4 failing tests in `tests/test_engine.py`**

Replace the existing bodies of `test_normalize_threads_maps_unified_fields`, `test_normalize_tiktok_maps_stats_and_epoch`, `test_normalize_reddit_maps_score_and_joins_text`, and `test_search_adapter_returns_records_and_credits` with:

```python
def test_normalize_threads_maps_unified_fields():
    item = _sc_fixture("threads_search.sample.json")["data"]["items"][0]
    rec = socialcrawl.normalize_threads(item)
    assert rec["post_id"] == "th_1"
    assert rec["text"] == "huge esports drama unfolding right now"
    assert rec["author"] == {"handle": "gamer", "followers": 12000}
    assert rec["likes"] == 820 and rec["comments"] == 140 and rec["shares"] == 260
    assert rec["views"] == 50000
    assert rec["created"].startswith("2025-")


def test_normalize_tiktok_maps_stats_and_epoch():
    item = _sc_fixture("tiktok_search.sample.json")["data"]["items"][0]
    rec = socialcrawl.normalize_tiktok(item)
    assert rec["likes"] == 120000 and rec["comments"] == 8000 and rec["shares"] == 30000
    assert rec["views"] == 1500000 and rec["reach"] == 1500000
    assert rec["created"].startswith("2025-")


def test_normalize_reddit_maps_fields():
    item = _sc_fixture("reddit_search.sample.json")["data"]["items"][0]
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
```
(The reddit test is renamed `..._maps_fields` — the API now returns one text field, no title/selftext join.)

- [ ] **Step 6: Run the full suite to verify green**

Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -5`
Expected: `98 passed` (0 failed).

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/threads_search.sample.json tests/fixtures/tiktok_search.sample.json \
        tests/fixtures/reddit_search.sample.json tests/test_engine.py
git commit -m "test: repair normalizer fixtures+tests to real {post,computed} envelope"
```

---

### Task 2: Surface `computed.language` on the normalized record

**Files:**
- Modify: `socialcrawl.py` (`_normalize_post`)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py` (near the other normalize tests):

```python
def test_normalize_post_surfaces_language():
    item = _sc_fixture("threads_search.sample.json")["data"]["items"][0]
    assert socialcrawl.normalize_threads(item)["language"] == "en"
    # missing computed.language -> "" (never KeyError)
    bare = {"post": {"id": "x"}, "computed": {}}
    assert socialcrawl.normalize_threads(bare)["language"] == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_normalize_post_surfaces_language -v`
Expected: FAIL with `KeyError: 'language'`.

- [ ] **Step 3: Add the `language` field to `_normalize_post`**

In `socialcrawl.py`, inside the dict returned by `_normalize_post`, add this line after the `"reach": ...` entry:

```python
        "language": (c.get("language") or "").lower(),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_engine.py::test_normalize_post_surfaces_language -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add socialcrawl.py tests/test_engine.py
git commit -m "feat: surface computed.language on normalized record"
```

---

### Task 3: Uniform adapter signature with TikTok `region` param

**Files:**
- Modify: `socialcrawl.py` (`search_threads`, `search_tiktok`, `search_reddit`)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def test_search_tiktok_includes_region_when_set(monkeypatch):
    captured = {}
    def fake_get(path, params):
        captured["params"] = params
        return {"success": True, "data": {"items": []}, "credits_remaining": 5}
    monkeypatch.setattr(socialcrawl, "_sc_get", fake_get)
    socialcrawl.search_tiktok("esports", "24h", region="VN")
    assert captured["params"]["region"] == "VN"


def test_search_tiktok_omits_region_when_none(monkeypatch):
    captured = {}
    def fake_get(path, params):
        captured["params"] = params
        return {"success": True, "data": {"items": []}, "credits_remaining": 5}
    monkeypatch.setattr(socialcrawl, "_sc_get", fake_get)
    socialcrawl.search_tiktok("esports", "24h")
    assert "region" not in captured["params"]


def test_search_threads_and_reddit_accept_and_ignore_region(monkeypatch):
    monkeypatch.setattr(socialcrawl, "_sc_get",
        lambda path, params: {"success": True, "data": {"items": []}, "credits_remaining": 5})
    assert socialcrawl.search_threads("x", "24h", region="VN") == ([], 5)
    assert socialcrawl.search_reddit("x", "24h", region="VN") == ([], 5)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "region" -v`
Expected: FAIL with `TypeError: search_tiktok() got an unexpected keyword argument 'region'`.

- [ ] **Step 3: Add `region=None` to all three adapters**

In `socialcrawl.py`, replace the three adapter functions with:

```python
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
```

- [ ] **Step 4: Run the region tests + full suite**

Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -5`
Expected: all pass (now 102 passed).

- [ ] **Step 5: Commit**

```bash
git add socialcrawl.py tests/test_engine.py
git commit -m "feat: add optional region param to search adapters (TikTok uses it)"
```

---

### Task 4: Filter predicates `keep_language` and `is_spam`

**Files:**
- Modify: `engine.py` (add after `passes_floor`, around line 111)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py` (near `test_passes_floor_or_semantics`):

```python
def test_keep_language_filters_to_allowed():
    assert engine.keep_language({"language": "vi"}, {"vi", "en"}) is True
    assert engine.keep_language({"language": "en"}, {"vi", "en"}) is True
    assert engine.keep_language({"language": "hi"}, {"vi", "en"}) is False
    assert engine.keep_language({"language": ""}, {"vi", "en"}) is False
    assert engine.keep_language({"language": "hi"}, set()) is True   # fail-open: no allow-list


def test_is_spam_flags_followbait_and_excess():
    # the real DC-esports pollution: follow-bait + 3 @ + 5 #
    spam = {"text": ("WELCOME TO OUR ESPORTS TEAM FOLLOW:- @a @b @c "
                     "#trending #newplayers #ffesports #deathcrew #fyp")}
    assert engine.is_spam(spam) is True
    assert engine.is_spam({"text": "follow me for more clips"}) is True       # strong phrase
    assert engine.is_spam({"text": "#a #b #c #d #e #f #g #h"}) is True         # 8 hashtags
    assert engine.is_spam({"text": "@a @b @c @d done"}) is True               # 4 mentions
    assert engine.is_spam({"text": "great match today #esports #fyp #valorant"}) is False
    assert engine.is_spam({"text": ""}) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "keep_language or is_spam" -v`
Expected: FAIL with `AttributeError: module 'engine' has no attribute 'keep_language'`.

- [ ] **Step 3: Implement the predicates**

In `engine.py`, immediately after the `passes_floor` function (after line 111), add:

```python
_FOLLOW_BAIT = ("follow me", "follow back", "follow us", "follow:-",
                "f4f", "follow for follow", "sub to", "link in bio", "dm me")


def keep_language(record, allowed):
    """True if the post's language is allowed. Empty/falsy `allowed` keeps all (fail-open)."""
    if not allowed:
        return True
    return (record.get("language") or "") in allowed


def is_spam(record):
    """True for follow-bait / hashtag- / mention-spam. Multi-signal to spare legit #fyp posts."""
    text = record.get("text") or ""
    low = text.lower()
    hashes, ats = text.count("#"), text.count("@")
    if any(p in low for p in _FOLLOW_BAIT):   # strong signal
        return True
    if hashes >= 8 or ats >= 4:               # very excessive on its own
        return True
    if hashes >= 5 and ats >= 3:              # combined moderate (the DC-esports case)
        return True
    return False
```

- [ ] **Step 4: Run the tests + full suite**

Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add keep_language and is_spam filter predicates"
```

---

### Task 5: Config defaults `languages` + `tiktok_region`

**Files:**
- Modify: `engine.py` (`DEFAULT_POLL`, lines 20–31)
- Modify: `templates/modes.default.json` (`poll` block)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def test_poll_config_installs_language_and_region_defaults():
    data = {"modes": {}}
    pc = engine.poll_config(data)
    assert pc["languages"] == ["vi", "en"]
    assert pc["tiktok_region"] == "VN"


def test_default_template_has_language_and_region():
    tmpl = _json.loads(
        (Path(__file__).parent.parent / "templates" / "modes.default.json").read_text())
    assert tmpl["poll"]["languages"] == ["vi", "en"]
    assert tmpl["poll"]["tiktok_region"] == "VN"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "language_and_region or has_language" -v`
Expected: FAIL with `KeyError: 'languages'`.

- [ ] **Step 3: Add the two keys to `DEFAULT_POLL`**

In `engine.py`, add these two entries to the `DEFAULT_POLL` dict (after the `"floors": {...}` entry, before the closing brace):

```python
    "languages": ["vi", "en"],
    "tiktok_region": "VN",
```

- [ ] **Step 4: Add the two keys to the template**

In `templates/modes.default.json`, inside the `"poll"` object, add after the `"floors"` block:

```json
    "languages": ["vi", "en"],
    "tiktok_region": "VN"
```
(Ensure the preceding `"floors"` object now ends with a comma so the JSON stays valid.)

- [ ] **Step 5: Verify the template is valid JSON and run tests**

Run: `python3 -c "import json; json.load(open('templates/modes.default.json')); print('valid')"`
Expected: `valid`
Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add engine.py templates/modes.default.json tests/test_engine.py
git commit -m "feat: add languages + tiktok_region poll config defaults"
```

---

### Task 6: Wire filters into `run_poll` and region into `cli_poll`

This is the integration task. New behavior: `run_poll` drops foreign-language and spam records before scoring; `cli_poll` binds `tiktok_region` into the adapter call. **Adding the language filter will break three existing `run_poll` tests** whose record fixtures lack a `language` field — those are fixed within this same task so every commit stays green.

**Files:**
- Modify: `engine.py` (`run_poll` ~line 230, `cli_poll` ~line 306)
- Test: `tests/test_engine.py` (new test + update 3 existing record fixtures)

- [ ] **Step 1: Write the new failing test**

Add to `tests/test_engine.py`:

```python
def test_run_poll_drops_foreign_language_and_spam(tmp_path):
    data = _poll_data()
    data["modes"]["culture_drama"]["platforms"] = ["reddit"]
    data["poll"]["floors"] = {"reddit": {"likes": 1}}     # let engagement through; isolate filters
    posts = [
        {"post_id": "keep_en", "url": "u", "text": "clean esports recap",
         "author": {"handle": "a", "followers": 0}, "created": "2026-06-13T01:00:00+00:00",
         "likes": 100, "comments": 1, "shares": 0, "views": 0, "reach": 0, "language": "en"},
        {"post_id": "drop_hi", "url": "u", "text": "namaste cricket update",
         "author": {"handle": "b", "followers": 0}, "created": "2026-06-13T01:00:00+00:00",
         "likes": 100, "comments": 1, "shares": 0, "views": 0, "reach": 0, "language": "hi"},
        {"post_id": "drop_spam", "url": "u", "text": "follow me @x @y @z #a #b #c #d #e",
         "author": {"handle": "c", "followers": 0}, "created": "2026-06-13T01:00:00+00:00",
         "likes": 100, "comments": 1, "shares": 0, "views": 0, "reach": 0, "language": "en"},
    ]
    log = tmp_path / "log.jsonl"
    out = engine.run_poll(data, now=_now_inside(), search_fn=lambda *a: (posts, 500),
                          capable_platforms={"reddit"}, state={"posts": {}}, log_path=log)
    ids = {json.loads(l)["post_id"] for l in log.read_text().splitlines()}
    assert ids == {"keep_en"}                 # hi dropped by language, spam dropped by is_spam
    assert out["found"] == 3 and out["logged"] == 1


def test_cli_poll_passes_tiktok_region_to_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "sc_test")
    captured = {}
    def fake_tiktok(q, lb, region=None):
        captured["region"] = region
        return ([], 100)
    monkeypatch.setattr(socialcrawl, "SEARCH_ADAPTERS", {
        "tiktok": fake_tiktok,
        "reddit": lambda q, lb, region=None: ([], 100),
        "threads": lambda q, lb, region=None: ([], 100),
    })
    monkeypatch.setattr(engine, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(engine, "_poll_log_path", lambda: tmp_path / "log.jsonl")
    monkeypatch.setattr(engine, "_poll_lock_path", lambda: tmp_path / "poll.lock")
    data = _poll_data()
    data["modes"]["culture_drama"]["platforms"] = ["tiktok"]
    data["poll"]["window"] = {"start": "00:00", "end": "23:59", "tz": "UTC"}  # always inside
    data["poll"]["tiktok_region"] = "VN"
    engine.cli_poll(data)
    assert captured["region"] == "VN"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "drops_foreign or passes_tiktok_region" -v`
Expected: FAIL — `test_run_poll_drops_foreign_language_and_spam` logs all 3 (no filter yet); `test_cli_poll_passes_tiktok_region_to_adapter` fails with `TypeError` (current lambda calls the adapter with 2 args, no `region`) or `KeyError`.

- [ ] **Step 3: Add the language+spam filter line in `run_poll`**

In `engine.py` `run_poll`, find (around line 211):

```python
    score_cfg, floors = pcfg["score"], pcfg["floors"]
    top_n, lookback = pcfg["top_n_per_platform_topic"], pcfg["lookback"]
```
Add a line right after it:
```python
    allowed_langs = set(pcfg.get("languages", ["vi", "en"]))
```

Then find (around line 230):
```python
            found += len(records)
            eligible = [r for r in records if passes_floor(r, floors.get(platform, {}))]
```
Replace those two lines with:
```python
            found += len(records)
            records = [r for r in records
                       if keep_language(r, allowed_langs) and not is_spam(r)]
            eligible = [r for r in records if passes_floor(r, floors.get(platform, {}))]
```

- [ ] **Step 4: Bind `tiktok_region` into the `cli_poll` adapter call**

In `engine.py` `cli_poll`, find (around line 302):
```python
        state = load_state(_state_path())
        summary = run_poll(
            data, now=now,
            search_fn=lambda platform, q, lb: socialcrawl.SEARCH_ADAPTERS[platform](q, lb),
            capable_platforms=set(socialcrawl.SEARCH_ADAPTERS),
            state=state, log_path=_poll_log_path())
```
Replace with:
```python
        state = load_state(_state_path())
        tiktok_region = poll_config(data).get("tiktok_region", "VN")
        summary = run_poll(
            data, now=now,
            search_fn=lambda platform, q, lb:
                socialcrawl.SEARCH_ADAPTERS[platform](q, lb, region=tiktok_region),
            capable_platforms=set(socialcrawl.SEARCH_ADAPTERS),
            state=state, log_path=_poll_log_path())
```

- [ ] **Step 5: Run the new tests — they pass, but 3 existing run_poll tests now fail**

Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -8`
Expected: the two new tests pass, but `test_run_poll_logs_top_n_per_platform_and_applies_floor`, `test_run_poll_continues_when_one_platform_fails`, and `test_run_poll_computes_velocity_from_state` now FAIL — their inline records have no `language`, so the vi/en filter drops them. This is expected; fix in the next step.

- [ ] **Step 6: Add `"language": "en"` to every record dict in those 3 tests**

In `tests/test_engine.py`, in each of `test_run_poll_logs_top_n_per_platform_and_applies_floor`, `test_run_poll_continues_when_one_platform_fails`, and `test_run_poll_computes_velocity_from_state`, add `"language": "en"` to every inline post dict (the `tiktok`, `reddit` lists). For example the first record becomes:

```python
        {"post_id": "tt_big", "url": "u", "text": "t", "author": {"handle": "a", "followers": 1},
         "created": "2026-06-13T01:00:00+00:00", "likes": 50000, "comments": 9000,
         "shares": 9000, "views": 2000000, "reach": 2000000, "language": "en"},
```
Apply the same `, "language": "en"` addition to all 5 record dicts across those three tests (2 tiktok + 1 reddit in the first test, 1 reddit in the second, 1 reddit in the third).

- [ ] **Step 7: Run the full suite to verify green**

Run: `python3 -m pytest tests/test_engine.py -q 2>&1 | tail -5`
Expected: all pass (108 passed).

- [ ] **Step 8: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: filter foreign-language + spam in run_poll; pass tiktok_region in cli_poll"
```

---

### Task 7: Deploy to the pod and verify end-to-end (operational)

This validates against the live API. It is environment-specific (production pod) and not unit-testable; run it manually after the code tasks are green.

**Context:**
- Kubeconfig: `~/Documents/kubeconfig_prod.yaml`; namespace `agent-core-111735`; container `gateway`.
- The pod seeds skill code from an old S3 snapshot at startup, so updated files must be copied with `kubectl cp` after every pod restart.
- Pod name drifts; resolve it each time.

- [ ] **Step 1: Resolve the pod name**

```bash
KUBECONFIG=~/Documents/kubeconfig_prod.yaml
kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 get pods --no-headers | grep openclaw | awk '{print $1}'
```
Expected: one `openclaw-…` pod name.

- [ ] **Step 2: Copy the updated files into the pod**

```bash
KUBECONFIG=~/Documents/kubeconfig_prod.yaml
POD=$(kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 get pods --no-headers | grep openclaw | awk '{print $1}')
BASE=/Users/lap15626/source/agents/epaphras/OpenClawModeSkills
DST=$POD:/root/.openclaw/workspace/skills/OpenClawModeSkills
for f in socialcrawl.py engine.py templates/modes.default.json; do
  kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 cp "$BASE/$f" "$DST/$f" -c gateway
done
echo "copied"
```
Expected: `copied` (no errors).

- [ ] **Step 3: Run a manual poll and confirm filtering works**

```bash
KUBECONFIG=~/Documents/kubeconfig_prod.yaml
POD=$(kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 get pods --no-headers | grep openclaw | awk '{print $1}')
SKILL=/root/.openclaw/workspace/skills/OpenClawModeSkills
kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 exec "$POD" -c gateway -- \
  sh -c "cd $SKILL && rm -f poll.lock && python3 engine.py poll"
```
Expected: JSON like `{"polled": N, "found": >0, "logged": >0, "credits_remaining": …, "markers": […]}`. `logged` should be > 0 (filtering keeps real posts), and lower than a pre-filter run (foreign/spam removed).

- [ ] **Step 4: Confirm only vi/en, non-spam posts were logged**

```bash
KUBECONFIG=~/Documents/kubeconfig_prod.yaml
POD=$(kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 get pods --no-headers | grep openclaw | awk '{print $1}')
SKILL=/root/.openclaw/workspace/skills/OpenClawModeSkills
kubectl --kubeconfig $KUBECONFIG -n agent-core-111735 exec "$POD" -c gateway -- \
  sh -c "tail -20 $SKILL/trending_posts.jsonl"
```
Expected: recent lines are readable Vietnamese/English posts; none match the follow-bait/hashtag-spam shape.

- [ ] **Step 5: No commit** — this task changes only the live pod, not the repo.

---

## Notes for the executor

- **Order matters in Task 6**: the filter line (Step 3) breaks the three legacy `run_poll` tests by design; Step 6 fixes them in the same commit so the suite is green before you commit.
- **Old live `modes.json` on the pod** has a `poll` block without `languages`/`tiktok_region`. That is fine — `run_poll` reads `pcfg.get("languages", ["vi","en"])` and `cli_poll` reads `poll_config(data).get("tiktok_region","VN")`, so old configs get the defaults at read time. No migration needed.
- **Do not** add per-deployment spam threshold config — the spec deliberately keeps `_FOLLOW_BAIT` and the integer cutoffs as code constants (YAGNI).
- This plan is independent of the Telegram carousel spec and the poll-timer bug; it ships and verifies on its own via `engine.py poll`.
