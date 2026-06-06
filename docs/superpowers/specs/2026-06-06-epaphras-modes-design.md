# Epaphras Modes — Design Spec

**Date:** 2026-06-06
**Status:** Approved (design phase)
**Repo:** `OpenClawModeSkills` (github.com/phangiaanh/OpenClawModeSkills)

## 1. Purpose

An OpenClaw skill that lets the user configure the **Epaphras** listening agent
over Telegram, using a two-screen inline keyboard. The user picks an operational
**mode** and toggles that mode's **topics**. Configuration is persisted to
`modes.yaml`, which a downstream listening agent consumes.

## 2. Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | **Telegram only** | A React dashboard is a separate app, not part of a `SKILL.md`. User interacts via chat bot. |
| Save model | **Write-through, no Save button** | Agent is stateless between button taps; `modes.yaml` is the single source of truth. Most robust. |
| Layout | **Two screens** (mode list → topic toggles) | Less clutter per screen; chosen by user. |
| Implementation | **`SKILL.md` + Python helper engine** | Deterministic YAML round-trip + reliable rendering; keeps `modes.yaml` pristine. |
| Engine language | **Python + ruamel.yaml** | Best comment/key-order-preserving YAML round-trip. |
| Command name | `/modes` | |
| Mode selection semantics | Tapping a mode **activates it** (`current_active_mode`) **and** opens its topics | One concept, least surprising. |

## 3. Architecture

OpenClaw skills are `SKILL.md` instruction files that drive existing tools
(`read`, `write`, `edit`, `message`/`editMessage`). They do not define their own
tools. The agent is **stateless between turns**, so all state lives in `modes.yaml`.

Two cooperating pieces:

### `SKILL.md`
- **`user-invocable: true`** — `/modes` opens the panel.
- **Model-invocable** (default; do NOT set `disable-model-invocation`) — so when a
  button tap arrives as a `callback_data: cb_*` message, the in-context skill
  instructions tell the agent to route it to the engine. This is what makes the
  toggle loop work.
- Contains almost no logic: it maps each callback prefix → one `engine.py` call →
  pipes the engine's JSON output into the `message` (open) or `editMessage`
  (subsequent taps) tool action.

### `engine.py`
- Deterministic Python (ruamel.yaml). Owns all `modes.yaml` reads/mutations and
  emits the exact Telegram payload. Comment- and key-order-preserving.

## 4. Components & Interfaces

### `engine.py` CLI

Each command prints **one JSON object** to stdout:

```json
{ "text": "<header/body text>", "buttons": [[{ "text": "...", "callback_data": "..." }]] }
```

| Command | Effect | Output |
|---------|--------|--------|
| `render-modes` | none (read-only) | Screen 1: mode list, active mode marked `▶️` |
| `render-topics [--mode <id>]` | none (read-only) | Screen 2: topics for given/active mode |
| `setmode <mode_id>` | set `current_active_mode`, write | that mode's Screen 2 |
| `toggle <topic_id>` | flip topic's `active` within `current_active_mode`, write | Screen 2 |
| `init` | seed `modes.yaml` from `templates/modes.default.yaml` if missing | (status) |

- All commands accept `--file` and honor `EPAPHRAS_MODES_FILE` env
  (default: `<skill_dir>/modes.yaml`).
- On error: exit non-zero, print `{ "error": "<message>" }`.

### Callback protocol (`callback_data`, all ≤ 64 bytes)

- `cb_setmode:<mode_id>` — e.g. `cb_setmode:culture_drama`
- `cb_toggle:<topic_id>` — e.g. `cb_toggle:esports`
- `cb_back` — return to mode list

## 5. Data Flow

1. `/modes` → agent runs `engine.py render-modes` → **sends a new message** with
   Screen 1 buttons.
2. Tap a mode → `callback_data: cb_setmode:culture_drama` → agent runs
   `engine.py setmode culture_drama` → **edits the same message** to Screen 2.
3. Tap a topic → `cb_toggle:esports` → `engine.py toggle esports` → edits the
   message; that one button flips `✅`/`⬜`.
4. Tap Back → `cb_back` → `engine.py render-modes` → edits message back to Screen 1.

**Visuals:** `▶️` marks the active mode on Screen 1; `✅` = topic on / `⬜` = topic
off on Screen 2. Each mode independently retains its own topic states.

### Screen sketches

```
SCREEN 1 — pick a mode
[📚 Research & Deep Dive]
[🎭 Drama & Cultural Pulse ▶️]
[🚨 Breaking News]
[💼 Venture & Market Intel]

SCREEN 2 — 🎭 Drama topics
[✅ Esports Drama]
[✅ Vtuber/Streamer Gossip]
[⬜ Viral Memes]
[⬜ Cancel Culture]
[⬅️ Back to modes]
```

## 6. Error Handling

- **Missing file** → auto-seed from `templates/modes.default.yaml`.
- **Malformed YAML** → **fail loudly, never overwrite**; agent reports "config
  unreadable" rather than clobbering. A `.bak` is written before each successful
  save.
- **Unknown mode/topic id** → engine exits non-zero with `{ "error": "..." }`;
  agent shows a brief notice and re-renders the last good screen.
- **Stale/rapid taps** → read-modify-write on each call; last tap wins (acceptable
  for a single user).

## 7. Repo Layout

```
OpenClawModeSkills/
  SKILL.md
  engine.py
  templates/modes.default.yaml   # seed (provided template values)
  requirements.txt               # ruamel.yaml
  tests/
    test_engine.py
    fixtures/modes.sample.yaml
  README.md
```

The live `modes.yaml` is created at runtime from the template; it is **not
committed** (user state). Add it to `.gitignore`.

## 8. SKILL.md Frontmatter

```yaml
---
name: modes
description: Configure Epaphras listening modes & topics over Telegram via an inline keyboard
user-invocable: true
metadata:
  openclaw: {"requires":{"bins":["python3"]}}
---
```

## 9. Testing

Unit tests on `engine.py`:
- `render-modes` / `render-topics` produce the correct payload shape and button set.
- `setmode` switches `current_active_mode` without disturbing other modes' topics.
- `toggle` flips the right topic only.
- YAML round-trip preserves comments and key order (ruamel).
- Unknown-id error path returns non-zero + `{ "error": ... }`.
- Auto-seed when file missing.
- `.bak` created on save.

Fixture: `tests/fixtures/modes.sample.yaml` (the provided template values).

Manual: a documented Telegram dry-run checklist (live Telegram is not unit-testable).

## 10. Seed Data (`templates/modes.default.yaml`)

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
