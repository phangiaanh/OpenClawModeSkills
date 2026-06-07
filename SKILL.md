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

## Calling the `message` tool

After every engine call you **must** call the `message` tool immediately.
Pass the engine's `text` as `message` and its `buttons` array as `buttons`:

```json
{
  "action": "send",
  "channel": "telegram",
  "to": "<current_chat_id>",
  "message": "<engine output text>",
  "buttons": "<engine output buttons array>"
}
```

`<current_chat_id>` is the numeric chat ID from the current incoming event.
Do **not** print or paraphrase the engine output — call `message` with it directly.

## Opening the panel (`/modes`)

1. Run: `python3 <skill_dir>/engine.py render-modes`
2. Immediately call `message` with the result's `text` and `buttons`.

## Handling a button callback

When a callback arrives, read its `data` field and dispatch:

| `data` prefix | Engine command |
|---|---|
| `cb_setmode:<mode_id>` | `python3 <skill_dir>/engine.py setmode <mode_id>` |
| `cb_toggle:<topic_id>` | `python3 <skill_dir>/engine.py toggle <topic_id>` |
| `cb_back` | `python3 <skill_dir>/engine.py render-modes` |

Then immediately call `message` with the result's `text` and `buttons`.

## Error handling

If the engine exits non-zero, send `⚠️ <error>` as plain text; do **not**
overwrite `modes.json`.

## Notes

- `modes.json` is the single source of truth; the engine writes a `.bak` before
  each save and preserves comments and key order.
- Selecting a mode both activates it (`current_active_mode`) and opens its topics.
- Each mode retains its own topic states independently.
