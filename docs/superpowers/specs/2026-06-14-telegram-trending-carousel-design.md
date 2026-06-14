# Telegram Trending Carousel — Design Spec

**Date:** 2026-06-14
**Status:** Approved (design phase)
**Repo:** `OpenClawModeSkills`
**Builds on:** `2026-06-13-socialcrawl-interval-polling-design.md` (consumes poll results)
**Depends on:** `2026-06-14-local-relevance-filtering-design.md` (cleaner input) and the **poll-timer
fix** (see §10).

## 1. Purpose

The SocialCrawl poll currently only appends results to `trending_posts.jsonl` — there is no user
surface. This project pushes each tick's trending posts to Telegram as a **single navigable
carousel message**: the user pages through topics (tabs) and ranked posts (arrows), opens any post
via a link button, and triggers an `Analyze` action (runtime deferred to a later spec).

The motivating constraint: a tick can produce many posts (up to ~9 per topic × several topics).
Emitting one card per post floods the chat. Telegram has **no native swipe-carousel that also
carries buttons** (media albums are swipeable but button-less). The idiomatic solution is a
**paginated single message** edited in place — which also reuses the exact
`cb_*` → `engine.py handle-callback` → `editMessageText` machinery already patched into the gateway.

## 2. Key facts (Telegram Bot API constraints)

1. **Inline keyboards attach to one message as a bottom button grid** — buttons cannot be
   interleaved between text lines. So "a button per post" forces either many messages or pagination.
2. **`sendMediaGroup` albums are swipeable but cannot carry inline buttons** — disqualified by the
   `Open`/`Analyze` requirement.
3. **`editMessageText` with a new `reply_markup` rewrites a message in place** — this is how
   pagination/"carousel" is done for bots: a callback edits the message to show the next item. The
   gateway already does exactly this for the modes panel.
4. **A timer-initiated send has no incoming `ctx`** — unlike a callback, the hourly emit is not a
   reply to a user action, so the target **chat id must be persisted** and passed to
   `bot.api.sendMessage` explicitly.

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Layout | **One message, topic tabs + rank paging** | Exactly one message/notification per tick regardless of post count; two-dimensional nav. |
| Emit cadence | **Fresh message each tick** | A timeline of hourly trends; one notification/hour. Older messages become read-only history. |
| Platform handling | **Merge platforms within a topic, rank by score** | Platform is a card label, not a nav dimension; keeps nav to 2 dims. |
| Link | **Telegram URL button** (`Open post`) | Native click-to-open; the user's must-have. |
| Analyze | **`cb_*` callback, stubbed** | Acknowledges now; wired to runtime in a later spec. |
| Delivery | **Node poll timer sends via `bot.api.sendMessage`** | All Telegram I/O stays in Node (consistent with callbacks); `engine.py` stays a pure compute/render tool. |
| Interactivity scope | **Only the latest tick's message is live** | Avoids multi-snapshot state; older buttons return "expired". |
| Score on card | **Shown (`🔥 87.3`)** | It's the ranking basis; compact. |
| Snippet length | **~140 chars, ellipsized** | Fits a phone card without scrolling. |

## 4. Card design (one rendered post)

```
🎮 Esports  ·  TikTok  ·  #2 of 8

"terharuuu pen nangis😭 BTR qualified
to EWC paris!! #bigetronesports..."

👤 @cukrikkk2
❤️ 5,332   👁️ 63,674   💬 93   🔁 71
🔥 87.3  ·  🕐 2h ago
──────────────
[ 🎮• ][ 🎵 ][ 🎬 ][ 💻 ]      ← topic tabs (• marks selected)
[ ◀ ][ 2/8 ][ ▶ ]            ← rank within topic (wrap at ends)
[ 🔗 Open post ][ 📊 Analyze ]
```

**Fields**
- **Header:** topic emoji + name · platform label · `#<rank> of <count>`
- **Snippet:** post text, ~140 chars, ellipsized
- **Author:** `@handle`
- **Engagement row:** likes / views / comments / shares — **omit any metric the platform lacks**
  (Reddit has no views ⇒ no 👁️; Threads/Reddit have no shares ⇒ no 🔁)
- **Footer line:** `🔥 <score> · 🕐 <relative age>`

**Buttons**
- **Topic tabs:** one per active topic that has ≥1 post this tick; selected tab suffixed `•`;
  empty topics hidden. Tap ⇒ jump to that topic's `#1`.
- **Rank pager:** `◀` / `▶` cycle that topic's posts (wrap around); center label `n/N` is a no-op.
- **`🔗 Open post`:** URL button → `record["url"]`.
- **`📊 Analyze`:** callback (see §6).

## 5. Snapshot state

Each tick the poll writes a single snapshot file the callback handler reads back:

`latest_trending.json`
```json
{
  "tick_id": "1781460000",
  "topics": {
    "esports": [ { "platform": "tiktok", "rank": 1, "post_id": "...", "url": "...",
                   "text": "...", "author": "@cukrikkk2", "language": "vi",
                   "likes": 5332, "views": 63674, "comments": 93, "shares": 71,
                   "score": 87.3, "age_hours": 2.0 }, ... ],
    "showbiz": [ ... ]
  },
  "topic_order": ["esports", "showbiz", "music"],
  "topic_meta": { "esports": {"label": "Esports", "icon": "🎮"}, ... }
}
```

- `tick_id` is **epoch-seconds as a string** (compact, fits Telegram's 64-byte `callback_data`
  limit). Generated in Python (`engine.py` may use `datetime`; only the JS side must avoid
  `Date.now()`). Used verbatim in both the snapshot and `callback_data` — no second format.
- Posts are the **already-filtered, scored, top-N** records from `run_poll`, grouped by topic and
  ordered by score. This reuses `run_poll`'s existing per-(topic×platform) top-N then merges
  platforms within a topic.
- The file is overwritten each tick (single live snapshot).

## 6. Callback scheme

All callbacks are `cb_*` so the existing gateway middleware routes them to `engine.py handle-callback`.

| Button | `callback_data` | Handler behavior |
|---|---|---|
| Topic tab | `cb_trend:<tick_id>:topic:<topic_id>` | Render `<topic_id>` rank #1. |
| Prev / Next | `cb_trend:<tick_id>:rank:<topic_id>:<idx>` | Render `<topic_id>` at `<idx>` (wrapped). |
| Analyze | `cb_analyze:<tick_id>:<topic_id>:<idx>` | **Stub:** answer callback toast "Analyze coming soon" (no message edit). Wired to runtime in a later spec. |

**Expired handling:** on any `cb_trend`/`cb_analyze`, the handler compares `<tick_id>` to the
`tick_id` in `latest_trending.json`. Mismatch ⇒ return text "⏳ This trending snapshot expired —
see the latest." (the message edits to that, buttons removed). This makes only the newest message
interactive without storing per-message history.

`tick_id` is epoch-seconds (§5), keeping `callback_data` within Telegram's 64-byte limit.

## 7. Render functions (`engine.py`)

- `build_carousel_card(snapshot, topic_id, idx)` → `{ "text": ..., "buttons": [[...]] }` — pure,
  unit-testable; produces the card text + the three button rows (URL button uses `{"text","url"}`,
  others `{"text","callback_data"}`).
- `emit_payload(snapshot)` → the first card (topic_order[0], idx 0) plus the chat id — what the poll
  returns for the timer to send.
- Wire `handle_callback` to recognize `cb_trend:` and `cb_analyze:` prefixes and dispatch to
  `build_carousel_card` / the analyze stub.

## 8. Delivery (Node poll timer)

The poll timer (once firing — §10) changes from "log stdout" to:

```js
const out = execFileSync("python3", [SKILL_DIR + "/engine.py", "poll"], {...}).toString().trim();
let payload; try { payload = JSON.parse(out); } catch { /* log + return */ }
if (payload && payload.emit && payload.chat_id) {
  bot.api.sendMessage(payload.chat_id, payload.emit.text, {
    reply_markup: { inline_keyboard: payload.emit.buttons }
  }).catch(e => { /* append to /tmp/epaphras_poll.log */ });
}
```

`bot` is in scope at the injection point (right before `bot.use(botRuntime.sequentialize(...))`).
`engine.py poll` returns its existing summary **plus** `emit` (first card) and `chat_id`; when there
are zero posts it omits `emit` and the timer sends nothing.

## 9. Target chat id

The carousel needs a chat to post into. Persist the chat id the first time the user interacts with
the panel:

- The gateway already has `chat.id` when handling `/epaphras` and `cb_*` callbacks. Extend the
  existing `store-msgid` path (which saves `panel_message_id`) to also save `chat_id` into
  `modes.json` (new key `chat_id`).
- `engine.py poll` reads `chat_id` from `modes.json`; if absent (user never opened the panel), the
  poll logs/scores as today but **omits `emit`** so nothing is sent — no crash.

## 10. Dependency: poll-timer fix

Delivery is Node-side, so the injected `setInterval` **must fire** — and in the current deployment
it does not (no `IIFE_REGISTERED` marker appears after restart, confirming the injected IIFE never
executes in the JITI-compiled gateway). Implementation **must first** restore a reliably-firing
timer (synchronous `execFileSync` variant already drafted in `scripts/full_patch_v2.py`) and verify
via the `IIFE_REGISTERED` + per-tick log lines before the carousel send is layered on. The
filtering spec has **no** such dependency and can ship independently first.

## 11. Testing

`tests/test_engine.py`:
- `build_carousel_card`: correct header/snippet/engagement (Reddit omits 👁️🔁); selected topic tab
  suffixed `•`; rank pager wraps (idx 0 `◀` → last, last `▶` → 0); URL button carries `url`.
- `handle_callback`: `cb_trend:` topic/rank dispatch returns the right card; stale `tick_id` →
  expired text; `cb_analyze:` → stub toast.
- `emit_payload`: empty snapshot ⇒ no `emit` key; chat id absent ⇒ no `emit`.
- snapshot write: `run_poll` produces `latest_trending.json` grouped by topic, ordered by score.

Node timer send logic is covered by manual verification on the pod (run `engine.py poll`, confirm a
message arrives), since the gateway patch is not unit-testable.

## 12. Out of scope

- **Analyze runtime** — the heavy-workload "Explore more" backend (separate brainstorm/spec).
- Multi-tick history / making older messages interactive.
- Media/thumbnail cards via `editMessageMedia` (possible future enhancement; text cards first).
- Cron-based scheduling (delivery chosen as Node-via-timer; revisit only if the timer proves
  unfixable).
