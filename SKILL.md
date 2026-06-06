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
Every change is written through immediately to `modes.json` — there is no Save
button.

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

The `message` tool schema for Telegram inline keyboards:

```json
{
  "action": "send",
  "channel": "telegram",
  "to": "<current_chat_id>",
  "message": "<engine output: text field>",
  "buttons": "<engine output: buttons field — pass as-is>"
}
```

`<current_chat_id>` is the chat ID from the current incoming message context.
`buttons` is the 2-D array the engine already produces — do **not** unwrap,
flatten, or reformat it.

## Opening the panel (`/modes`)

Run the engine and send a numbered text menu — do **not** use the `buttons`
field (known serialization bug in the current build; buttons code is correct
and will work once the host is patched):

1. Run: `python3 <skill_dir>/engine.py render-modes`
2. Build and send a text menu from the engine output:

```
Epaphras — Listening Config 📡
Reply with a number to switch mode:

1. 📚 Research & Deep Dive
2. 🎭 Drama & Cultural Pulse  ◀ active
3. 🚨 Breaking News & Global Alert
4. 💼 Venture & Market Intelligence
```

Mark the current `current_active_mode` with `◀ active`. Send as a plain
`message` tool call with no `buttons` field.

## Handling a mode reply

When the user replies with a number (1–4), map it to the mode id and run:

```
python3 <skill_dir>/engine.py setmode <mode_id>
```

Then send the topic menu for that mode (see below).

## Topic menu

After switching mode (or when user asks to see/change topics), run:

```
python3 <skill_dir>/engine.py render-topics --mode <mode_id>
```

Build a numbered list from the engine output and send as plain text:

```
🎭 Drama & Cultural Pulse — topics
Platforms: TikTok + Threads

1. ✅ Esports Drama
2. ✅ Vtuber/Streamer Gossip
3. ⬜ Viral Memes
4. ⬜ Cancel Culture

Reply with a number to toggle. Reply "back" to return to modes.
```

When the user replies with a number, run:

```
python3 <skill_dir>/engine.py toggle <topic_id>
```

Then re-send the updated topic menu.

## Error handling

If the engine exits non-zero, send `⚠️ <error>` as plain text; do **not**
overwrite `modes.json`.

## When buttons work again

The engine already outputs the correct `{text, buttons: [[{text, callback_data}]]}`
format. When the host serialization bug is fixed, replace the text-menu flow
above with: call `message` tool with `action: "send"`, `message` ← engine
`text`, `buttons` ← engine `buttons` (pass as-is, no reformatting).

## Notes

- `modes.json` is the single source of truth; the engine writes a `.bak` before
  each save and preserves comments and key order.
- Selecting a mode both activates it (`current_active_mode`) and opens its topics.
- Each mode retains its own topic states independently.
