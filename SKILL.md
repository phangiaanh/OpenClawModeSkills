---
name: epaphras
description: Create & configure custom Epaphras listening modes/topics over Telegram (inline keyboard + free-text wizard)
user-invocable: true
metadata:
  openclaw: {"requires":{"bins":["python3"]}}
---

# Epaphras

Create and configure custom Epaphras listening modes & topics over Telegram using a
two-screen inline keyboard and a free-text wizard.

## Engine

All state lives in `modes.json`. Call it with:

```
python3 <skill_dir>/engine.py <command> [arg] [--mode <id>]
```

| Command | Purpose |
|---|---|
| `render-modes` | Screen 1: mode list |
| `handle-callback <cb_data>` | Route any `cb_*` button; persist; return next screen |
| `handle-text <text>` | Wizard text step; no-op if wizard idle |
| `store-msgid <id>` | Persist the panel message ID |
| `get-msgid` | Return `{"message_id": <id>}` |

## Opening the panel (`/epaphras`)

All button taps and wizard text are handled **in-process by the patched gateway**:
- Any `cb_*` callback → `engine.py handle-callback <data>` → edit panel.
- Free text while a wizard is active → `engine.py handle-text <text>` → edit panel.

The LLM only needs to handle `/epaphras`: run `engine.py render-modes`, send with
`action: "send"`, note the `messageId`, then persist it:

```
python3 <skill_dir>/engine.py store-msgid <messageId>
```

```json
{
  "action": "send",
  "channel": "telegram",
  "to": "<current_chat_id>",
  "message": "<engine text>",
  "buttons": "<engine buttons>"
}
```

## Callback reference

| `callback_data` | Engine command |
|---|---|
| `cb_setmode:<id>` | activate mode |
| `cb_toggle:<tid>` | flip topic |
| `cb_back` | → Screen 1 |
| `cb_newmode` | start create-mode wizard |
| `cb_pickplat:<accountId>` | toggle platform in draft |
| `cb_createmode` | finalize new mode |
| `cb_addtopic:<mode_id>` | start add-topic wizard |
| `cb_delmode:<id>` | confirm-delete mode |
| `cb_deltopic:<mid>:<tid>` | confirm-delete topic |
| `cb_confirmdel:<...>` | perform delete |
| `cb_cancel` | cancel / reset wizard |
| `cb_notif` | toggle zernio webhook on/off |

## Wizard flows

**New mode:** ➕ New mode → type a name → pick ≤2 platforms (live from zernio) → ✅ Create.

**Add topic:** ➕ Add topic → type a topic name → topic added (active by default).

**Delete:** 🗑 → confirm → deleted. ✖ No cancels.

**Cancel:** Send a `/command` mid-wizard to cancel and run the command normally.

## Error handling

If the engine exits non-zero, send `⚠️ <error>` as plain text.

## Notifications (zernio webhook)

Screen 1 shows **🔔 Notifications: On/Off**. Tapping it creates (On) or deletes (Off)
a zernio webhook via the MCP gateway, subscribing to inbound-engagement events
(`comment.received`, `message.received`, `reaction.received`, `review.new`,
`lead.received`, `conversation.started`).

Zernio delivers events to `POST {EPAPHRAS_PUBLIC_URL}/zernio/webhook` on the OpenClaw
runtime (mounted by the gateway patch). The receiver verifies the `X-Zernio-Signature`
HMAC, dedups on `X-Zernio-Event-Id`, acks `200`, then runs
`engine.py handle-webhook <payload>`, which matches the event against the **active
mode's** platforms and active topic labels and appends one JSON line per delivery to
`EPAPHRAS_WEBHOOK_LOG` (default `webhook_events.jsonl`). Pushing matches to Telegram is
not done yet — review the log file.

Topic/mode/platform edits take effect on the *next* delivery with no zernio call (the
receiver reads `modes.json` live); a best-effort `webhook-sync` after each change
re-creates the webhook if it was deleted externally.

### Env vars
- `EPAPHRAS_MCP_GATEWAY_URL` — zernio MCP gateway (default: this deployment's gateway).
- `EPAPHRAS_PUBLIC_URL` — public base URL of the runtime; the receiver path
  `/zernio/webhook` is appended. **Required to enable notifications.**
- `EPAPHRAS_WEBHOOK_LOG` — filter-decision log path (default `webhook_events.jsonl`).

### Engine commands
`webhook-status`, `webhook-enable`, `webhook-disable`, `webhook-sync`,
`handle-webhook <payload-json>`.

## Notes

- `modes.json` is the single source of truth; the engine writes a `.bak` before each save.
- The platform picker fetches accounts via the zernio MCP gateway (no env token required).
- `EPAPHRAS_MODES_FILE` env var overrides the default `modes.json` location.
