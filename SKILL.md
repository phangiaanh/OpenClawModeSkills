---
name: modes
description: Configure Epaphras listening modes & topics over Telegram via an inline keyboard
user-invocable: true
metadata:
  openclaw: {"requires":{"bins":["python3"]}}
---

# Epaphras Modes

Configure the Epaphras listening agent over Telegram. A two-screen inline
keyboard lets the user pick an active **mode** and toggle that mode's **topics**.
Every change is written through immediately to `modes.yaml` — there is no Save
button.

## Engine

All state lives in `modes.yaml`. **Never hand-edit the YAML or build button JSON
yourself** — always call the engine:

```
python3 <skill_dir>/engine.py <command> [arg] [--mode <id>]
```

The engine prints one JSON object:
```json
{ "message": "...", "presentation": { "blocks": [ {"type": "buttons", "buttons": [{"label": "...", "value": "..."}]} ] } }
```
or `{ "error": "..." }` on failure (non-zero exit).

Commands:
- `render-modes` — Screen 1 (mode list)
- `render-topics [--mode <id>]` — Screen 2 (topics; defaults to current active mode)
- `setmode <mode_id>` — activate a mode, persist, return its Screen 2
- `toggle <topic_id>` — flip a topic in the active mode, persist, return Screen 2

## Opening the panel (`/modes`)

1. Run the engine: `python3 <skill_dir>/engine.py render-modes`
2. Send the panel to the current chat:
   ```
   openclaw message send --channel telegram --target <current_chat_id> \
     --message "<output.message>" \
     --presentation '<output.presentation as JSON string>' \
     --json
   ```
   Capture the returned message ID from `--json` output — you'll need it to edit
   in place on button taps.

`<current_chat_id>` is the chat ID of the incoming session (available from the
current message context).

## Handling button taps

Button taps arrive as `callback_data: <value>` in the user message. Route by
prefix, run the engine, then **edit the panel message in place**:

```
openclaw message edit --channel telegram --target <current_chat_id> \
  --message-id <panel_message_id> \
  --message "<output.message>" \
  --presentation '<output.presentation as JSON string>'
```

| Incoming value | Run | Screen shown |
|----------------|-----|--------------|
| `cb_setmode:<mode_id>` | `engine.py setmode <mode_id>` | Screen 2 |
| `cb_toggle:<topic_id>` | `engine.py toggle <topic_id>` | Screen 2 |
| `cb_back` | `engine.py render-modes` | Screen 1 |

If the panel message ID is not available, fall back to sending a new message
instead of editing.

If the engine exits non-zero with `{ "error": ... }`, send `⚠️ <error>` as a
plain text message; do **not** overwrite `modes.yaml`.

## Notes

- `modes.yaml` is the single source of truth; the engine writes a `.bak` before
  each save and preserves comments and key order.
- Selecting a mode both activates it (`current_active_mode`) and opens its topics.
- Each mode retains its own topic states independently.
