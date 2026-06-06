# OpenClawModeSkills — Epaphras Modes

An OpenClaw skill to configure the Epaphras listening agent over Telegram with a
two-screen inline keyboard. Picks an active **mode** and toggles its **topics**,
persisting to `modes.yaml` (write-through, no Save button).

## Install

1. Place this directory where OpenClaw discovers skills.
2. Install the engine dependency: `python3 -m pip install -r requirements.txt`
3. Ensure `python3` is on PATH (gated via `metadata.openclaw.requires.bins`).
4. (Optional) Set `EPAPHRAS_MODES_FILE` to choose where `modes.yaml` lives.
   Default: alongside `engine.py`. The file is seeded from
   `templates/modes.default.yaml` on first use.

## Usage

In Telegram, send `/modes` to open the panel. Tap a mode to activate it and see
its topics; tap topics to toggle; tap **Back** to return to the mode list.

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

- [ ] `/modes` shows 4 mode buttons; the active mode has `▶️`.
- [ ] Tapping a mode edits the same message to that mode's topics (no new message).
- [ ] Topic buttons show `✅` (on) / `⬜` (off) matching `modes.yaml`.
- [ ] Tapping a topic flips its mark in place and updates `modes.yaml`.
- [ ] Tapping **Back** returns to the mode list, with the just-selected mode `▶️`.
- [ ] Switching modes does not change another mode's topic states.
- [ ] A `modes.yaml.bak` appears after a save.
- [ ] An invalid/edge action shows a `⚠️` notice and leaves `modes.yaml` intact.
