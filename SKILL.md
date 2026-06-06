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
yourself** — always call the engine and pass its output straight to the messaging
tool:

```
python3 <skill_dir>/engine.py <command> [arg] [--mode <id>]
```

The engine prints one JSON object: `{ "text": "...", "buttons": [[...]] }`, or
`{ "error": "..." }` on failure (non-zero exit). Pass `text` as the message text
and `buttons` as the inline keyboard buttons.

Commands:
- `render-modes` — Screen 1 (mode list)
- `render-topics --mode <id>` — Screen 2 (topics for a mode)
- `setmode <mode_id>` — activate a mode, persist, return its Screen 2
- `toggle <topic_id>` — flip a topic in the active mode, persist, return Screen 2

## Opening the panel (`/modes`)

1. Run `python3 <skill_dir>/engine.py render-modes`.
2. **Send a new message** to the current chat with the returned `text` and
   `buttons` as a Telegram inline keyboard.

## Handling button taps

Button taps arrive as a message containing `callback_data: <data>`. Route by
prefix, then **edit the current panel message in place** (`editMessage`) with the
engine's returned `text` and `buttons`:

| Incoming `callback_data` | Run | Then |
|--------------------------|-----|------|
| `cb_setmode:<mode_id>` | `engine.py setmode <mode_id>` | edit message → Screen 2 |
| `cb_toggle:<topic_id>` | `engine.py toggle <topic_id>` | edit message → Screen 2 |
| `cb_back` | `engine.py render-modes` | edit message → Screen 1 |

If the engine exits non-zero with `{ "error": ... }`, send a brief one-line
notice (e.g. "⚠️ <error>") and re-render the last screen; do **not** overwrite
`modes.yaml`.

## Notes

- `modes.yaml` is the single source of truth; the engine writes a `.bak` before
  each save and preserves comments and key order.
- Selecting a mode both activates it (`current_active_mode`) and opens its topics.
- Each mode retains its own topic states independently.
