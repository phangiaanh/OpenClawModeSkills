---
name: modes
description: Configure Epaphras listening modes & topics over Telegram via inline keyboard buttons
user-invocable: true
metadata:
  openclaw: {"requires":{"bins":["python3"]}}
---

# Epaphras Modes

Configure the Epaphras listening agent over Telegram. A two-screen inline
keyboard lets the user pick an active **mode** and toggle that mode's **topics**.
Every change is written through immediately to `modes.json` — there is no Save
step.

## Engine

All state lives in `modes.json`. Call it with:

```
python3 <skill_dir>/engine.py <command> [arg] [--mode <id>]
```

Render commands return `{ "text": "...", "buttons": [[...]] }`.
State commands (setmode, toggle) persist and return the next screen.
Utility commands:

| Command | Purpose |
|---|---|
| `render-modes` | Screen 1: mode list |
| `render-topics [--mode <id>]` | Screen 2: topics |
| `setmode <mode_id>` | Activate mode, return Screen 2 |
| `toggle <topic_id>` | Flip topic, return Screen 2 |
| `store-msgid <id>` | Persist the panel message ID |
| `get-msgid` | Return `{"message_id": <id>}` |

## Opening the panel (`/modes`)

1. Run `render-modes`, send with `action: "send"`, note the `messageId` in the result.
2. Immediately persist it:

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

## Handling a button callback

> **Every callback MUST use `action: "edit"`, never `action: "send"`.**
> Sending creates a new message. Editing updates the existing panel in place.

When `callback_data` arrives:

1. Run `get-msgid` to retrieve the stored panel message ID.
2. Run the engine command for the callback (see table below).
3. Call `message` with **`action: "edit"`** and the retrieved `messageId`.

```json
{
  "action": "edit",
  "channel": "telegram",
  "to": "<current_chat_id>",
  "messageId": <id from get-msgid>,
  "message": "<engine text>",
  "buttons": "<engine buttons>"
}
```

| `callback_data` | Engine command |
|---|---|
| `cb_setmode:<mode_id>` | `setmode <mode_id>` |
| `cb_toggle:<topic_id>` | `toggle <topic_id>` |
| `cb_back` | `render-modes` |

## Error handling

If the engine exits non-zero, send `⚠️ <error>` as plain text.

## Notes

- `modes.json` is the single source of truth; the engine writes a `.bak` before each save.
- Selecting a mode activates it and opens its topics.
- Each mode retains its own topic states independently.
