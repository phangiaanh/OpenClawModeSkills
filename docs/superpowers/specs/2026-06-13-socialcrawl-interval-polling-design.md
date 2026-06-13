# SocialCrawl Interval-Polling Discovery — Design Spec

**Date:** 2026-06-13
**Status:** Approved (design phase)
**Repo:** `OpenClawModeSkills`
**Supersedes:** `2026-06-11-zernio-webhook-design.md` (the zernio webhook data-fetching stage is fully replaced)

## 1. Purpose

Replace Epaphras's data-fetching stage. The previous design wired listening modes/topics to
**zernio** via a **webhook**: zernio pushed inbound-engagement events about *the user's own
connected accounts*, which a receiver filtered against topics and logged. That was the wrong tool
for what Epaphras is *for* — **discovery of trending posts across the social web**. zernio
structurally cannot do "a post matching keyword X appeared anywhere" (its own webhook spec §2.2
flagged this).

This project switches the data source to **SocialCrawl** (a unified social-media data API with
keyword search + trending across 39 platforms) and the trigger from **webhook (push)** to
**interval polling (pull)**. Each poll runs keyword searches for the active mode's active topics
across its platforms, computes a local **trend score**, gates results against absolute floors, and
appends the survivors to a JSONL log. Pushing results to Telegram remains **out of scope**
(deferred, as in the prior design).

## 2. Key facts discovered (read first)

These shaped every decision; they are verified properties of SocialCrawl, not assumptions.

1. **SocialCrawl is pull-based and keyword-capable.** Unified API, `x-api-key: sc_…` auth, base
   `https://www.socialcrawl.dev/v1`. Returns a `{success, data, credits_used, credits_remaining,
   cached, request_id}` envelope. This is the discovery capability zernio lacks.
2. **Per-platform keyword search is ~20× cheaper than the unified call.** `GET /search/everywhere`
   costs **20 credits** (covers ~12 sources in one call). The per-platform search endpoints cost
   **1 credit** each:
   - Threads — `GET /v1/threads/search` (`query`, `start_date`, `end_date`).
   - TikTok — `GET /v1/tiktok/search` (`query`, `date_posted` = yesterday|this-week|this-month|…,
     `sort_by` = relevance|most-liked|date-posted, `region`, `cursor`).
   - Reddit — `GET /v1/reddit/search` (`query`, `sort` = relevance|new|top|comment_count,
     `timeframe` = day|week|month|year, `after`).
3. **Keyword discovery is not uniform across platforms.** Threads/TikTok/Reddit/X/YouTube support
   it; **Facebook and Instagram do not** offer organic-post keyword search (Meta restriction) —
   only ads/marketplace/events or known-page pulls. So a "platform capability map" gates which
   platforms can be polled for discovery.
4. **Credits are real money.** Cost = `searchable platforms × active topics × 1 credit` per poll.
   With 3 platforms × 5 topics = 15 cr/poll. Hourly 08:00–20:00 ≈ 13 polls/day. Free tier is 100
   credits (one-time); paid tiers ~£15–£299+. Cost control (active-topics-only, windowed cadence,
   floors, top-N) is therefore first-class.
5. **No server-side "trending" sort on unified search.** Each post carries raw engagement (likes,
   comments, shares, views, saves), pre-computed `engagement_rate`/`estimated_reach`, and a
   `created` timestamp — so **"trending" is a scoring function we compute locally** (fully tunable,
   unit-testable). TikTok/Reddit additionally allow native pre-sort (`most-liked`/`top`).

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Data source | **SocialCrawl, full replacement of zernio** | zernio can't do discovery; remove all zernio/webhook code. |
| Trigger | **Interval polling (gateway patch + `setInterval`)** | Mirrors the existing webhook-receiver patch approach; pull is SocialCrawl's native mode. |
| Logic split | **Thin JS timer + fat Python engine** | Keep the fragile, restart-lost, hard-to-test gateway patch minimal; all logic unit-testable in `engine.py`. |
| Fetch path | **Per-platform search endpoints (1 cr)**, not `/search/everywhere` (20 cr) | ~20× cheaper; richer native params; only pay for selected platforms. |
| Query style | **Broad query + local trending filter** | A "cultural pulse" tool wants the viral pulse of each vertical; the score does the filtering. |
| Topic model | **Decouple `label` (display) from `query` (sent)** | "art" is a fine label but a poor query; queries must be tunable independently. |
| Trend score | **Per-platform-normalized magnitude + velocity + recency** | Raw metrics aren't comparable across platforms; re-logging unlocks velocity (Δ/poll). |
| Score weights | **comments/shares ≈ 2× likes; β≈0.6 (magnitude-leaning); gravity≈1.5** | Drama/culture mode values conversation + spread + persistence; all tunable in config. |
| Dedup | **Re-log trajectory** (track posts across polls) | A post that holds the top over hours is genuinely top-of-day; enables velocity + "hours_trending". |
| Floor | **Absolute per-platform numeric thresholds** | Safety net under per-platform normalization; prevents "top of a dead batch" from logging. |
| Volume cap | **Top-N per (topic × platform) per poll** (default 3) | Guarantees every watched platform surfaces (you chose them deliberately); avoids one platform sweeping a topic. |
| Cadence | **Hourly, 08:00–20:00, local tz (ICT default)**, active topics only | Trends build over hours; windowed + active-only bounds credit spend. |
| Output | **Append JSONL log only** (no Telegram) | Validate polling/dedup first; delivery deferred (seam left in place). |
| API key | **`SOCIALCRAWL_API_KEY` env var** (`sc_…`) | Real secret; never committed (zernio used a token-in-URL gateway). |

## 4. Architecture

```
Gateway process (patched, marker _EPAPHRAS_POLL_V1)
  _registerEpaphrasPoll(): setInterval(interval_minutes) ──> shell: python3 engine.py poll

engine.py poll  (all logic; unit-testable)
  1. window + enabled gate  → outside 08–20 ICT or poll.enabled=false → skip, exit 0
  2. resolve work           → active mode → platforms ∩ capability-map, active topics
  3. fetch                  → per (topic.query × platform): SocialCrawl per-platform search (1cr)
  4. normalize              → unified record per post
  5. floor filter           → drop posts below platform absolute floor
  6. score + state          → magnitude + velocity + recency; update poll_state.json
  7. rank + cap             → per (topic × platform), top_n_per_platform_topic by score
  8. log                    → append JSONL (rank, hours_trending)
  9. summary                → {polled, found, logged, credits_used, credits_remaining}
```

**Design rule:** the JS patch stays dead-thin (`setInterval` → `engine.py poll`). The window gate,
topic loop, scoring, floors, and dedup all live in Python so they survive pod restarts untouched
and are unit-testable.

## 5. Data model

### `modes.json` (the `webhook` block is removed; a `poll` block replaces it)

```json
{
  "current_active_mode": "culture_drama",
  "modes": {
    "culture_drama": {
      "name": "Drama & Cultural Pulse",
      "icon": "🎭",
      "platforms": ["threads", "tiktok", "reddit"],
      "topics": {
        "esports":    {"label": "Esports",    "query": "esports",    "active": true},
        "showbiz":    {"label": "Showbiz",    "query": "celebrity",  "active": true},
        "music":      {"label": "Music",      "query": "music",      "active": true},
        "art":        {"label": "Art",        "query": "art",        "active": false},
        "technology": {"label": "Technology", "query": "technology", "active": true}
      }
    }
  },
  "poll": {
    "enabled": true,
    "interval_minutes": 60,
    "window": { "start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh" },
    "lookback": "24h",
    "top_n_per_platform_topic": 3,
    "score":  { "w_like": 1, "w_comment": 2, "w_share": 2, "w_reach": 1, "beta": 0.6, "gravity": 1.5 },
    "floors": {
      "tiktok":  { "views": 100000, "likes": 10000 },
      "reddit":  { "upvotes": 500 },
      "threads": { "likes": 500 }
    }
  },
  "wizard": { "step": "idle" },
  "panel_message_id": null
}
```

- `platforms` becomes a **string list** of network names (previously zernio account objects).
- Topics gain a **`query`** field (defaults to `label` if absent).
- Seed: **one** mode `culture_drama`, five topics, `art` inactive by default.

### Platform capability map (code constant, not config)

Maps each *searchable* platform → its adapter (endpoint path, param translation, response
normalizer). The new-mode picker offers **only** platforms in this map. Initial set: `threads`,
`tiktok`, `reddit` (extensible to `x`, `youtube`, …). Facebook/Instagram organic search is absent
by design.

### State store — `poll_state.json` (sidecar, gitignored)

Keyed `"<platform>:<post_id>"`:
```json
{ "first_seen": "<iso>", "last_seen": "<iso>", "last_raw": 12345, "peak_score": 0.87, "topic": "esports" }
```
Drives velocity (`raw_now − last_raw`) and re-logging. Entries age out after ~24h or once they
drop below floor.

### Log line — `trending_posts.jsonl` (append-only, gitignored)

```json
{"ts":"…","topic":"esports","platform":"tiktok","post_id":"…","url":"…","text":"…",
 "author":{"handle":"…","followers":0},"created":"…",
 "likes":0,"comments":0,"shares":0,"reach":0,
 "magnitude":0.0,"velocity":0.0,"score":0.0,"rank":1,"hours_trending":3}
```

`author` (handle + followers), `url`, full `text`, and per-platform `created`/`first_seen` are
retained deliberately as the substrate for **future wave monitoring** (§13) — they are the raw
material a later cross-platform content-matching step would join on. Logging `author` now is
lossless and cheap; omitting it would make past content unrecoverable for that feature.

### UI

The existing `🔔 Notifications` button is repurposed to **`📡 Polling: On/Off`** → toggles
`poll.enabled`; the `cb_notif` callback is retained, pointed at the new flag.

## 6. Trend score

Per post, per poll:

```
raw       = w_like·likes + w_comment·comments + w_share·shares + w_reach·reach
magnitude = raw / platform_baseline            # normalize within the platform's batch this poll
velocity  = max(0, (raw − last_raw) / Δhours)  # from poll_state; 0 on first sighting
recency   = 1 / (age_hours + 2)^gravity

score = (beta·magnitude + (1 − beta)·velocity_norm) · recency
```

Defaults: `w_like=1, w_comment=2, w_share=2, w_reach=1`, `beta=0.6` (magnitude-leaning),
`gravity=1.5`. All in `poll.score`; retuning never touches code. `velocity_norm` is velocity scaled
to the same per-platform baseline as magnitude. Missing metrics default to 0.

**Gating order:** (1) absolute per-platform **floor** removes non-notable posts; (2) score the
survivors; (3) **top-N per (topic × platform)** keeps only the highest scores *within each
platform* (default 3), so every watched platform is represented and no single platform sweeps a
topic. A post enters the tracked set the first hour it clears floor + top-N, re-logs each poll
while it holds, and ages out when it drops below floor — so `hours_trending` measures how long it
held the bar.

## 7. Component surface (`engine.py` + `socialcrawl.py`)

### `socialcrawl.py` (new)
- `_sc_get(path, params)` — `GET {base}{path}` with `x-api-key`; parse envelope; raise
  `ConfigError` on `success:false` / HTTP / network failure / missing key.
- Per-platform adapters behind the capability map: `search_threads`, `search_tiktok`,
  `search_reddit` — each owns its path, param translation (`lookback` → native recency param),
  native sort, and normalizer → unified record `{post_id, url, text, author:{handle, followers},
  created, likes, comments, shares, views, reach}`.

### `engine.py` (additions)
| Command | Effect | Returns |
|---|---|---|
| `poll` | full tick (§4); honors window/enabled; never crashes the timer | `{polled, found, logged, credits_used, credits_remaining}` or `{skipped, reason}` |

Plus pure helpers: `trend_score`, `apply_floor`, `normalize_*` (via adapters), `poll_state` IO,
window/enabled gate, `_now`/age math. `cb_notif` → toggles `poll.enabled`.

### Env vars
- `SOCIALCRAWL_API_KEY` — **new, required** (`sc_…`).
- `EPAPHRAS_MODES_FILE` — unchanged.
- `EPAPHRAS_POLL_LOG` — replaces `EPAPHRAS_WEBHOOK_LOG` (default `<skill_dir>/trending_posts.jsonl`).
- Removed: `EPAPHRAS_MCP_GATEWAY_URL`, `EPAPHRAS_PUBLIC_URL`.

## 8. Error handling

A poll tick must never crash the gateway timer; one platform failing must not lose the others.

| Situation | Behavior |
|---|---|
| `SOCIALCRAWL_API_KEY` unset/invalid | `ConfigError` → `⚠️`; non-zero exit; nothing logged. Picker shows `⚠️` instead of platforms. |
| One platform's search fails | Caught per-adapter; write `⚠️ <platform> fetch failed` marker; **continue** others (partial poll still logs). |
| Credits exhausted mid-batch | Stop further calls; `⚠️ low credits` marker; log what's already scored; exit 0. |
| No active mode / topics / searchable platforms | `{skipped:true, reason:"nothing to poll"}`, exit 0 — no API calls. |
| Outside window / `poll.enabled=false` | Skip, exit 0. |
| Malformed post (no `post_id`/`created`) | Dropped (can't dedup or decay). Other missing metrics default to 0. |
| `poll_state.json` missing/corrupt | Treat as empty; velocity 0 that tick; never blocks. |
| Overlapping ticks | `poll.lock` lockfile; a new tick exits early if one is in flight (no double-spend / state race). |
| Network flakiness | Per-adapter `urlopen(timeout=10)`; timeout = that-platform-failed, not a crash. |

The timer is the sole writer of `poll_state.json` and the log; the lockfile serializes ticks.

## 9. Test plan

- **Unit — scoring/filtering (no network):** weighting (comments/shares 2×); per-platform
  magnitude normalization; velocity from seeded state (incl. 0 first-sighting); recency decay;
  floor drops sub-threshold; top-N cap; `hours_trending` from `first_seen`.
- **Unit — adapters/normalizer:** each platform's sample response → unified record. Fixtures:
  `threads_search.sample.json`, `tiktok_search.sample.json`, `reddit_search.sample.json`.
- **Unit — poll orchestration (monkeypatch `_sc_get`):** window gate skips outside hours; disabled
  skips; one platform raising → others still logged; credits-exhausted stops early; state ages out.
- **Manual (gateway, not unit-testable):** apply patch → hourly tick fires inside window only; a
  real poll appends to `trending_posts.jsonl`; lockfile blocks overlap; `📡 Polling` toggle flips
  `poll.enabled`.

## 10. Cleanup (full zernio removal)

Delete: the webhook receiver patch + HMAC/dedup receiver; `enable_webhook`/`disable_webhook`/
`sync_webhook`/`webhook_status`/`_maybe_sync`; `handle_webhook`/`match_event`/`_gather_text`/
`_topic_matches`/`_event_platform`/`_webhook_log_path`/`_list_webhooks`/`_find_webhook_by_url`/
`_wh_id`/`webhook_config`/`webhook_url`/`_gen_secret`; zernio account picker (`fetch_accounts`,
`_get_accounts_payload`, `_ensure_accounts`) and `_mcp_call`; `WEBHOOK_EVENTS`/`WEBHOOK_NAME`/
`DEFAULT_MCP_GATEWAY_URL`/`DEFAULT_PUBLIC_URL`; the `webhook` config block; webhook engine commands.
The new-mode wizard's platform step switches from "live zernio accounts" to "pick from the
capability map." Update `SKILL.md` / `README.md`.

## 11. Repo layout

```
OpenClawModeSkills/
  engine.py                 # + poll, scoring, floors, state; − all webhook/zernio code
  socialcrawl.py            # _sc_get + per-platform search adapters + normalizer
  scripts/full_patch_v2.py  # − webhook receiver  + thin poll timer (_EPAPHRAS_POLL_V1)
  modes.json                # seeded: 1 mode, 5 topics, poll block
  poll_state.json           # runtime state (gitignored)
  trending_posts.jsonl      # runtime log (gitignored)
  SKILL.md / README.md      # rewritten for discovery/polling
  templates/modes.default.json
  tests/
    test_engine.py          # scoring/floor/poll tests (webhook tests removed)
    fixtures/{threads,tiktok,reddit}_search.sample.json
```

## 12. Out of scope (explicit)

- Pushing trending posts to Telegram (deferred; log only — seam left in place).
- The 20-credit `/search/everywhere` unified call (per-platform search is cheaper; could be added
  as an explicit "search everywhere" mode later).
- Non-searchable platforms (Facebook/Instagram organic) and a "watch specific pages" model.
- Multiple concurrent active modes / multi-tenant.
- Per-platform trending endpoints (e.g. `/tiktok/trending`, 5 cr) — local scoring covers trending
  from search results today.

## 13. Future direction: wave monitoring (not built here)

A planned later feature will track how a piece of content **spreads from one platform to another**
(a "wave"). It is explicitly **out of scope** for this project, but two decisions here are made to
keep it cheaply buildable later — this section records the seam so we don't paint into a corner:

- **Per-platform records, not pooled.** Top-N is scoped per (topic × platform) and the state store
  keys on `platform:post_id`, so each platform's sighting of trending content — and its
  `first_seen` timestamp — is preserved independently. That ordering (who had it first, how fast it
  jumped) is the core wave signal and would be lost under pooling.
- **Rich per-post logging.** Each log line retains `url`, full `text`, `author.handle`, and
  per-platform `created`/`first_seen`. These are the **join keys** a future cross-platform matcher
  would use to decide that a TikTok video, a Threads post, and a Reddit thread are "the same
  content": a shared external URL, near-duplicate normalized text, or the same author across
  networks.

**Deliberately deferred** (do *not* build now): the content-matching/clustering engine itself, any
content-fingerprint/hashing scheme, media-level matching, and the cross-platform timeline/graph.
When built, the matcher should compute its fingerprint *from the already-logged fields above* — no
data captured here is wave-specific or speculative; it is all independently useful for the trending
log today.
