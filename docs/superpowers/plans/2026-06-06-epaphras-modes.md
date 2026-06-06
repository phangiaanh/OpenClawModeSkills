# Epaphras Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an OpenClaw skill (`SKILL.md` + Python engine) that configures the Epaphras listening agent over Telegram via a two-screen inline keyboard, persisting to `modes.yaml`.

**Architecture:** `SKILL.md` is injected into the agent's system prompt; it maps each Telegram callback (`cb_setmode:`, `cb_toggle:`, `cb_back`) to a single `engine.py` subcommand and pipes the engine's JSON payload into the `message`/`editMessage` tool. `engine.py` (ruamel.yaml) owns every `modes.yaml` read/mutation and emits the exact `{text, buttons}` payload. Write-through: every mutating tap reads, mutates, writes (with `.bak`), and re-renders.

**Tech Stack:** Python 3, ruamel.yaml, pytest. OpenClaw skill format (`SKILL.md` frontmatter), Telegram inline keyboards.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `engine.py` | All `modes.yaml` IO, mutation, and Telegram-payload rendering. Importable functions + CLI. |
| `templates/modes.default.yaml` | Seed config copied to live `modes.yaml` on first run. |
| `requirements.txt` | `ruamel.yaml` dependency. |
| `tests/fixtures/modes.sample.yaml` | Test fixture (copy of seed). |
| `tests/test_engine.py` | Unit tests for engine functions + CLI. |
| `SKILL.md` | Skill instructions: frontmatter + callback→engine→tool mapping. |
| `README.md` | Install, config, manual Telegram dry-run checklist. |
| `.gitignore` | Ignore runtime `modes.yaml`, `*.bak`, `__pycache__`. |

All paths are relative to the repo root `OpenClawModeSkills/`.

---

## Task 1: Scaffolding — template, deps, gitignore

**Files:**
- Create: `templates/modes.default.yaml`
- Create: `tests/fixtures/modes.sample.yaml`
- Create: `requirements.txt`
- Create: `.gitignore`

- [ ] **Step 1: Create the seed template**

Create `templates/modes.default.yaml`:

```yaml
# Active system envelope state managed by OpenClaw UI skill handler
current_active_mode: "culture_drama"  # deep_research | culture_drama | global_news | venture_intel

modes:
  deep_research:
    name: "Research & Deep Dive"
    icon: "📚"
    platforms: ["LinkedIn", "Reddit"]
    topics:
      academic_papers: { label: "Academic Papers", active: true }
      tech_forums: { label: "Technical Forums", active: false }
      ai_ml_arch: { label: "AI/ML Architecture", active: true }
      community_sentiment: { label: "Community Sentiment", active: false }

  culture_drama:
    name: "Drama & Cultural Pulse"
    icon: "🎭"
    platforms: ["TikTok", "Threads"]
    topics:
      esports: { label: "Esports Drama", active: true }
      vtuber_gossip: { label: "Vtuber/Streamer Gossip", active: true }
      viral_memes: { label: "Viral Memes", active: false }
      cancel_culture: { label: "Cancel Culture", active: false }

  global_news:
    name: "Breaking News & Global Alert"
    icon: "🚨"
    platforms: ["X", "Telegram"]
    topics:
      geopolitics: { label: "Geopolitical Crises", active: false }
      market_meltdown: { label: "Market Meltdowns", active: true }
      tech_breakouts: { label: "Tech Breakouts", active: true }
      disasters: { label: "Weather Disasters", active: false }

  venture_intel:
    name: "Venture & Market Intelligence"
    icon: "💼"
    platforms: ["LinkedIn", "Product Hunt"]
    topics:
      vc_funding: { label: "VC Funding Drops", active: false }
      stealth_hires: { label: "Stealth Engineering Waves", active: false }
      ph_launches: { label: "Product Hunt Launches", active: true }
      exec_migration: { label: "Executive Migrations", active: false }
```

- [ ] **Step 2: Copy it as the test fixture**

Run: `mkdir -p tests/fixtures && cp templates/modes.default.yaml tests/fixtures/modes.sample.yaml`

- [ ] **Step 3: Create `requirements.txt`**

```
ruamel.yaml>=0.18
pytest>=8.0
```

- [ ] **Step 4: Create `.gitignore`**

```
modes.yaml
*.bak
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 5: Install deps and verify the template parses**

Run: `python3 -m pip install -r requirements.txt && python3 -c "from ruamel.yaml import YAML; YAML().load(open('templates/modes.default.yaml'))"`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add templates/modes.default.yaml tests/fixtures/modes.sample.yaml requirements.txt .gitignore
git commit -m "chore: scaffold Epaphras modes skill (template, deps, gitignore)"
```

---

## Task 2: Config IO — resolve path, seed, load, save with backup

**Files:**
- Create: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine.py`:

```python
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import engine

FIXTURE = Path(__file__).parent / "fixtures" / "modes.sample.yaml"


@pytest.fixture
def cfg(tmp_path):
    """A live config file seeded from the fixture, returned as a path."""
    dst = tmp_path / "modes.yaml"
    dst.write_text(FIXTURE.read_text())
    return dst


def test_resolve_path_prefers_arg(tmp_path):
    p = tmp_path / "x.yaml"
    assert engine.resolve_path(str(p)) == p


def test_resolve_path_uses_env(tmp_path, monkeypatch):
    p = tmp_path / "env.yaml"
    monkeypatch.setenv("EPAPHRAS_MODES_FILE", str(p))
    assert engine.resolve_path(None) == p


def test_ensure_file_seeds_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DEFAULT_TEMPLATE", FIXTURE)
    target = tmp_path / "nested" / "modes.yaml"
    engine.ensure_file(target)
    assert target.exists()
    assert "culture_drama" in target.read_text()


def test_load_config_reads_modes(cfg):
    data = engine.load_config(cfg)
    assert data["current_active_mode"] == "culture_drama"
    assert "deep_research" in data["modes"]


def test_load_config_rejects_malformed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("current_active_mode: [unclosed\n")
    with pytest.raises(engine.ConfigError):
        engine.load_config(bad)


def test_save_config_writes_backup(cfg):
    data = engine.load_config(cfg)
    engine.save_config(cfg, data)
    assert Path(str(cfg) + ".bak").exists()


def test_save_config_preserves_comments_and_order(cfg):
    data = engine.load_config(cfg)
    engine.save_config(cfg, data)
    text = cfg.read_text()
    assert text.startswith("# Active system envelope state")
    # key order preserved: deep_research appears before culture_drama
    assert text.index("deep_research") < text.index("culture_drama")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine'` (or attribute errors).

- [ ] **Step 3: Write minimal `engine.py` IO layer**

Create `engine.py`:

```python
"""Epaphras Modes engine: modes.yaml IO, mutation, and Telegram payload rendering."""
import json
import os
import shutil
import sys
from pathlib import Path

from ruamel.yaml import YAML

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "modes.default.yaml"
DEFAULT_FILE = Path(__file__).parent / "modes.yaml"

_yaml = YAML()
_yaml.preserve_quotes = True


class ConfigError(Exception):
    """Raised for any unreadable/invalid config or unknown id."""


def resolve_path(arg=None):
    if arg:
        return Path(arg)
    env = os.environ.get("EPAPHRAS_MODES_FILE")
    if env:
        return Path(env)
    return DEFAULT_FILE


def ensure_file(path, template=None):
    path = Path(path)
    if not path.exists():
        src = Path(template) if template else DEFAULT_TEMPLATE
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, path)
    return path


def load_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = _yaml.load(f)
    except Exception as e:  # ruamel raises various parse errors
        raise ConfigError(f"config unreadable: {e}")
    if not data or "modes" not in data:
        raise ConfigError("config missing 'modes'")
    return data


def save_config(path, data):
    path = Path(path)
    if path.exists():
        shutil.copyfile(path, Path(str(path) + ".bak"))
    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add config IO (resolve, seed, load, save+backup)"
```

---

## Task 3: Rendering — render_modes and render_topics

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_engine.py`:

```python
def test_render_modes_marks_active(cfg):
    data = engine.load_config(cfg)
    out = engine.render_modes(data)
    assert "buttons" in out and "text" in out
    rows = out["buttons"]
    assert len(rows) == 4
    flat = [b for row in rows for b in row]
    active = next(b for b in flat if b["callback_data"] == "cb_setmode:culture_drama")
    assert "▶️" in active["text"]
    inactive = next(b for b in flat if b["callback_data"] == "cb_setmode:deep_research")
    assert "▶️" not in inactive["text"]


def test_render_topics_shows_toggle_marks(cfg):
    data = engine.load_config(cfg)
    out = engine.render_topics(data, "culture_drama")
    flat = [b for row in out["buttons"] for b in row]
    esports = next(b for b in flat if b["callback_data"] == "cb_toggle:esports")
    assert esports["text"].startswith("✅")  # active: true in fixture
    memes = next(b for b in flat if b["callback_data"] == "cb_toggle:viral_memes")
    assert memes["text"].startswith("⬜")  # active: false
    back = flat[-1]
    assert back["callback_data"] == "cb_back"
    assert "TikTok + Threads" in out["text"]


def test_render_topics_defaults_to_active_mode(cfg):
    data = engine.load_config(cfg)
    out = engine.render_topics(data)  # no mode arg -> current_active_mode
    assert "🎭" in out["text"]


def test_render_topics_unknown_mode_raises(cfg):
    data = engine.load_config(cfg)
    with pytest.raises(engine.ConfigError):
        engine.render_topics(data, "nope")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k render -v`
Expected: FAIL — `AttributeError: module 'engine' has no attribute 'render_modes'`.

- [ ] **Step 3: Implement rendering in `engine.py`**

Add to `engine.py` (after `save_config`):

```python
def render_modes(data):
    active = data.get("current_active_mode")
    buttons = []
    for mode_id, mode in data["modes"].items():
        label = f"{mode['icon']} {mode['name']}"
        if mode_id == active:
            label += " ▶️"
        buttons.append([{"text": label, "callback_data": f"cb_setmode:{mode_id}"}])
    return {"text": "Epaphras — Listening Config\nPick a mode:", "buttons": buttons}


def render_topics(data, mode_id=None):
    mode_id = mode_id or data.get("current_active_mode")
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    mode = data["modes"][mode_id]
    platforms = " + ".join(mode["platforms"])
    text = f"{mode['icon']} {mode['name']}\nPlatforms: {platforms}\nTap a topic to toggle:"
    buttons = []
    for topic_id, topic in mode["topics"].items():
        mark = "✅" if topic["active"] else "⬜"
        buttons.append([{"text": f"{mark} {topic['label']}", "callback_data": f"cb_toggle:{topic_id}"}])
    buttons.append([{"text": "⬅️ Back to modes", "callback_data": "cb_back"}])
    return {"text": text, "buttons": buttons}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: render mode-list and topic-toggle keyboards"
```

---

## Task 4: Mutations — setmode and toggle

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_engine.py`:

```python
def test_setmode_changes_active_only(cfg):
    data = engine.load_config(cfg)
    engine.setmode(data, "global_news")
    assert data["current_active_mode"] == "global_news"
    # other modes' topics untouched
    assert data["modes"]["culture_drama"]["topics"]["esports"]["active"] is True


def test_setmode_unknown_raises(cfg):
    data = engine.load_config(cfg)
    with pytest.raises(engine.ConfigError):
        engine.setmode(data, "ghost")


def test_toggle_flips_only_target_in_active_mode(cfg):
    data = engine.load_config(cfg)  # active = culture_drama
    engine.toggle(data, "viral_memes")  # was False
    assert data["modes"]["culture_drama"]["topics"]["viral_memes"]["active"] is True
    # sibling unchanged
    assert data["modes"]["culture_drama"]["topics"]["esports"]["active"] is True


def test_toggle_unknown_topic_raises(cfg):
    data = engine.load_config(cfg)
    with pytest.raises(engine.ConfigError):
        engine.toggle(data, "not_a_topic")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k "setmode or toggle" -v`
Expected: FAIL — `AttributeError: ... 'setmode'`.

- [ ] **Step 3: Implement mutations in `engine.py`**

Add to `engine.py`:

```python
def setmode(data, mode_id):
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    data["current_active_mode"] = mode_id
    return data


def toggle(data, topic_id):
    mode_id = data["current_active_mode"]
    topics = data["modes"][mode_id]["topics"]
    if topic_id not in topics:
        raise ConfigError(f"unknown topic: {topic_id} in mode {mode_id}")
    topics[topic_id]["active"] = not bool(topics[topic_id]["active"])
    return data
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add setmode and toggle mutations"
```

---

## Task 5: CLI dispatch + error envelope

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_engine.py`:

```python
def run_cli(cfg, *args):
    """Invoke engine.py as a subprocess against cfg; return (rc, parsed_json)."""
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "engine.py"), *args, "--file", str(cfg)],
        capture_output=True, text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_cli_render_modes(cfg):
    rc, out = run_cli(cfg, "render-modes")
    assert rc == 0
    assert len(out["buttons"]) == 4


def test_cli_setmode_persists_and_returns_topics(cfg):
    rc, out = run_cli(cfg, "setmode", "global_news")
    assert rc == 0
    assert "🚨" in out["text"]
    assert engine.load_config(cfg)["current_active_mode"] == "global_news"


def test_cli_toggle_persists(cfg):
    rc, out = run_cli(cfg, "toggle", "viral_memes")
    assert rc == 0
    assert engine.load_config(cfg)["modes"]["culture_drama"]["topics"]["viral_memes"]["active"] is True


def test_cli_render_topics_with_mode_flag(cfg):
    rc, out = run_cli(cfg, "render-topics", "--mode", "deep_research")
    assert rc == 0
    assert "📚" in out["text"]


def test_cli_unknown_id_returns_error_envelope(cfg):
    rc, out = run_cli(cfg, "setmode", "ghost")
    assert rc == 1
    assert "error" in out


def test_cli_init_seeds(tmp_path):
    target = tmp_path / "fresh.yaml"
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "engine.py"), "init", "--file", str(target)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert target.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_engine.py -k cli -v`
Expected: FAIL — `json.decoder.JSONDecodeError` (no stdout) or argparse usage error, because `main` does not exist yet.

- [ ] **Step 3: Implement the CLI in `engine.py`**

Add to the end of `engine.py`:

```python
def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Epaphras Modes engine")
    parser.add_argument(
        "command",
        choices=["render-modes", "render-topics", "setmode", "toggle", "init"],
    )
    parser.add_argument("arg", nargs="?", help="mode_id or topic_id")
    parser.add_argument("--file", help="path to modes.yaml")
    parser.add_argument("--mode", help="mode id for render-topics")
    args = parser.parse_args(argv)

    path = resolve_path(args.file)
    try:
        if args.command == "init":
            ensure_file(path)
            _emit({"text": f"initialized {path}", "buttons": []})
            return 0

        ensure_file(path)
        data = load_config(path)

        if args.command == "render-modes":
            _emit(render_modes(data))
        elif args.command == "render-topics":
            _emit(render_topics(data, args.mode))
        elif args.command == "setmode":
            setmode(data, args.arg)
            save_config(path, data)
            _emit(render_topics(data, args.arg))
        elif args.command == "toggle":
            toggle(data, args.arg)
            save_config(path, data)
            _emit(render_topics(data))
        return 0
    except ConfigError as e:
        _emit({"error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS (all tests, ~21).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add CLI dispatch with JSON error envelope"
```

---

## Task 6: Author SKILL.md

**Files:**
- Create: `SKILL.md`

- [ ] **Step 1: Write `SKILL.md`**

Create `SKILL.md`:

````markdown
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
````

- [ ] **Step 2: Verify the frontmatter parses and the engine path resolves**

Run: `python3 -c "from ruamel.yaml import YAML; import io; d=open('SKILL.md').read().split('---')[1]; print(YAML().load(io.StringIO(d))['name'])"`
Expected: prints `modes`.

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "feat: add SKILL.md (callback routing + engine orchestration)"
```

---

## Task 7: README + manual dry-run checklist

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write `README.md`**

Replace `README.md` with:

````markdown
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
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README install/usage + Telegram dry-run checklist"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** Tasks map to spec §§3–10 — IO/seed/backup (T2), rendering (T3), mutations (T4), CLI+error envelope (T5), SKILL.md frontmatter & callback routing (T6), README+dry-run (T7), seed template (T1).
- **Type consistency:** Engine functions used identically across tasks: `resolve_path`, `ensure_file`, `load_config`, `save_config`, `render_modes`, `render_topics(data, mode_id=None)`, `setmode(data, mode_id)`, `toggle(data, topic_id)`, `ConfigError`, `main`.
- **Edit semantics caveat:** The SKILL.md assumes OpenClaw's `editMessage` targets the message a callback originated from. If the runtime requires an explicit message id, the implementer should capture it from the callback context — verify against the live OpenClaw Telegram channel during the dry-run.
