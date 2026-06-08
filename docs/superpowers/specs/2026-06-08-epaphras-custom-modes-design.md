# Epaphras Custom Modes — Design Spec

**Date:** 2026-06-08
**Status:** Approved (design phase)
**Repo:** `OpenClawModeSkills`
**Supersedes parts of:** `2026-06-06-epaphras-modes-design.md` (architecture has since moved
the callback handler in-process via a gateway patch; the `callback_sidecar.py` is obsolete).

## 1. Purpose

Today the Epaphras listening agent is configured over Telegram from a **fixed** set of four
predefined modes and their predefined topics. Customers can only pick from what we defined.
This project makes modes and topics **customer-defined**: a customer creates a mode (a name +
up to two of *their own attached platforms*, pulled live from zernio), adds their own topics
to it, toggles them, and deletes modes/topics — all from the Telegram inline keyboard, with
state persisted to `modes.json`. The opening command is renamed `/modes` → `/epaphras`.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Command name | `/modes` → **`/epaphras`** | Branding. Just the `SKILL.md` `name:` frontmatter. |
| What customers define | **Mode + their own topics** (free text) | Max flexibility; the requested UX. |
| Operations | **Create, delete, toggle** (no rename/edit) | To change, delete & recreate. Leaner. |
| Preset modes | **Kept as deletable starter modes** | Gentle onboarding; the old four seed `modes.json`. |
| Config scope | **Single config per deployment** | One bot, one zernio token, one accounts list, one `modes.json`. Not multi-tenant. |
| Platform source | **Live attached accounts** from `GET zernio.com/api/v1/accounts` | The customer's real connected accounts; `≤2` per mode. |
| Platform fetch | **Live each time the picker opens** | Small list, always current; graceful ⚠️ on failure. |
| Text capture | **In-process gateway patch** on `bot.on("message")` | Deterministic & fast; the Gemma model is too unreliable for wizard steps. |
| Token storage | **`ZERNIO_API_TOKEN` env var** | Matches `EPAPHRAS_MODES_FILE`; keeps the secret out of any file. |
| Custom mode icon | **Default 🎯**, no picker | Keeps the wizard short (YAGNI). |
| Deletes | **One-tap confirm screen** | Prevents accidental data loss. |

## 3. Architecture

Same spine as the live system: a thin **gateway patch** in front of a deterministic
**`engine.py`** that owns all of `modes.json`. The gateway (a grammY bot) is the single Telegram
update consumer; the engine is stateless across calls (all state in `modes.json`). Two extensions:

1. **Generic callback dispatcher.** The current in-process handler hardcodes
   `cb_setmode/cb_toggle/cb_back`. It is broadened to a pass-through: *any* `cb_*` →
   `engine.py handle-callback <data>` → `editMessageText` → `return` (LLM bypassed). All routing
   logic moves into the engine (Python, unit-testable), so **adding verbs never requires
   re-patching the gateway**.
2. **Text-capture intercept** in `bot.on("message")`. Before LLM dispatch, the gateway reads the
   small `modes.json` in JS and checks `wizard.step`. Only if a wizard is pending **and** the text
   is not a `/command` does it shell out to `engine.py handle-text <text>`, edit the stored panel
   message, and `return`. Otherwise it falls through to the LLM untouched — normal chat pays zero
   cost.

The model behind the agent is Gemma/GreenNode, which required extensive gateway patching merely
to emit tool calls; this is the reason both the button and the text fast-paths run in-process
rather than through the LLM.

## 4. Data Model (`modes.json`)

### Platform entries (unified object shape)

```json
"platforms": [
  {"accountId": "6a2239332b2567671ad7b555", "platform": "threads", "handle": "wintermelonely"},
  {"accountId": "6a224a452b2567671ad96724", "platform": "tiktok",  "handle": "phan.gia.anh7"}
]
```

- `accountId` ← account `_id` from the zernio API; `platform` ← `platform`; `handle` ← `username`.
- Render label: `{emoji} {platform} · @{handle}` (e.g. `🧵 threads · @wintermelonely`).
- **Preset** modes use the same shape minus `accountId` (illustrative only). Render and load
  **tolerate the legacy string form** (`["LinkedIn","Reddit"]`) and normalize it on load.

### Mode shape

```json
"<mode_id>": {
  "name": "My Mode",
  "icon": "🎯",
  "platforms": [ ... ],
  "topics": { "<topic_id>": { "label": "...", "active": true } }
}
```

### New top-level wizard state (single in-flight wizard; single user)

```json
"wizard": {
  "step": "idle | await_name | pick_platforms | await_topic",
  "draft": { "name": "...", "platforms": [ ... ] },
  "target_mode_id": "<id>"
}
```

- `target_mode_id` is used by the add-topic flow to target an existing mode.
- IDs are short generated slugs (`m1`, `t1`, …) to stay well under Telegram's 64-byte
  `callback_data` limit; duplicate names get a numeric suffix.

Existing top-level keys (`current_active_mode`, `panel_message_id`, `modes`) are unchanged.

## 5. UX Flows

```
SCREEN 1 — modes
[▶️ Drama & Cultural Pulse] [🗑]
[   Research & Deep Dive  ] [🗑]
[➕ New mode]

SCREEN 2 — <mode> topics   (header: 🧵 threads · @… + 🎵 tiktok · @…)
[✅ Esports Drama] [🗑]
[⬜ Viral Memes ] [🗑]
[➕ Add topic]
[⬅️ Back]
```

**Create-mode wizard**
1. `➕ New mode` → panel: *"Send a name for the new mode."* (`step=await_name`).
2. Customer types a name → engine saves `draft.name`, renders the **platform picker**: live
   accounts as `[⬜ 🧵 threads · @…]` toggles (max 2 selectable), `[✅ Create (n/2)]`, `[✖ Cancel]`
   (`step=pick_platforms`).
3. `✅ Create` → mode created with **0 topics**, lands on its Screen 2.

**Add-topic** — `➕ Add topic` → *"Send the topic name."* (`step=await_topic`, `target_mode_id` set)
→ customer types → topic added, back on Screen 2.

**Delete** — `🗑` on a mode/topic → *"Delete X? [Yes] [No]"* → confirm performs the delete.

**Cancellation** — `✖ Cancel`, `[No]`, or a `/command` sent mid-wizard resets the wizard; a
`/command` additionally falls through so it runs normally.

## 6. `engine.py` Command Surface

The gateway only ever calls the two dispatchers plus the openers; all verb routing lives in Python.

| Command | Effect | Returns |
|---|---|---|
| `render-modes` | read-only | Screen 1 |
| `handle-callback <cb_data>` | route any `cb_*`, persist, advance wizard | `{text, buttons}` |
| `handle-text <text>` | wizard text step; no-op when idle | `{handled, text, buttons}` |
| `store-msgid <id>` / `get-msgid` | panel message id (unchanged) | |
| `init` | seed from template if missing | status |

`handle-callback` routes (all parsed in Python, ≤64-byte safe):

| `callback_data` | Action |
|---|---|
| `cb_setmode:<id>` | activate mode → Screen 2 |
| `cb_toggle:<tid>` | flip topic `active` |
| `cb_back` | → Screen 1 |
| `cb_newmode` | start wizard → `await_name` |
| `cb_pickplat:<accountId>` | toggle platform in draft (enforce max 2) |
| `cb_createmode` | finalize draft → new mode's Screen 2 |
| `cb_addtopic:<mode_id>` | set `target_mode_id` → `await_topic` |
| `cb_delmode:<id>` / `cb_deltopic:<mid>:<tid>` | → confirm screen |
| `cb_confirmdel:<...>` | perform the delete |
| `cb_cancel` | reset wizard / dismiss confirm |

Each command prints one JSON object; on error, exit non-zero with `{ "error": "..." }`.
All commands honor `--file` and `EPAPHRAS_MODES_FILE` (default `<skill_dir>/modes.json`).

### Zernio accounts client

- Stdlib `urllib` (zero external deps), `GET https://zernio.com/api/v1/accounts`,
  `Authorization: Bearer $ZERNIO_API_TOKEN`, 10s timeout.
- Filter to usable accounts: `enabled && isActive && platformStatus == "active"`.
- Extract `_id`, `platform`, `username` per account.
- Emoji map: threads 🧵 · tiktok 🎵 · x ✖️ · instagram 📸 · youtube ▶️ · linkedin 💼 ·
  facebook 📘 · fallback 🌐.
- **Never crashes.** Renders an inline ⚠️ panel + `[✖ Cancel]` for: missing token, network/HTTP
  error, empty/invalid JSON, or zero usable accounts. `modes.json` is left intact.

### Validation

- Name / topic label: trimmed, non-empty, ≤40 chars.
- 1–2 platforms required to create a mode.
- Short unique generated IDs; duplicate name → numeric suffix.

## 7. Gateway Patches (`scripts/full_patch_v2.py`)

- **Patch 1 — `openai-completions.js`** (Gemma native tool-call parsing): **unchanged.**
- **Patch 2 — `pi-embedded-*.js` callback intercept (rewritten):** replace the hardcoded
  `cb_setmode/toggle/back` block with a generic `if (/^cb_/.test(data))` →
  `engine.py handle-callback <data>` → `editMessageText` → `return`. Marker bumped
  (`_EPAPHRAS_ESM_V4`); the script already cleans up older markers and is idempotent.
- **Patch 3 — `pi-embedded-*.js` text intercept (new):** in the message handler, before LLM
  dispatch — read `modes.json` in JS; if `wizard.step !== "idle"` and `text` does not start with
  `/`, call `engine.py handle-text <text>`, edit the stored `panel_message_id`, optionally
  `deleteMessage` the user's typed line, and `return`. Marker `_EPAPHRAS_TEXT_V1`, idempotent,
  anchored in `bot.on("message")`. The exact anchor is located against the live `pi-embedded-*.js`
  at apply time (uses `await import("child_process")` — ESM, like Patch 2).

## 8. Seed Migration & Cleanup

- `templates/modes.default.json`: convert all `platforms` to the object shape (presets omit
  `accountId`); add `"wizard": {"step": "idle"}`. A stale `modes.json` with legacy string
  platforms is normalized on load (render tolerates both during transition).
- `SKILL.md`: rename `name: epaphras`; document the wizard, the callback table, and the
  text-capture behavior.
- `callback_sidecar.py`: **delete** — obsolete since the in-process handler, and now superseded.
- `README.md` and this spec updated.

## 9. Repo Layout

```
OpenClawModeSkills/
  SKILL.md                         # name: epaphras; wizard + callback docs
  engine.py                        # + handle-callback, handle-text, wizard, zernio client
  templates/modes.default.json     # object-shape platforms + wizard idle
  scripts/full_patch_v2.py         # Patch 2 rewritten + Patch 3 added
  tests/
    test_engine.py                 # + wizard / platform / delete / migration tests
    fixtures/accounts.sample.json  # the two-account zernio payload
  README.md
  (callback_sidecar.py removed)
```

`modes.json` remains runtime state, gitignored. `ZERNIO_API_TOKEN` is supplied via the
gateway pod environment and never committed.

## 10. Test Plan (`tests/test_engine.py`, stdlib + monkeypatch)

- Callback routing: each verb → correct mutation & screen.
- Wizard transitions: `newmode → await_name → (text) → pick_platforms → pickplat (max-2
  enforced) → createmode`; new mode has 0 topics and lands on its Screen 2.
- `handle-text`: idle → `handled:false`; `await_name`/`await_topic` → correct write; `/command`
  while pending → cancels, `handled:false`.
- Platform picker: **monkeypatch the HTTP call** with the sample payload → correct labels/ids;
  max-2 cap; token-missing / network-error / empty-accounts → ⚠️ panels, no crash, `modes.json`
  intact.
- Delete + confirm; cancel paths.
- ID/slug generation, duplicate-name suffixing, 64-byte `callback_data` bound.
- Seed migration: legacy string-platform config normalizes; `.bak` written on save.
- Fixture: `tests/fixtures/accounts.sample.json` (the two-account payload).

Manual: a Telegram dry-run checklist (live Telegram is not unit-testable) covering `/epaphras`,
create-mode end to end, add/delete topic, delete mode, and the ⚠️ no-accounts path.
