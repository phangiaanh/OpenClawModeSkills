# Zernio Webhook Wiring — Design Spec

**Date:** 2026-06-11
**Status:** Approved (design phase)
**Repo:** `OpenClawModeSkills`
**Builds on:** `2026-06-08-epaphras-custom-modes-design.md` (modes/topics/platforms config)

## 1. Purpose

The previous projects let a customer define listening **modes**, **topics**, and attach
**platforms** (live zernio accounts), all persisted to `modes.json`. This project wires that
config to zernio: it **registers a zernio webhook**, **receives** the resulting event
deliveries, **filters** them against the active mode's topics/platforms, and **logs** the
filter decision to a file for later review. Pushing matched notifications to the Telegram UI
is explicitly **out of scope** (deferred to a later project).

## 2. Critical constraints discovered (read first)

These shaped every decision below; they are not negotiable facts about the platform.

1. **Topics are not a webhook concept.** `webhooks_create_webhook_settings` accepts only
   `name`, `url`, `events[]`, `secret`, `is_active`, `custom_headers` — **no keyword/query/
   filter field.** Zernio filters deliveries *only by event type*. Topic/platform filtering
   therefore happens **downstream in our receiver**, which reads `modes.json` live per delivery.
2. **No web-listening events exist.** Zernio is a social management/publishing/inbox platform.
   Its ~37 events are about *your own connected accounts' activity* (posts, inbound comments/
   DMs, reviews, leads). There is **no** "a post matching keyword X appeared anywhere" event.
   Topic matching is only meaningful on inbound-engagement events that carry others' text about
   your accounts.
3. **Webhooks are account-global.** One webhook fires for *all* connected accounts; there is no
   per-mode/per-platform scoping at registration. Scoping is the receiver's job.
4. **At-least-once + 5s ack.** A delivery must get a `2xx` within 5s or zernio retries (7
   attempts over ~51h, then dead-letters). The same event may arrive more than once → dedup on
   `X-Zernio-Event-Id`.
5. **HMAC signing.** When a `secret` is set, every delivery carries `X-Zernio-Signature`
   (HMAC-SHA256 of the raw body). The receiver must verify it and reject mismatches.
6. **Plan-gating.** `review.new` / `reaction.received` may require a "Usage plan" tier; absent
   entitlement they simply never deliver. If `create` itself errors, surface `⚠️` and persist
   nothing.
7. **Gateway URL drift.** `engine.py:57` hardcodes `gw-zernio-53461`, but this deployment's
   gateway is `gw-watermelon-111735` (same zernio MCP today, but the code should not hardcode a
   foreign gateway). Fixed by env (see §7).
8. **The public host had no receiver.** `https://openclaw-111735-epaphras.agentbase-runtime.
   aiplatform.vngcloud.vn/` serves the "OpenClaw Control" SPA + `/health`; all other POSTs 404.
   The receiver route is *new* and is mounted on that same runtime HTTP server (§5).

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Architecture | **Thin JS receiver + fat Python engine** (Approach A) | Mirrors the existing spine; all logic stays unit-testable in `engine.py`. |
| Scope | **Registration + receiver**, output to **log file** | UI push deferred per customer request. |
| Events subscribed | **Inbound engagement set** (§4) | Only events where topic keyword-matching is meaningful. |
| Topic → delivery | **Downstream live match** in receiver | No webhook filter field exists; live read reflects edits instantly. |
| Registration trigger | **`🔔 Notifications` panel button** (enable/disable) | Matches the button-driven UX. |
| Sync on mode/topic change | **Idempotent drift-correct** (`webhook-sync`) | Topic edits need no zernio write; sync only writes on real drift; self-heals external deletes. |
| Matching scope | **Active mode only** | One active mode at a time; receiver reads `current_active_mode` live. |
| Receiver output | **Append JSONL to a log file** (no Telegram) | UI push deferred; log is auditable. |
| Secret storage | **In `modes.json`** (gitignored runtime state) | Same trust level as the existing token-in-env pattern; receiver (JS) already reads `modes.json`. |
| Dedup store | **Bounded in-memory `Set`** (~1000) in the receiver | At-least-once tolerance; restart reset is acceptable. |

## 4. Data model & matching

### New `webhook` block in `modes.json`
```json
"webhook": {
  "enabled": true,
  "id": "<zernio webhook id>",
  "secret": "<generated 32-byte hex>",
  "url": "https://.../zernio/webhook",
  "events": ["comment.received","message.received","reaction.received","review.new","lead.received","conversation.started"],
  "synced_at": "<iso8601, stamped on each successful sync>"
}
```
- Existing top-level keys (`current_active_mode`, `panel_message_id`, `modes`, `wizard`) unchanged.
- `secret` is generated with `secrets.token_hex(32)` on first enable.
- `panel_chat_id` capture is **deferred** (only needed once UI push lands).

### Subscribed events (constant)
`EPAPHRAS_WEBHOOK_EVENTS = ["comment.received", "message.received", "reaction.received",
"review.new", "lead.received", "conversation.started"]`.

### `handle-webhook <json>` (Python, tested)
1. Parse the delivered payload; resolve the event's account → `platform`.
2. Keep only if `platform` ∈ the **active mode's** platforms.
3. Extract candidate text (comment body / message text / review text / lead fields).
4. Lowercase substring/token match against the **active mode's active topic labels**
   (`topicX OR topicY …`).
5. Return `{notify, matched_topics, platform, snippet, event, event_id}`. Reads `modes.json`
   live → always reflects the current mode/topics.

## 5. Receiver (gateway patch, `scripts/full_patch_v2.py`, marker `_EPAPHRAS_WEBHOOK_V1`)

Mount `POST /zernio/webhook` on the OpenClaw Control HTTP server (same runtime serving the
dashboard). Thin and ordered for the 5s budget:

1. Read the **raw body**; compute HMAC-SHA256 with `webhook.secret`; compare to
   `X-Zernio-Signature`. Reject `401` on missing/mismatch.
2. Dedup on `X-Zernio-Event-Id` against a bounded in-memory `Set`; if seen, `200` and stop.
3. **Respond `200` immediately** (ack before processing).
4. Shell out to `engine.py handle-webhook <payload>`.
5. Append one JSONL line — `{event_id, event, platform, matched_topics, snippet, notify, ts}`
   — to `EPAPHRAS_WEBHOOK_LOG` (default `<skill_dir>/webhook_events.jsonl`). **No Telegram
   send.** Both matches and non-matches are logged for audit.

Idempotent, marker-guarded, anchored against the live runtime's HTTP/router setup (located at
apply time, like the existing Patch 2/3). **Implementation risk:** finding the runtime's HTTP
server/router anchor is the main unknown; spike this first.

## 6. `engine.py` command surface (additions)

- **Generalize the MCP client:** refactor the hardcoded accounts body into
  `_mcp_call(name, arguments)` (SSE parse + `ast.literal_eval`, reused). `fetch_accounts()` →
  `_mcp_call("accounts_list_accounts", {})`; webhook commands reuse it.

| Command | Effect | Returns |
|---|---|---|
| `webhook-status` | `webhooks_get_webhook_settings` | current state |
| `webhook-enable` | gen secret if absent; create-or-update with `url`+`events`; persist block; `enabled=true`; then `webhooks_test_webhook` | status / `⚠️` |
| `webhook-disable` | `webhooks_delete_webhook_settings`; `enabled=false` | status |
| `webhook-sync` | idempotent drift-correct: read settings, create/update only on drift; no-op if disabled | status |
| `handle-webhook <json>` | match event (§4) | `{notify, …}` |

- `handle-callback` calls `webhook-sync` after any mode/topic/platform mutation **iff**
  `webhook.enabled` (topic/platform/active-mode edits normally produce no zernio write).
- New callback `cb_notif` → `webhook-enable`/`webhook-disable`; rendered as
  `🔔 Notifications: On/Off` on Screen 1. All `⚠️` paths leave `modes.json` intact.

## 7. Config / env

- `EPAPHRAS_MCP_GATEWAY_URL` — MCP gateway; **default this deployment's `gw-watermelon-111735`**
  (replaces the hardcoded `gw-zernio-53461`).
- `EPAPHRAS_PUBLIC_URL` — public base URL; webhook `url` = this + `/zernio/webhook`.
- `EPAPHRAS_WEBHOOK_LOG` — JSONL log path (default `<skill_dir>/webhook_events.jsonl`, gitignored).
- `EPAPHRAS_MODES_FILE` — unchanged.

## 8. Repo layout

```
OpenClawModeSkills/
  engine.py                    # + _mcp_call, webhook-{status,enable,disable,sync}, handle-webhook, cb_notif
  scripts/full_patch_v2.py     # + receiver patch (_EPAPHRAS_WEBHOOK_V1)
  SKILL.md / README.md         # notifications button, env vars, log file
  tests/
    test_engine.py             # + webhook command + matcher tests
    fixtures/comment_received.sample.json
  webhook_events.jsonl         # runtime log, gitignored
```

## 9. Test plan

- **Registration (monkeypatch `_mcp_call`):** `webhook-enable` create path (no existing) vs
  update path (existing id); secret generated once and persisted; `webhook-disable` deletes &
  clears; `webhook-sync` no-ops when in sync, writes only on drift (missing/inactive/wrong
  url/events); create-error → `⚠️`, nothing persisted.
- **`handle-webhook`:** match (topic in text, platform in active mode) → `notify:true`;
  platform not in active mode → `notify:false`; no topic match → `notify:false`; reads live
  `current_active_mode`. Fixture: `comment_received.sample.json`.
- **`cb_notif`** toggles `enabled` and routes to enable/disable.
- **Manual (JS, not unit-testable):** Telegram dry-run — tap `🔔` to enable, run
  `webhooks_test_webhook`, confirm a JSONL line appears in `webhook_events.jsonl`; bad-signature
  delivery → `401`; duplicate `event_id` → single log line; ack returns `200` quickly.

## 10. Out of scope (explicit)

- Pushing matched events to the Telegram UI (deferred; receiver only logs).
- Per-mode event sets (architecture leaves the seam in `webhook-sync`; all modes use the same
  set today).
- General web/keyword listening beyond your own accounts (no such zernio event).
- Multi-tenant / multiple concurrent active modes.
