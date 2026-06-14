# Local Relevance & Spam Filtering — Design Spec

**Date:** 2026-06-14
**Status:** Approved (design phase)
**Repo:** `OpenClawModeSkills`
**Builds on:** `2026-06-13-socialcrawl-interval-polling-design.md` (adds filtering to the poll pipeline)

## 1. Purpose

The SocialCrawl poll currently returns globally-mixed results: US spam accounts and large
volumes of non-readable foreign-language posts (Hindi, Indonesian, etc.) pollute the feed for a
Vietnam-based user. This project adds three filters to the existing poll pipeline so that each
tick yields **locally relevant, readable, non-spam** posts:

1. **Language filter** — keep only Vietnamese + English posts.
2. **TikTok region proxy** — bias TikTok results toward Vietnam.
3. **Spam drop-filter** — discard follow-bait / hashtag-spam posts.

No new subsystem; this is a focused extension of `socialcrawl.py` (fetch/normalize) and
`engine.py` (eligibility predicates).

## 2. Key facts discovered (read first)

Verified live against the SocialCrawl API and the published OpenAPI spec
(`https://www.socialcrawl.dev/v1/openapi.json`) — not assumptions.

1. **Only TikTok exposes a region parameter.** Per the OpenAPI spec:
   - `/tiktok/search` has `region` (string). Its own description: *"this doesn't filter the tiktoks
     only in a specific region, it puts the proxy there… Use 2 letter country codes like US, GB,
     FR."* So it is a **soft proxy-location signal**, not a hard filter. Codes must be **uppercase**
     (`VN`, `US`); lowercase `vn` returns HTTP 502 `UPSTREAM_ERROR`.
   - `/threads/search` has only `query`, `start_date`, `end_date`, `trim` — **no region**.
   - `/reddit/search` has only `query`, `sort`, `timeframe`, `after`, `trim` — **no region**.
2. **`region` works in practice for TikTok.** Live test, query `esports`, `this-week`:
   - baseline → 20 `en`, 6 `fr`, … (US/global skew)
   - `region=VN` → 11 `vi`, 6 `en`, … (Vietnam skew)
   - `region=US` → 21 `en`, … (confirms the proxy takes effect)
3. **Every result on every platform carries a pre-computed language.** `item["computed"]["language"]`
   is an ISO-639-1 code (`vi`, `en`, `hi`, …) or `null`. This is the **only universal geo/relevance
   lever** — it works where `region` does not (Threads, Reddit).
4. **The API silently ignores unknown query params** (returns 200 with normal results). So sending
   `region` to Threads/Reddit is harmless (it is simply dropped), which lets all adapters share one
   signature.
5. **Spam has a recognizable shape.** A real polluting post from the live `esports` Threads feed:
   > *"WELCOME TO OUR ESPORTS TEAM DC IQ AND DC LEADER. ONE MORE STEP TO CHANGE FOLLOW:-
   > @dcesports_gg and @blurrxx.shubham , @xx__nobita__410 #trending #newplayers #ffesports
   > #deathcrew #fypシ❤️💞❤️"* — likes=0.
   Signals: follow-bait phrase, 3 `@mentions`, 5 `#hashtags`, emoji spam, zero engagement.
   Legitimate TikTok posts routinely carry 4–6 hashtags (`#fyp #esports #valorant`), so hashtag
   count **alone** is too blunt — must combine signals.

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Where filters live | **Predicates in `engine.py` next to `passes_floor`** | Same kind of eligibility predicate; consistent with existing code. A separate `filters.py` is premature (~30 lines). |
| Fetch params | **`region` stays in `socialcrawl.py` adapters** | It's an HTTP request param, belongs with the fetch. |
| Target languages | **`vi` + `en`** (user choice) | Keeps Vietnam content + global English (international esports/showbiz); drops Hindi/Indonesian/etc. Accepts that US English spam still passes the language gate — hence the spam filter. |
| Language filter fail-open | **Empty/missing `languages` ⇒ keep all** | Never silently empty the feed on a config mistake. |
| TikTok region | **`VN`, configurable** | Soft proxy signal; meaningfully shifts the mix toward local. |
| Spam action | **Drop entirely** (user choice) | Cleanest feed; same treatment as failing a floor or wrong language. |
| Spam thresholds | **Code constants, not config** | It's "basic"; `modes.json` is wizard-managed and shouldn't be cluttered with 5 spam knobs. |
| Spam calibration | **Multi-signal, not single-signal** | A lone 5-hashtag post is normal on TikTok; require a strong signal or a combination. |

## 4. Data model change

`socialcrawl._normalize_post(item)` gains one field, read from the `computed` block:

```python
"language": (c.get("language") or "").lower(),   # c = item["computed"]
```

All three normalizers already delegate to `_normalize_post`, so this is a one-line change that
applies uniformly.

## 5. Fetch change — uniform adapter signature

All three adapters take an optional `region`; only TikTok uses it:

```python
def search_tiktok(query, lookback, region=None):
    params = {"query": query,
              "date_posted": _TIKTOK_LOOKBACK.get(lookback, "this-week"),
              "sort_by": "most-liked"}
    if region:
        params["region"] = region          # uppercase ISO; soft proxy signal
    env = _sc_get("/tiktok/search", params)
    return [normalize_tiktok(i) for i in _results(env)], env.get("credits_remaining")

def search_threads(query, lookback, region=None):   # region accepted, ignored (no API param)
    ...
def search_reddit(query, lookback, region=None):     # region accepted, ignored (no API param)
    ...
```

`run_poll`'s search lambda passes `region=tiktok_region` to every adapter; Threads/Reddit drop it.

## 6. Filter predicates (`engine.py`)

```python
_FOLLOW_BAIT = ("follow me", "follow back", "follow us", "follow:-",
                "f4f", "follow for follow", "sub to", "link in bio", "dm me")

def keep_language(record, allowed):
    """allowed = set like {'vi','en'}. Empty/falsy allowed ⇒ keep all (fail-open)."""
    if not allowed:
        return True
    return (record.get("language") or "") in allowed

def is_spam(record):
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

## 7. Pipeline change (`run_poll`)

Insert language + spam filtering immediately after `found += len(records)`, **before** the floor:

```python
found += len(records)
records = [r for r in records if keep_language(r, allowed_langs) and not is_spam(r)]
eligible = [r for r in records if passes_floor(r, floors.get(platform, {}))]
```

`found` still counts raw fetched records (for credit/debug visibility); the new line trims to
readable, non-spam posts before scoring.

## 8. Config additions

`templates/modes.default.json` (and live `modes.json`) `poll` block gains two fields:

```json
"languages": ["vi", "en"],
"tiktok_region": "VN"
```

`poll_config(data)` reads them with defaults `["vi","en"]` and `"VN"`. Spam thresholds stay in
code (`_FOLLOW_BAIT` + the integer cutoffs in `is_spam`).

## 9. Error handling

- **Empty/missing `languages`** → `keep_language` returns True for all (fail-open).
- **Invalid `tiktok_region`** → SocialCrawl returns HTTP 502 for that platform's call; `run_poll`
  already wraps each platform fetch in `try/except` and records a `marker`, so one bad region
  degrades only TikTok for that tick, never the whole poll.
- **`null` language** → treated as not-in-allowed ⇒ dropped when a filter is active. Acceptable:
  unlabeled posts are low-value for a relevance feed.

## 10. Testing (`tests/test_engine.py`)

- `is_spam`: the DC-esports post → `True`; a clean post → `False`; a legit 3-hashtag `#fyp` post
  → `False`; an 8-hashtag post → `True`; a `"follow me"` post → `True`.
- `keep_language`: `{'vi','en'}` keeps `vi`/`en`, drops `hi`; empty `allowed` keeps everything;
  `null`/missing language dropped when `allowed` is set.
- `search_tiktok`: includes `region` in params when set, omits it when `None` (mock `_sc_get`).
- `_normalize_post`: surfaces `computed.language` lowercased.
- `run_poll`: a fixture mixing vi/en/hi + one spam post yields only the vi/en non-spam survivors
  through to scoring.

## 11. Out of scope

- Telegram emission of results (separate spec: `2026-06-14-telegram-trending-carousel-design.md`).
- Per-deployment tunable spam thresholds (revisit only if false positives show up in practice).
- Language detection beyond what SocialCrawl pre-computes (we trust `computed.language`).
