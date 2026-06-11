# OpenClawModeSkills — Epaphras

An OpenClaw skill to create and configure custom Epaphras listening modes & topics
over Telegram using a two-screen inline keyboard and a free-text wizard. Persists
all state to `modes.json` (write-through, no Save button).

Requires only Python 3 standard library — no pip or external packages needed.

## Install

1. Place this directory where OpenClaw discovers skills.
2. Ensure `python3` is on PATH (gated via `metadata.openclaw.requires.bins`).
3. (Optional) Set `EPAPHRAS_MODES_FILE` to choose where `modes.json` lives.
   Default: alongside `engine.py`. The file is seeded from
   `templates/modes.default.json` on first use.
4. The platform picker calls the zernio MCP gateway (token embedded in the gateway URL);
   no extra env vars required.
5. (Optional) To enable zernio notifications, set `EPAPHRAS_PUBLIC_URL` to the
   runtime's public base URL. Tap **🔔 Notifications** in the panel to register the
   webhook. Filter decisions are logged to `webhook_events.jsonl` (configurable via
   `EPAPHRAS_WEBHOOK_LOG`); Telegram delivery of matches is not yet implemented.

## Usage

In Telegram, send `/epaphras` to open the panel. Tap a mode to activate it and see
its topics; tap **➕ New mode** to create your own (name + up to two attached
platforms); tap **➕ Add topic** to add topics; tap 🗑 to delete.

## Engine CLI (for testing)

```
python3 engine.py render-modes
python3 engine.py render-topics --mode deep_research
python3 engine.py setmode global_news
python3 engine.py toggle market_meltdown
```

## Tests

```
python3 -m pytest tests/ -v
```

## Manual Telegram dry-run checklist

- [ ] `/epaphras` shows 4 mode buttons; the active mode has `▶️`.
- [ ] Tapping a mode edits the same message to that mode's topics (no new message).
- [ ] Topic buttons show `✅` (on) / `⬜` (off) matching `modes.json`.
- [ ] Tapping a topic flips its mark in place and updates `modes.json`.
- [ ] Tapping **Back** returns to the mode list, with the just-selected mode `▶️`.
- [ ] Switching modes does not change another mode's topic states.
- [ ] A `modes.json.bak` appears after a save.
- [ ] An invalid/edge action shows a `⚠️` notice and leaves `modes.json` intact.
