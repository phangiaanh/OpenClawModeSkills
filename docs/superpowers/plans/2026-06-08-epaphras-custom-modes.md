# Epaphras Custom Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let customers create their own Epaphras modes (name + ≤2 of their live zernio-attached platforms), add/delete their own topics, and toggle them over Telegram — renaming the entry command `/modes` → `/epaphras`.

**Architecture:** A deterministic Python `engine.py` owns all of `modes.json` (modes, topics, and a small wizard state machine). A thin in-process gateway patch routes any `cb_*` button to `engine.py handle-callback` and routes wizard free-text to `engine.py handle-text`, editing the Telegram panel in place — the flaky Gemma LLM is bypassed for both. Platforms come live from `GET zernio.com/api/v1/accounts` via stdlib `urllib`.

**Tech Stack:** Python 3 standard library only (no pip); pytest; Node/grammY gateway patched by a Python script; Telegram inline keyboards.

---

## File Structure

- `engine.py` — **modify.** Add platform-label helper, slug/ID generation, wizard state machine, zernio accounts client, the `handle-callback`/`handle-text`/`render-platforms` commands, and updated `render_modes`/`render_topics` (delete + add buttons). Single module; matches existing layout.
- `templates/modes.default.json` — **modify.** Migrate `platforms` to object shape; add `"wizard": {"step": "idle"}`.
- `tests/test_engine.py` — **modify.** Update two existing render tests; add wizard/platform/delete/migration tests.
- `tests/fixtures/accounts.sample.json` — **create.** The two-account zernio payload (HTTP fixture).
- `tests/fixtures/modes.sample.json` — **unchanged.** Deliberately keeps legacy string platforms to prove backward tolerance.
- `scripts/full_patch_v2.py` — **modify.** Rewrite the callback intercept (generic `cb_*`) and add the text intercept.
- `SKILL.md` — **modify.** Rename `name: epaphras`; document wizard + callbacks.
- `README.md` — **modify.** `/epaphras` usage.
- `callback_sidecar.py` — **delete.** Obsolete.

Conventions to follow (from existing code): `ConfigError` for all invalid input; every command prints exactly one JSON object; render payloads carry both `buttons` and `inline_keyboard` (identical); state commands `save_config` (which writes a `.bak`).

---

## Task 1: Platform label helper with legacy tolerance

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_engine.py`:

```python
def test_platform_label_object_with_handle():
    entry = {"accountId": "abc", "platform": "threads", "handle": "wintermelonely"}
    assert engine.platform_label(entry) == "🧵 threads · @wintermelonely"


def test_platform_label_object_unknown_platform_uses_globe():
    assert engine.platform_label({"platform": "mastodon"}) == "🌐 mastodon"


def test_platform_label_legacy_string_passthrough():
    assert engine.platform_label("LinkedIn") == "LinkedIn"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k platform_label -v`
Expected: FAIL — `AttributeError: module 'engine' has no attribute 'platform_label'`.

- [ ] **Step 3: Implement**

In `engine.py`, after the imports and before `class ConfigError`, add:

```python
PLATFORM_EMOJI = {
    "threads": "🧵", "tiktok": "🎵", "x": "✖️", "twitter": "✖️",
    "instagram": "📸", "youtube": "▶️", "linkedin": "💼", "facebook": "📘",
}
DEFAULT_ICON = "🎯"


def platform_label(entry):
    """Render a platform entry: legacy string passes through; object form is
    formatted as '<emoji> <platform> · @<handle>'."""
    if isinstance(entry, str):
        return entry
    platform = entry.get("platform", "?")
    emoji = PLATFORM_EMOJI.get(platform, "🌐")
    handle = entry.get("handle")
    return f"{emoji} {platform} · @{handle}" if handle else f"{emoji} {platform}"
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k platform_label -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: platform_label helper with legacy-string tolerance"
```

---

## Task 2: Slug + unique-ID generation

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_slugify_basic():
    assert engine._slugify("My Cool Mode!") == "my_cool_mode"


def test_slugify_caps_length_and_fallback():
    assert len(engine._slugify("x" * 50)) <= 18
    assert engine._slugify("!!!") == "mode"


def test_gen_id_unique_suffix():
    existing = {"news", "news_2"}
    assert engine.gen_id(existing, "news") == "news_3"
    assert engine.gen_id(existing, "tech") == "tech"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "slugify or gen_id" -v`
Expected: FAIL — attributes not defined.

- [ ] **Step 3: Implement**

Add `import re` to the top of `engine.py` (with the other stdlib imports), then add:

```python
def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug[:18] or "mode"


def gen_id(existing, base):
    """Return base, or base_2, base_3 … not present in `existing`."""
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "slugify or gen_id" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: slugify + gen_id helpers for short unique ids"
```

---

## Task 3: Wizard state helpers

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_get_wizard_defaults_to_idle():
    data = {"modes": {}}
    assert engine.get_wizard(data)["step"] == "idle"
    assert data["wizard"]["step"] == "idle"  # written through


def test_reset_wizard_clears_state():
    data = {"modes": {}, "wizard": {"step": "await_name", "draft": {"name": "x"}}}
    engine.reset_wizard(data)
    assert data["wizard"] == {"step": "idle"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k wizard -v`
Expected: FAIL — attributes not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def get_wizard(data):
    return data.setdefault("wizard", {"step": "idle"})


def reset_wizard(data):
    data["wizard"] = {"step": "idle"}
    return data
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k wizard -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: wizard state helpers (idle default + reset)"
```

---

## Task 4: Update render_modes (delete + new-mode buttons)

**Files:**
- Modify: `engine.py` (`render_modes`)
- Test: `tests/test_engine.py` (update existing `test_render_modes_marks_active`)

- [ ] **Step 1: Update the existing test to the new layout**

Replace `test_render_modes_marks_active` in `tests/test_engine.py` with:

```python
def test_render_modes_marks_active():
    data = engine.load_config(cfg_path())
    out = engine.render_modes(data)
    assert "buttons" in out and "text" in out and "inline_keyboard" in out
    rows = out["buttons"]
    # 4 mode rows + 1 "New mode" row
    assert len(rows) == 5
    flat = [b for row in rows for b in row]
    active = next(b for b in flat if b["callback_data"] == "cb_setmode:culture_drama")
    assert "▶️" in active["text"]
    inactive = next(b for b in flat if b["callback_data"] == "cb_setmode:deep_research")
    assert "▶️" not in inactive["text"]
    # every mode row has a delete button
    assert any(b["callback_data"] == "cb_delmode:culture_drama" for b in flat)
    # new-mode affordance present
    assert rows[-1][0]["callback_data"] == "cb_newmode"
    assert out["inline_keyboard"] == out["buttons"]
```

Add this helper near the top of the test file (after the `cfg` fixture) so the rewritten test can load the fixture without the fixture-arg:

```python
def cfg_path():
    return FIXTURE
```

Note: `engine.load_config(FIXTURE)` reads the committed fixture directly (read-only); these render tests never write.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_render_modes_marks_active -v`
Expected: FAIL — current code returns 4 rows with single-button rows.

- [ ] **Step 3: Implement**

Replace `render_modes` in `engine.py` with:

```python
def render_modes(data):
    active = data.get("current_active_mode")
    rows = []
    for mode_id, mode in data["modes"].items():
        label = f"{mode.get('icon', DEFAULT_ICON)} {mode['name']}"
        if mode_id == active:
            label += " ▶️"
        rows.append([
            {"text": label, "callback_data": f"cb_setmode:{mode_id}"},
            {"text": "🗑", "callback_data": f"cb_delmode:{mode_id}"},
        ])
    rows.append([{"text": "➕ New mode", "callback_data": "cb_newmode"}])
    return {"text": "Epaphras — Listening Config\nPick a mode:",
            "buttons": rows, "inline_keyboard": rows}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py::test_render_modes_marks_active tests/test_engine.py::test_cli_render_modes -v`
Expected: `test_render_modes_marks_active` PASS. `test_cli_render_modes` asserts `len(out["buttons"]) == 4` — update it now:

In `test_cli_render_modes`, change `assert len(out["buttons"]) == 4` to `assert len(out["buttons"]) == 5`. Re-run: both PASS.

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: render_modes adds per-mode delete + new-mode button"
```

---

## Task 5: Update render_topics (delete + add-topic buttons, platform labels)

**Files:**
- Modify: `engine.py` (`render_topics`)
- Test: `tests/test_engine.py` (update existing `test_render_topics_shows_toggle_marks`)

- [ ] **Step 1: Update the existing test**

Replace `test_render_topics_shows_toggle_marks` with:

```python
def test_render_topics_shows_toggle_marks():
    data = engine.load_config(cfg_path())
    out = engine.render_topics(data, "culture_drama")
    assert out["inline_keyboard"] == out["buttons"]
    flat = [b for row in out["buttons"] for b in row]
    esports = next(b for b in flat if b["callback_data"] == "cb_toggle:esports")
    assert esports["text"].startswith("✅")   # active: true in fixture
    memes = next(b for b in flat if b["callback_data"] == "cb_toggle:viral_memes")
    assert memes["text"].startswith("⬜")      # active: false
    # per-topic delete carries mode + topic id
    assert any(b["callback_data"] == "cb_deltopic:culture_drama:esports" for b in flat)
    # add-topic then back are the last two rows
    assert out["buttons"][-2][0]["callback_data"] == "cb_addtopic:culture_drama"
    assert out["buttons"][-1][0]["callback_data"] == "cb_back"
    # legacy string platforms still render
    assert "TikTok + Threads" in out["text"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_render_topics_shows_toggle_marks -v`
Expected: FAIL — no `cb_deltopic`/`cb_addtopic` buttons yet.

- [ ] **Step 3: Implement**

Replace `render_topics` in `engine.py` with:

```python
def render_topics(data, mode_id=None):
    mode_id = mode_id or data.get("current_active_mode")
    if mode_id is None:
        raise ConfigError("no active mode set")
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    mode = data["modes"][mode_id]
    platforms = " + ".join(platform_label(p) for p in mode["platforms"])
    text = f"{mode.get('icon', DEFAULT_ICON)} {mode['name']}\nPlatforms: {platforms}\nTap a topic to toggle:"
    rows = []
    for topic_id, topic in mode["topics"].items():
        mark = "✅" if topic["active"] else "⬜"
        rows.append([
            {"text": f"{mark} {topic['label']}", "callback_data": f"cb_toggle:{topic_id}"},
            {"text": "🗑", "callback_data": f"cb_deltopic:{mode_id}:{topic_id}"},
        ])
    rows.append([{"text": "➕ Add topic", "callback_data": f"cb_addtopic:{mode_id}"}])
    rows.append([{"text": "⬅️ Back to modes", "callback_data": "cb_back"}])
    return {"text": text, "buttons": rows, "inline_keyboard": rows}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "render_topics" -v`
Expected: PASS (all render_topics tests, incl. the unchanged `defaults_to_active_mode` and `unknown_mode_raises`).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: render_topics adds delete/add-topic buttons + platform labels"
```

---

## Task 6: Zernio accounts client

**Files:**
- Modify: `engine.py`
- Create: `tests/fixtures/accounts.sample.json`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Create the HTTP fixture**

Create `tests/fixtures/accounts.sample.json` with a trimmed version of the real payload (two usable accounts + one disabled to prove filtering):

```json
{
  "accounts": [
    {"_id": "6a2239332b2567671ad7b555", "platform": "threads",
     "username": "wintermelonely", "enabled": true, "isActive": true, "platformStatus": "active"},
    {"_id": "6a224a452b2567671ad96724", "platform": "tiktok",
     "username": "phan.gia.anh7", "enabled": true, "isActive": true, "platformStatus": "active"},
    {"_id": "deadbeef", "platform": "x",
     "username": "disabled_one", "enabled": false, "isActive": true, "platformStatus": "active"}
  ],
  "hasAnalyticsAccess": true
}
```

- [ ] **Step 2: Write failing tests**

```python
import json as _json

ACCOUNTS_FIXTURE = Path(__file__).parent / "fixtures" / "accounts.sample.json"


def _patch_payload(monkeypatch):
    payload = _json.loads(ACCOUNTS_FIXTURE.read_text())
    monkeypatch.setattr(engine, "_get_accounts_payload", lambda token: payload)


def test_fetch_accounts_filters_and_maps(monkeypatch):
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    _patch_payload(monkeypatch)
    accounts = engine.fetch_accounts()
    assert len(accounts) == 2  # disabled one filtered out
    assert accounts[0] == {"accountId": "6a2239332b2567671ad7b555",
                           "platform": "threads", "handle": "wintermelonely"}


def test_fetch_accounts_missing_token_raises(monkeypatch):
    monkeypatch.delenv("ZERNIO_API_TOKEN", raising=False)
    with pytest.raises(engine.ConfigError, match="ZERNIO_API_TOKEN"):
        engine.fetch_accounts()


def test_fetch_accounts_network_error_raises(monkeypatch):
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")

    def boom(token):
        raise OSError("connection refused")

    monkeypatch.setattr(engine, "_get_accounts_payload", boom)
    with pytest.raises(engine.ConfigError, match="accounts fetch failed"):
        engine.fetch_accounts()
```

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k fetch_accounts -v`
Expected: FAIL — `_get_accounts_payload`/`fetch_accounts` not defined.

- [ ] **Step 4: Implement**

Add `import urllib.request`, `import urllib.error`, and `import ssl` to the imports in `engine.py`, then add:

```python
ZERNIO_ACCOUNTS_URL = "https://zernio.com/api/v1/accounts"


def _get_accounts_payload(token):
    """Raw GET of the zernio accounts API. Split out so tests can monkeypatch it."""
    req = urllib.request.Request(
        ZERNIO_ACCOUNTS_URL, headers={"Authorization": f"Bearer {token}"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        return json.loads(resp.read())


def fetch_accounts(token=None):
    """Return usable accounts as [{accountId, platform, handle}]. Raises ConfigError
    on missing token, network/HTTP failure, or invalid JSON."""
    token = token or os.environ.get("ZERNIO_API_TOKEN")
    if not token:
        raise ConfigError("ZERNIO_API_TOKEN not set")
    try:
        payload = _get_accounts_payload(token)
    except json.JSONDecodeError as e:
        raise ConfigError(f"accounts response invalid: {e}")
    except (urllib.error.URLError, OSError) as e:
        raise ConfigError(f"accounts fetch failed: {e}")
    out = []
    for a in payload.get("accounts", []):
        if not (a.get("enabled") and a.get("isActive") and a.get("platformStatus") == "active"):
            continue
        out.append({"accountId": a.get("_id"),
                    "platform": a.get("platform"),
                    "handle": a.get("username")})
    return out
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k fetch_accounts -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_engine.py tests/fixtures/accounts.sample.json
git commit -m "feat: zernio accounts client with filtering + error handling"
```

---

## Task 7: Platform picker rendering + selection

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def _wizard_picking(name="My Mode"):
    return {"current_active_mode": "culture_drama", "modes": {},
            "wizard": {"step": "pick_platforms", "draft": {"name": name, "platforms": []}}}


def test_render_platforms_lists_accounts(monkeypatch):
    _patch_payload(monkeypatch)
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    data = _wizard_picking()
    out = engine.render_platforms(data)
    flat = [b for row in out["buttons"] for b in row]
    assert any(b["callback_data"] == "cb_pickplat:6a2239332b2567671ad7b555" for b in flat)
    assert any(b["callback_data"] == "cb_createmode" for b in flat)
    assert flat[-1]["callback_data"] == "cb_cancel"


def test_render_platforms_token_error_shows_warning(monkeypatch):
    monkeypatch.delenv("ZERNIO_API_TOKEN", raising=False)
    data = _wizard_picking()
    out = engine.render_platforms(data)
    assert out["text"].startswith("⚠️")
    assert out["buttons"][-1][0]["callback_data"] == "cb_cancel"


def test_pick_platform_toggles_and_caps_at_two(monkeypatch):
    _patch_payload(monkeypatch)
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    data = _wizard_picking()
    engine.render_platforms(data)  # caches accounts in wizard
    engine.pick_platform(data, "6a2239332b2567671ad7b555")
    engine.pick_platform(data, "6a224a452b2567671ad96724")
    plats = data["wizard"]["draft"]["platforms"]
    assert {p["accountId"] for p in plats} == {
        "6a2239332b2567671ad7b555", "6a224a452b2567671ad96724"}
    # toggling an already-selected one removes it
    engine.pick_platform(data, "6a2239332b2567671ad7b555")
    assert {p["accountId"] for p in data["wizard"]["draft"]["platforms"]} == {
        "6a224a452b2567671ad96724"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "render_platforms or pick_platform" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def _ensure_accounts(data):
    """Fetch the account list once per wizard and cache it under wizard['accounts']."""
    wiz = get_wizard(data)
    if "accounts" not in wiz:
        wiz["accounts"] = fetch_accounts()  # may raise ConfigError
    return wiz["accounts"]


def render_platforms(data):
    wiz = get_wizard(data)
    draft = wiz.get("draft", {})
    selected = {p["accountId"] for p in draft.get("platforms", [])}
    cancel_only = [[{"text": "✖ Cancel", "callback_data": "cb_cancel"}]]
    try:
        accounts = _ensure_accounts(data)
    except ConfigError as e:
        return {"text": f"⚠️ {e}", "buttons": cancel_only, "inline_keyboard": cancel_only}
    if not accounts:
        return {"text": "⚠️ No usable accounts found.",
                "buttons": cancel_only, "inline_keyboard": cancel_only}
    rows = []
    for a in accounts:
        mark = "✅" if a["accountId"] in selected else "⬜"
        rows.append([{"text": f"{mark} {platform_label(a)}",
                      "callback_data": f"cb_pickplat:{a['accountId']}"}])
    rows.append([{"text": f"✅ Create ({len(selected)}/2)", "callback_data": "cb_createmode"}])
    rows.append([{"text": "✖ Cancel", "callback_data": "cb_cancel"}])
    text = f"New mode: {draft.get('name', '?')}\nPick up to 2 platforms:"
    return {"text": text, "buttons": rows, "inline_keyboard": rows}


def pick_platform(data, account_id):
    wiz = get_wizard(data)
    if wiz.get("step") != "pick_platforms":
        raise ConfigError("not picking platforms")
    plats = wiz.setdefault("draft", {}).setdefault("platforms", [])
    found = next((p for p in plats if p["accountId"] == account_id), None)
    if found:
        plats.remove(found)
        return
    if len(plats) >= 2:
        return  # cap silently; render shows current selection
    accounts = wiz.get("accounts") or fetch_accounts()
    match = next((a for a in accounts if a["accountId"] == account_id), None)
    if match is None:
        raise ConfigError(f"unknown account: {account_id}")
    plats.append(match)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "render_platforms or pick_platform" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: platform picker render + selection (max 2, cached fetch)"
```

---

## Task 8: Create-mode wizard (start → name → create)

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_start_new_mode_enters_await_name():
    data = {"current_active_mode": "x", "modes": {}}
    out = engine.start_new_mode(data)
    assert data["wizard"]["step"] == "await_name"
    assert out["buttons"][-1][0]["callback_data"] == "cb_cancel"


def test_submit_name_advances_to_pick_platforms(monkeypatch):
    _patch_payload(monkeypatch)
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    out = engine.submit_name(data, "  Crypto Watch  ")
    assert data["wizard"]["step"] == "pick_platforms"
    assert data["wizard"]["draft"]["name"] == "Crypto Watch"
    assert any(b["callback_data"] == "cb_createmode"
               for row in out["buttons"] for b in row)


def test_submit_name_rejects_empty():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    out = engine.submit_name(data, "   ")
    assert out["text"].startswith("⚠️")
    assert data["wizard"]["step"] == "await_name"  # stays


def test_create_mode_persists_and_activates(monkeypatch):
    _patch_payload(monkeypatch)
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Crypto Watch")
    engine.pick_platform(data, "6a224a452b2567671ad96724")
    out = engine.create_mode(data)
    assert data["wizard"]["step"] == "idle"
    new_id = data["current_active_mode"]
    assert new_id == "crypto_watch"
    assert data["modes"][new_id]["icon"] == "🎯"
    assert data["modes"][new_id]["topics"] == {}
    assert len(data["modes"][new_id]["platforms"]) == 1
    assert "Crypto Watch" in out["text"]


def test_create_mode_requires_at_least_one_platform(monkeypatch):
    _patch_payload(monkeypatch)
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Empty Mode")
    out = engine.create_mode(data)  # no platforms picked
    assert data["wizard"]["step"] == "pick_platforms"  # stays
    assert "Empty Mode" not in [m.get("name") for m in data["modes"].values()]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "new_mode or submit_name or create_mode" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def start_new_mode(data):
    data["wizard"] = {"step": "await_name", "draft": {"name": "", "platforms": []}}
    rows = [[{"text": "✖ Cancel", "callback_data": "cb_cancel"}]]
    return {"text": "Send a name for the new mode:", "buttons": rows, "inline_keyboard": rows}


def submit_name(data, text):
    wiz = get_wizard(data)
    name = text.strip()
    if not (1 <= len(name) <= 40):
        rows = [[{"text": "✖ Cancel", "callback_data": "cb_cancel"}]]
        return {"text": "⚠️ Name must be 1–40 characters.\nSend a name for the new mode:",
                "buttons": rows, "inline_keyboard": rows}
    wiz["draft"] = {"name": name, "platforms": []}
    wiz.pop("accounts", None)  # force a fresh fetch for this mode
    wiz["step"] = "pick_platforms"
    return render_platforms(data)


def create_mode(data):
    wiz = get_wizard(data)
    draft = wiz.get("draft", {})
    plats = draft.get("platforms", [])
    if not (1 <= len(plats) <= 2):
        return render_platforms(data)  # not ready; stay on picker
    mode_id = gen_id(set(data["modes"].keys()), _slugify(draft["name"]))
    data["modes"][mode_id] = {
        "name": draft["name"], "icon": DEFAULT_ICON,
        "platforms": plats, "topics": {},
    }
    data["current_active_mode"] = mode_id
    reset_wizard(data)
    return render_topics(data, mode_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "new_mode or submit_name or create_mode" -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: create-mode wizard (start, name, create)"
```

---

## Task 9: Add-topic flow

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_start_add_topic_sets_target():
    data = _json.loads(FIXTURE.read_text())
    out = engine.start_add_topic(data, "global_news")
    assert data["wizard"]["step"] == "await_topic"
    assert data["wizard"]["target_mode_id"] == "global_news"
    assert "topic name" in out["text"].lower()


def test_start_add_topic_unknown_mode_raises():
    data = _json.loads(FIXTURE.read_text())
    with pytest.raises(engine.ConfigError):
        engine.start_add_topic(data, "ghost")


def test_submit_topic_adds_active_topic():
    data = _json.loads(FIXTURE.read_text())
    engine.start_add_topic(data, "global_news")
    out = engine.submit_topic(data, "Oil Prices")
    topics = data["modes"]["global_news"]["topics"]
    assert "oil_prices" in topics
    assert topics["oil_prices"] == {"label": "Oil Prices", "active": True}
    assert data["wizard"]["step"] == "idle"
    assert "🚨" in out["text"]


def test_submit_topic_rejects_empty():
    data = _json.loads(FIXTURE.read_text())
    engine.start_add_topic(data, "global_news")
    out = engine.submit_topic(data, "   ")
    assert out["text"].startswith("⚠️")
    assert data["wizard"]["step"] == "await_topic"  # stays
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "add_topic or submit_topic" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def start_add_topic(data, mode_id):
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    wiz = get_wizard(data)
    wiz["step"] = "await_topic"
    wiz["target_mode_id"] = mode_id
    rows = [[{"text": "✖ Cancel", "callback_data": "cb_cancel"}]]
    return {"text": "Send the topic name:", "buttons": rows, "inline_keyboard": rows}


def submit_topic(data, text):
    wiz = get_wizard(data)
    mode_id = wiz.get("target_mode_id")
    if mode_id not in data["modes"]:
        reset_wizard(data)
        raise ConfigError("target mode no longer exists")
    label = text.strip()
    if not (1 <= len(label) <= 40):
        rows = [[{"text": "✖ Cancel", "callback_data": "cb_cancel"}]]
        return {"text": "⚠️ Topic must be 1–40 characters.\nSend the topic name:",
                "buttons": rows, "inline_keyboard": rows}
    topics = data["modes"][mode_id]["topics"]
    topic_id = gen_id(set(topics.keys()), _slugify(label))
    topics[topic_id] = {"label": label, "active": True}
    reset_wizard(data)
    return render_topics(data, mode_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "add_topic or submit_topic" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add-topic flow (await + submit)"
```

---

## Task 10: Delete flows (confirm + perform)

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_confirm_delete_mode_offers_yes_no():
    data = _json.loads(FIXTURE.read_text())
    out = engine.confirm_delete_mode(data, "global_news")
    flat = [b for row in out["buttons"] for b in row]
    assert any(b["callback_data"] == "cb_confirmdel:mode:global_news" for b in flat)
    assert any(b["callback_data"] == "cb_cancel" for b in flat)
    # nothing deleted yet
    assert "global_news" in data["modes"]


def test_perform_delete_mode_removes_and_reassigns_active():
    data = _json.loads(FIXTURE.read_text())  # active = culture_drama
    out = engine.perform_delete(data, "mode:culture_drama")
    assert "culture_drama" not in data["modes"]
    assert data["current_active_mode"] in data["modes"]
    assert out["buttons"][-1][0]["callback_data"] == "cb_newmode"  # Screen 1


def test_perform_delete_topic_removes_only_target():
    data = _json.loads(FIXTURE.read_text())
    engine.perform_delete(data, "topic:global_news:disasters")
    topics = data["modes"]["global_news"]["topics"]
    assert "disasters" not in topics
    assert "market_meltdown" in topics  # sibling intact
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "delete" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def confirm_delete_mode(data, mode_id):
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    name = data["modes"][mode_id]["name"]
    rows = [[
        {"text": "✅ Yes, delete", "callback_data": f"cb_confirmdel:mode:{mode_id}"},
        {"text": "✖ No", "callback_data": "cb_cancel"},
    ]]
    return {"text": f"Delete mode “{name}”?", "buttons": rows, "inline_keyboard": rows}


def confirm_delete_topic(data, mode_id, topic_id):
    if mode_id not in data["modes"] or topic_id not in data["modes"][mode_id]["topics"]:
        raise ConfigError("unknown topic")
    label = data["modes"][mode_id]["topics"][topic_id]["label"]
    rows = [[
        {"text": "✅ Yes, delete", "callback_data": f"cb_confirmdel:topic:{mode_id}:{topic_id}"},
        {"text": "✖ No", "callback_data": "cb_cancel"},
    ]]
    return {"text": f"Delete topic “{label}”?", "buttons": rows, "inline_keyboard": rows}


def perform_delete(data, spec):
    parts = spec.split(":")
    if parts[0] == "mode" and len(parts) == 2:
        mode_id = parts[1]
        data["modes"].pop(mode_id, None)
        if data.get("current_active_mode") == mode_id:
            data["current_active_mode"] = next(iter(data["modes"]), None)
        return render_modes(data)
    if parts[0] == "topic" and len(parts) == 3:
        mode_id, topic_id = parts[1], parts[2]
        if mode_id in data["modes"]:
            data["modes"][mode_id]["topics"].pop(topic_id, None)
            return render_topics(data, mode_id)
        return render_modes(data)
    raise ConfigError(f"bad delete spec: {spec}")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "delete" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: delete flows for modes and topics with confirm"
```

---

## Task 11: handle-callback dispatcher

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_handle_callback_routes_setmode():
    data = _json.loads(FIXTURE.read_text())
    out = engine.handle_callback(data, "cb_setmode:global_news")
    assert data["current_active_mode"] == "global_news"
    assert "🚨" in out["text"]


def test_handle_callback_routes_toggle():
    data = _json.loads(FIXTURE.read_text())
    engine.handle_callback(data, "cb_toggle:viral_memes")
    assert data["modes"]["culture_drama"]["topics"]["viral_memes"]["active"] is True


def test_handle_callback_deltopic_parses_compound_arg():
    data = _json.loads(FIXTURE.read_text())
    out = engine.handle_callback(data, "cb_deltopic:global_news:disasters")
    flat = [b for row in out["buttons"] for b in row]
    assert any(b["callback_data"] == "cb_confirmdel:topic:global_news:disasters" for b in flat)


def test_handle_callback_cancel_resets_to_modes():
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_callback(data, "cb_cancel")
    assert data["wizard"]["step"] == "idle"
    assert out["buttons"][-1][0]["callback_data"] == "cb_newmode"


def test_handle_callback_unknown_raises():
    data = _json.loads(FIXTURE.read_text())
    with pytest.raises(engine.ConfigError):
        engine.handle_callback(data, "cb_bogus:1")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k handle_callback -v`
Expected: FAIL — `handle_callback` not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def cancel(data):
    reset_wizard(data)
    return render_modes(data)


def handle_callback(data, cb):
    if cb == "cb_back":
        return render_modes(data)
    if cb == "cb_newmode":
        return start_new_mode(data)
    if cb == "cb_createmode":
        return create_mode(data)
    if cb == "cb_cancel":
        return cancel(data)
    if ":" not in cb:
        raise ConfigError(f"unknown callback: {cb}")
    verb, arg = cb.split(":", 1)
    if verb == "cb_setmode":
        setmode(data, arg)
        return render_topics(data, arg)
    if verb == "cb_toggle":
        toggle(data, arg)
        return render_topics(data)
    if verb == "cb_pickplat":
        pick_platform(data, arg)
        return render_platforms(data)
    if verb == "cb_addtopic":
        return start_add_topic(data, arg)
    if verb == "cb_delmode":
        return confirm_delete_mode(data, arg)
    if verb == "cb_deltopic":
        mode_id, topic_id = arg.split(":", 1)
        return confirm_delete_topic(data, mode_id, topic_id)
    if verb == "cb_confirmdel":
        return perform_delete(data, arg)
    raise ConfigError(f"unknown callback: {cb}")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k handle_callback -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: handle_callback dispatcher routing all cb_* verbs"
```

---

## Task 12: handle-text dispatcher

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_handle_text_idle_not_handled():
    data = _json.loads(FIXTURE.read_text())
    assert engine.handle_text(data, "hello") == {"handled": False}


def test_handle_text_await_name_handled(monkeypatch):
    _patch_payload(monkeypatch)
    monkeypatch.setenv("ZERNIO_API_TOKEN", "t")
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_text(data, "Crypto Watch")
    assert out["handled"] is True
    assert data["wizard"]["step"] == "pick_platforms"
    assert "buttons" in out and "inline_keyboard" in out


def test_handle_text_slash_command_cancels_and_passes_through():
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_text(data, "/epaphras")
    assert out == {"handled": False}
    assert data["wizard"]["step"] == "idle"  # wizard reset
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k handle_text -v`
Expected: FAIL — `handle_text` not defined.

- [ ] **Step 3: Implement**

Add to `engine.py`:

```python
def handle_text(data, text):
    wiz = get_wizard(data)
    step = wiz.get("step", "idle")
    if step == "idle":
        return {"handled": False}
    if text.strip().startswith("/"):
        reset_wizard(data)
        return {"handled": False}
    if step == "await_name":
        screen = submit_name(data, text)
    elif step == "await_topic":
        screen = submit_topic(data, text)
    else:
        return {"handled": False}  # e.g. pick_platforms: ignore stray text
    return {"handled": True, "text": screen["text"],
            "buttons": screen["buttons"], "inline_keyboard": screen["inline_keyboard"]}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k handle_text -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: handle_text dispatcher (wizard steps + slash-command cancel)"
```

---

## Task 13: CLI wiring + persistence

**Files:**
- Modify: `engine.py` (`main`)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
def test_cli_handle_callback_newmode_persists(cfg):
    rc, out = run_cli(cfg, "handle-callback", "cb_newmode")
    assert rc == 0
    assert engine.load_config(cfg)["wizard"]["step"] == "await_name"


def test_cli_handle_text_idle_not_handled_no_write(cfg):
    before = cfg.read_text()
    rc, out = run_cli(cfg, "handle-text", "random chatter")
    assert rc == 0
    assert out == {"handled": False}
    assert cfg.read_text() == before  # idle => no save


def test_cli_handle_callback_unknown_error_envelope(cfg):
    rc, out = run_cli(cfg, "handle-callback", "cb_bogus:1")
    assert rc == 1
    assert "error" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "cli_handle" -v`
Expected: FAIL — `argparse` rejects the new commands (invalid choice).

- [ ] **Step 3: Implement**

In `engine.py` `main`, extend the `choices` list:

```python
        choices=["render-modes", "render-topics", "setmode", "toggle", "init",
                 "store-msgid", "get-msgid",
                 "handle-callback", "handle-text", "render-platforms"],
```

Then add these branches inside the `try:` block (after the existing `get-msgid` branch, before `return 0`):

```python
        elif args.command == "handle-callback":
            if not args.arg:
                _emit({"error": "handle-callback requires a callback_data argument"})
                return 1
            out = handle_callback(data, args.arg)
            save_config(path, data)
            _emit(out)
        elif args.command == "handle-text":
            text = args.arg or ""
            step = data.get("wizard", {}).get("step", "idle")
            out = handle_text(data, text)
            if step != "idle":
                save_config(path, data)
            _emit(out)
        elif args.command == "render-platforms":
            out = render_platforms(data)
            save_config(path, data)  # persist cached account snapshot
            _emit(out)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k "cli_handle" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (no regressions in the original tests).

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: CLI wiring for handle-callback/handle-text/render-platforms"
```

---

## Task 14: Migrate the seed template

**Files:**
- Modify: `templates/modes.default.json`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write a failing test**

```python
def test_default_template_uses_object_platforms_and_idle_wizard():
    tmpl = _json.loads((Path(__file__).parent.parent / "templates" / "modes.default.json").read_text())
    assert tmpl["wizard"]["step"] == "idle"
    for mode in tmpl["modes"].values():
        for p in mode["platforms"]:
            assert isinstance(p, dict) and "platform" in p
    # object platforms still render via render_topics
    first_id = next(iter(tmpl["modes"]))
    out = engine.render_topics(tmpl, first_id)
    assert "Platforms:" in out["text"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_engine.py -k default_template -v`
Expected: FAIL — current template has string platforms and no `wizard`.

- [ ] **Step 3: Implement**

Replace the entire contents of `templates/modes.default.json` with:

```json
{
  "current_active_mode": "culture_drama",
  "wizard": { "step": "idle" },
  "modes": {
    "deep_research": {
      "name": "Research & Deep Dive",
      "icon": "📚",
      "platforms": [{ "platform": "linkedin" }, { "platform": "reddit" }],
      "topics": {
        "academic_papers": { "label": "Academic Papers", "active": true },
        "tech_forums": { "label": "Technical Forums", "active": false },
        "ai_ml_arch": { "label": "AI/ML Architecture", "active": true },
        "community_sentiment": { "label": "Community Sentiment", "active": false }
      }
    },
    "culture_drama": {
      "name": "Drama & Cultural Pulse",
      "icon": "🎭",
      "platforms": [{ "platform": "tiktok" }, { "platform": "threads" }],
      "topics": {
        "esports": { "label": "Esports Drama", "active": true },
        "vtuber_gossip": { "label": "Vtuber/Streamer Gossip", "active": true },
        "viral_memes": { "label": "Viral Memes", "active": false },
        "cancel_culture": { "label": "Cancel Culture", "active": false }
      }
    },
    "global_news": {
      "name": "Breaking News & Global Alert",
      "icon": "🚨",
      "platforms": [{ "platform": "x" }, { "platform": "threads" }],
      "topics": {
        "geopolitics": { "label": "Geopolitical Crises", "active": false },
        "market_meltdown": { "label": "Market Meltdowns", "active": true },
        "tech_breakouts": { "label": "Tech Breakouts", "active": true },
        "disasters": { "label": "Weather Disasters", "active": false }
      }
    },
    "venture_intel": {
      "name": "Venture & Market Intelligence",
      "icon": "💼",
      "platforms": [{ "platform": "linkedin" }, { "platform": "instagram" }],
      "topics": {
        "vc_funding": { "label": "VC Funding Drops", "active": false },
        "stealth_hires": { "label": "Stealth Engineering Waves", "active": false },
        "ph_launches": { "label": "Product Hunt Launches", "active": true },
        "exec_migration": { "label": "Executive Migrations", "active": false }
      }
    }
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -k default_template -v`
Expected: PASS. Then run the full suite — `python3 -m pytest tests/ -v` — to confirm the legacy-string fixture path (`modes.sample.json`) still passes (proves backward tolerance).

- [ ] **Step 5: Commit**

```bash
git add templates/modes.default.json tests/test_engine.py
git commit -m "feat: migrate seed template to object platforms + idle wizard"
```

---

## Task 15: Gateway patch — generic callback + text intercept

**Files:**
- Modify: `scripts/full_patch_v2.py`

This task edits a Python script that patches the remote Node bundle. It is verified by re-reading the script and by the manual Telegram checklist (Task 17), not by unit tests.

- [ ] **Step 1: Add the old callback marker to the cleanup list**

In `scripts/full_patch_v2.py`, find the cleanup loop:

```python
for _old_marker in ('_EPAPHRAS_FAST_CB_V2', '_registerEpaphrasModesCallbacks'):
```

Change it to also strip the previous in-place callback marker:

```python
for _old_marker in ('_EPAPHRAS_FAST_CB_V2', '_registerEpaphrasModesCallbacks', '_EPAPHRAS_ESM_V3'):
```

- [ ] **Step 2: Replace the callback INJECT with a generic dispatcher**

Find the block that builds `INJECT` for `_EPAPHRAS_ESM_V3` (the one parsing `cb_setmode:`/`cb_toggle:`/`else render-modes`) and the `EMBED_MARKER = '_EPAPHRAS_ESM_V3'` line. Bump the marker and replace the hardcoded routing with a single `handle-callback` pass-through.

Change:

```python
EMBED_MARKER = '_EPAPHRAS_ESM_V3'
```

to:

```python
EMBED_MARKER = '_EPAPHRAS_ESM_V4'
```

Replace the entire `INJECT = ( ... )` string for the callback handler with:

```python
        INJECT = (
            '// PATCH: ' + EMBED_MARKER + ' — generic cb_* intercept (before shouldSkipUpdate)\n'
            '\t\tif (/^cb_/.test(callback.data ?? "")) {\n'
            '\t\t\ttry {\n'
            '\t\t\t\tconst { execFileSync: _es } = await import("child_process");\n'
            '\t\t\t\tconst _d = (callback.data ?? "").trim();\n'
            '\t\t\t\tconst _ENGINE = "' + ENGINE_PATH + '";\n'
            '\t\t\t\tconst _out = JSON.parse(_es("python3", [_ENGINE, "handle-callback", _d], { timeout: 8000 }).toString().trim());\n'
            '\t\t\t\tif (!_out.error) {\n'
            '\t\t\t\t\ttry {\n'
            '\t\t\t\t\t\tawait bot.api.answerCallbackQuery(callback.id).catch(() => {});\n'
            '\t\t\t\t\t\tconst _msg = callback.message;\n'
            '\t\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\t\tawait bot.api.editMessageText(_msg.chat.id, _msg.message_id, _out.text || "", _rm);\n'
            '\t\t\t\t\t} catch(_te) { /* swallow Telegram errors (e.g. message not modified) */ }\n'
            '\t\t\t\t\treturn;\n'
            '\t\t\t\t}\n'
            '\t\t\t} catch(_e) { /* engine error or ESM import fail — fall through */ }\n'
            '\t\t}\n'
            '\t\t// END PATCH: ' + EMBED_MARKER + '\n'
            '\t\t'
        )
```

Leave the surrounding anchor logic (`ANCHOR = 'if (!callback) return;\n\t\tif (shouldSkipUpdate(ctx)) return;'`, the `OLD`/`NEW`/`before`/`after` splice) exactly as-is — it still applies because the injection point is unchanged.

- [ ] **Step 3: Add the text intercept (Patch 3) at the end of the script**

Append to `scripts/full_patch_v2.py` (after the callback patch writes the file). It re-reads the just-written bundle, locates the message handler, and injects a wizard-text intercept:

```python
# ─── Patch 3: pi-embedded text intercept for wizard free-text steps ────────────
TEXT_MARKER = '_EPAPHRAS_TEXT_V1'
with open(EMBEDDED, 'r') as f:
    tsrc = f.read()

if TEXT_MARKER in tsrc:
    print(f"pi-embedded already has {TEXT_MARKER} — skipping text intercept")
else:
    # grammY registers text via bot.on("message", ...) or bot.on("message:text", ...).
    # Inject right after the handler's opening `=> {`.
    import re as _re
    m = _re.search(r'bot\.on\(\s*["\']message(?::text)?["\']\s*,\s*async\s*\(([^)]*)\)\s*=>\s*\{', tsrc)
    if not m:
        print("WARNING: message handler anchor not found — text intercept NOT applied. "
              "Grep the bundle: grep -n 'bot.on(\"message' " + EMBEDDED)
    else:
        ctx_name = m.group(1).strip() or "ctx"
        insert_at = m.end()
        INJECT_TEXT = (
            '\n\t\t// PATCH: ' + TEXT_MARKER + ' — capture wizard free-text\n'
            '\t\ttry {\n'
            '\t\t\tconst _t = ' + ctx_name + '.message?.text;\n'
            '\t\t\tif (typeof _t === "string" && !_t.startsWith("/")) {\n'
            '\t\t\t\tconst _fs = await import("fs");\n'
            '\t\t\t\tconst _MODES = process.env.EPAPHRAS_MODES_FILE || "' + ENGINE_PATH.replace("engine.py", "modes.json") + '";\n'
            '\t\t\t\tlet _step = "idle", _panel = null;\n'
            '\t\t\t\ttry { const _j = JSON.parse(_fs.readFileSync(_MODES, "utf8")); _step = _j.wizard?.step ?? "idle"; _panel = _j.panel_message_id ?? null; } catch (_re) {}\n'
            '\t\t\t\tif (_step !== "idle") {\n'
            '\t\t\t\t\tconst { execFileSync: _es } = await import("child_process");\n'
            '\t\t\t\t\tconst _out = JSON.parse(_es("python3", ["' + ENGINE_PATH + '", "handle-text", _t], { timeout: 8000 }).toString().trim());\n'
            '\t\t\t\t\tif (_out.handled) {\n'
            '\t\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\t\ttry { if (_panel) await bot.api.editMessageText(' + ctx_name + '.chat.id, _panel, _out.text || "", _rm); else await ' + ctx_name + '.reply(_out.text || "", _rm); } catch (_ee) {}\n'
            '\t\t\t\t\t\ttry { await bot.api.deleteMessage(' + ctx_name + '.chat.id, ' + ctx_name + '.message.message_id).catch(() => {}); } catch (_de) {}\n'
            '\t\t\t\t\t\treturn;\n'
            '\t\t\t\t\t}\n'
            '\t\t\t\t}\n'
            '\t\t\t}\n'
            '\t\t} catch (_e) { /* fall through to normal handling */ }\n'
            '\t\t// END PATCH: ' + TEXT_MARKER + '\n'
        )
        tsrc = tsrc[:insert_at] + INJECT_TEXT + tsrc[insert_at:]
        with open(EMBEDDED, 'w') as f:
            f.write(tsrc)
        print(f"Patched pi-embedded with {TEXT_MARKER} (wizard text intercept)")
```

- [ ] **Step 4: Sanity-check the script parses**

Run: `python3 -c "import ast; ast.parse(open('scripts/full_patch_v2.py').read()); print('ok')"`
Expected: `ok` (no syntax error). The real apply runs on the pod per the README's re-apply steps.

- [ ] **Step 5: Commit**

```bash
git add scripts/full_patch_v2.py
git commit -m "feat: gateway patch — generic cb_* dispatcher + wizard text intercept"
```

---

## Task 16: Rename command, update SKILL.md / README, remove sidecar

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Delete: `callback_sidecar.py`

- [ ] **Step 1: Rename the command in SKILL.md frontmatter**

In `SKILL.md`, change the frontmatter `name` and description:

```yaml
---
name: epaphras
description: Create & configure custom Epaphras listening modes/topics over Telegram (inline keyboard + free-text wizard)
user-invocable: true
metadata:
  openclaw: {"requires":{"bins":["python3"]}}
---
```

- [ ] **Step 2: Replace the callback table and add the wizard docs**

In `SKILL.md`, replace the "Handling a button callback" routing table and "Opening the panel (`/modes`)" heading so they reflect the new model. Set the opening heading to ``Opening the panel (`/epaphras`)`` and replace the callback table with:

```markdown
All button taps and wizard text are handled **in-process by the patched gateway**:
- Any `cb_*` callback → `engine.py handle-callback <data>` → edit panel.
- Free text while a wizard is active → `engine.py handle-text <text>` → edit panel.

The LLM only needs to handle `/epaphras`: run `engine.py render-modes`, send with
`action: "send"`, then persist the message id with `store-msgid <messageId>`.

Wizard: ➕ New mode → type a name → pick ≤2 platforms (live from zernio) → ✅ Create.
➕ Add topic → type a topic name. 🗑 → confirm → delete.
```

- [ ] **Step 3: Update README usage**

In `README.md`, replace `/modes` with `/epaphras` in the Usage section and the dry-run checklist heading, and add a line under Install:

```markdown
4. Set `ZERNIO_API_TOKEN` in the gateway environment so the platform picker can
   list the customer's attached accounts (`GET https://zernio.com/api/v1/accounts`).
```

Also change the "Usage" sentence to:

```markdown
In Telegram, send `/epaphras` to open the panel. Tap a mode to activate it and see
its topics; tap **➕ New mode** to create your own (name + up to two attached
platforms); tap **➕ Add topic** to add topics; tap 🗑 to delete.
```

- [ ] **Step 4: Remove the obsolete sidecar**

Run:

```bash
git rm callback_sidecar.py
```

(The gateway in-process handler replaced it; Task 15 keeps callbacks and now text in-process.)

- [ ] **Step 5: Verify nothing references the sidecar**

Run: `grep -rn "callback_sidecar" . --include=*.py --include=*.md`
Expected: no matches (or only this plan/spec mentioning it as removed).

- [ ] **Step 6: Commit**

```bash
git add SKILL.md README.md
git commit -m "docs: rename /modes -> /epaphras, document wizard, remove obsolete sidecar"
```

---

## Task 17: Full regression + manual Telegram dry-run

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS.

- [ ] **Step 2: Manual smoke of the CLI happy path**

```bash
export ZERNIO_API_TOKEN=<real token>
export EPAPHRAS_MODES_FILE=/tmp/epaphras_smoke.json
python3 engine.py init
python3 engine.py handle-callback cb_newmode
python3 engine.py handle-text "Smoke Mode"        # should list your live accounts
python3 engine.py handle-callback cb_pickplat:<an accountId from the previous output>
python3 engine.py handle-callback cb_createmode    # lands on the new mode's topics
python3 engine.py handle-text "First Topic"
python3 engine.py render-modes                     # new mode appears, marked ▶️
```

Expected: each step prints a `{text, buttons, inline_keyboard}` payload; the final `render-modes` includes the new mode with a `cb_delmode:smoke_mode` button and a trailing `cb_newmode` row.

- [ ] **Step 3: Telegram dry-run checklist (after re-applying the gateway patch per README)**

- [ ] `/epaphras` shows the mode list with a **➕ New mode** row and 🗑 on each mode.
- [ ] **➕ New mode** → panel asks for a name; typing a name shows the live account picker.
- [ ] Selecting a 3rd platform is ignored (cap 2); **✅ Create (n/2)** updates the count.
- [ ] **✅ Create** lands on the new mode's topic screen (no topics yet).
- [ ] **➕ Add topic** → typing a name adds it (✅, active) and the typed message is removed.
- [ ] 🗑 on a topic/mode → confirm → deletes; **✖ No** cancels.
- [ ] Sending `/epaphras` mid-wizard cancels the wizard and reopens the panel.
- [ ] Missing/invalid `ZERNIO_API_TOKEN` → the picker shows a ⚠️ panel with **✖ Cancel** (no crash).
- [ ] A `modes.json.bak` appears after changes; normal chat (no wizard) is unaffected by the bot.

- [ ] **Step 4: Commit any checklist notes (optional)**

```bash
git commit --allow-empty -m "test: verified custom-modes flows (engine suite + Telegram dry-run)"
```

---

## Self-Review Notes

- **Spec coverage:** `/epaphras` rename (T16); customer mode+topics (T8/T9); create/delete/toggle (T8/T9/T10, toggle pre-existing); presets as deletable starters (T14 keeps 4, T10 deletes); single config (no schema change); live platforms ≤2 (T6/T7); in-process text capture (T15); `ZERNIO_API_TOKEN` env (T6); default icon 🎯 (T1/T8); delete confirm (T10); legacy-string tolerance (T1, T5, fixture untouched); seed migration (T14); tests + fixtures (throughout); sidecar removal (T16).
- **Type consistency:** platform entry `{accountId, platform, handle}` produced by `fetch_accounts` (T6) and consumed by `pick_platform`/`create_mode` (T7/T8) and `platform_label` (T1); wizard `{step, draft{name,platforms}, target_mode_id, accounts}` written/read consistently (T3, T7–T12); callback verbs in `handle_callback` (T11) match those emitted by `render_modes`/`render_topics`/pickers (T4/T5/T7/T10).
- **No placeholders:** every code step shows complete code; the one discovery step (T15 message-handler anchor) provides a concrete regex + grep fallback and a WARNING path, matching the existing script's style.
