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

All state lives in `modes.json`. **Never hand-edit it or build button JSON
yourself** — always call the engine and pass its output directly to the
`message` tool:

```
python3 <skill_dir>/engine.py <command> [arg] [--mode <id>]
```

The engine prints one JSON object on success:
```json
{ "text": "...", "buttons": [[{"text": "...", "callback_data": "..."}]] }
```
or `{ "error": "..." }` with a non-zero exit code on failure.

Commands:
- `render-modes` — Screen 1 (mode list)
- `render-topics [--mode <id>]` — Screen 2 (topics; defaults to current active mode)
- `setmode <mode_id>` — activate a mode, persist, return its Screen 2
- `toggle <topic_id>` — flip a topic in the active mode, persist, return Screen 2

## Opening the panel (`/modes`)

1. Run: `python3 <skill_dir>/engine.py render-modes`
2. **Send a new message** with `action: "send"`. Pass the engine's `text` as
   `message` and its `buttons` as `buttons`. Note the `messageId` returned by
   the tool — you will need it for all subsequent edits.

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

Button taps arrive as a callback event. The event contains:
- `callback_data` — the button's data string
- `message.message_id` — the ID of the panel message to edit
- `message.chat.id` — the chat ID

Route by prefix, run the engine, then **edit the panel message in place** using
`action: "edit"` with the `messageId` from the callback event. This updates
the buttons instantly without sending a new message.

```json
{
  "action": "edit",
  "channel": "telegram",
  "to": "<message.chat.id>",
  "messageId": <message.message_id>,
  "message": "<engine text>",
  "buttons": "<engine buttons>"
}
```

| `callback_data` prefix | Engine command |
|---|---|
| `cb_setmode:<mode_id>` | `python3 <skill_dir>/engine.py setmode <mode_id>` |
| `cb_toggle:<topic_id>` | `python3 <skill_dir>/engine.py toggle <topic_id>` |
| `cb_back` | `python3 <skill_dir>/engine.py render-modes` |

## Error handling

If the engine exits non-zero, send `⚠️ <error>` as plain text; do **not**
overwrite `modes.json`.

## Notes

- `modes.json` is the single source of truth; the engine writes a `.bak` before
  each save and preserves comments and key order.
- Selecting a mode both activates it (`current_active_mode`) and opens its topics.
- Each mode retains its own topic states independently.
