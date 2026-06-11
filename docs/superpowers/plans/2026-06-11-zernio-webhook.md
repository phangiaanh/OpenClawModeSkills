# Zernio Webhook Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register a zernio webhook from the Epaphras Telegram config, receive its event deliveries on the OpenClaw runtime, filter each delivery against the active mode's topics/platforms, and append the filter decision to a log file (no Telegram push yet).

**Architecture:** A thin JS receiver (gateway patch) verifies the HMAC signature, dedups, and acks `200` fast, then shells out to `engine.py handle-webhook <payload>` which holds all topic/platform matching and logging logic in Python. Registration is a generalized MCP client in `engine.py` (`_mcp_call`) plus `webhook-{status,enable,disable,sync}` commands, surfaced via a `🔔 Notifications` button on the Telegram panel.

**Tech Stack:** Python 3 standard library only (`urllib`, `ssl`, `hmac`/`secrets`, `json`, `ast`, `datetime`), pytest, and a Python-authored JS patch (`scripts/full_patch_v2.py`) against the grammY/`pi-embedded` OpenClaw runtime.

**Spec:** `docs/superpowers/specs/2026-06-11-zernio-webhook-design.md`

---

## File structure

- **Modify `engine.py`** — generalize the MCP client; add webhook config helpers, registration commands, the event matcher + logger, the `cb_notif` callback, the `render_modes` notifications button, the post-mutation sync hook, and CLI wiring. All new logic lives here so it is unit-testable.
- **Modify `scripts/full_patch_v2.py`** — add Patch 4: mount `POST /zernio/webhook` on the OpenClaw runtime HTTP server (HMAC verify → dedup → ack → call engine).
- **Modify `templates/modes.default.json`** — add a default disabled `webhook` block.
- **Modify `.gitignore`** — ignore `webhook_events.jsonl`.
- **Modify `SKILL.md` / `README.md`** — document the notifications button, the env vars, and the log file.
- **Create `tests/fixtures/comment_received.sample.json`** — a sample delivery for matcher tests.
- **Modify `tests/test_engine.py`** — tests for every new Python function.

**Constants used across tasks (define once, in Task 1 / Task 2):**
- `DEFAULT_MCP_GATEWAY_URL = "https://gw-watermelon-111735.agentbase-gateway.aiplatform.vngcloud.vn/zernio"`
- `WEBHOOK_EVENTS = ["comment.received", "message.received", "reaction.received", "review.new", "lead.received", "conversation.started"]`
- `WEBHOOK_NAME = "Epaphras"`
- `TEXT_KEYS = {"text", "body", "content", "message", "caption", "title", "comment", "question", "name", "subject"}`

---

## Task 1: Generalize the MCP client (`_mcp_call`)

Refactor the hardcoded accounts-only MCP call into a reusable `_mcp_call(name, arguments)` and make the gateway URL env-configurable (fixes the `gw-zernio-53461` drift). The existing `_get_accounts_payload` becomes a thin wrapper so current tests keep passing.

**Files:**
- Modify: `engine.py:57-87` (replace `MCP_GATEWAY_URL`, `_MCP_BODY`, `_get_accounts_payload`)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:

```python
def test_mcp_call_parses_sse_repr(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return (
            b"event: message\n"
            b"data: {\"jsonrpc\": \"2.0\", \"id\": 2, \"result\": "
            b"{\"content\": [{\"type\": \"text\", \"text\": \"{'webhooks': []}\"}]}}\n\n"
        )

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("EPAPHRAS_MCP_GATEWAY_URL", "https://gw.example/zernio")
    out = engine._mcp_call("webhooks_get_webhook_settings", {})
    assert out == {"webhooks": []}
    assert captured["url"] == "https://gw.example/zernio"
    assert captured["body"]["params"]["name"] == "webhooks_get_webhook_settings"
    assert captured["body"]["params"]["arguments"] == {}


def test_mcp_call_raises_on_error_envelope(monkeypatch):
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'data: {"error": "Invalid API key"}\n\n'

    monkeypatch.setattr(engine.urllib.request, "urlopen",
                        lambda req, timeout=None, context=None: FakeResp())
    with pytest.raises(engine.ConfigError, match="mcp error"):
        engine._mcp_call("webhooks_get_webhook_settings", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_mcp_call_parses_sse_repr -v`
Expected: FAIL with `AttributeError: module 'engine' has no attribute '_mcp_call'`

- [ ] **Step 3: Write minimal implementation**

In `engine.py`, replace the block at lines 57-87 (`MCP_GATEWAY_URL`, `_MCP_BODY`, `_get_accounts_payload`) with:

```python
DEFAULT_MCP_GATEWAY_URL = "https://gw-watermelon-111735.agentbase-gateway.aiplatform.vngcloud.vn/zernio"


def _mcp_call(name, arguments=None):
    """Call a tool on the zernio MCP gateway and return the parsed result.

    The gateway returns SSE ("event: message\\ndata: {...}") whose result text is
    a Python repr (None/True/False), not JSON. Split out so tests can monkeypatch
    urllib. Raises ConfigError on a JSON-RPC error envelope.
    """
    url = os.environ.get("EPAPHRAS_MCP_GATEWAY_URL", DEFAULT_MCP_GATEWAY_URL)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": "curl/8.0.0"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        raw = resp.read().decode()
    envelope = None
    for line in raw.splitlines():
        if line.startswith("data: "):
            envelope = json.loads(line[6:])
            break
    if envelope is None:
        envelope = json.loads(raw)
    if "error" in envelope:
        raise ConfigError(f"mcp error: {envelope['error']}")
    return ast.literal_eval(envelope["result"]["content"][0]["text"])


def _get_accounts_payload():
    """POST to the zernio MCP gateway for the account list."""
    return _mcp_call("accounts_list_accounts", {})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py::test_mcp_call_parses_sse_repr tests/test_engine.py::test_mcp_call_raises_on_error_envelope tests/test_engine.py::test_fetch_accounts_filters_and_maps -v`
Expected: PASS (3 passed) — the existing `test_fetch_accounts_*` still passes because it monkeypatches `_get_accounts_payload`.

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "refactor: generalize MCP client into _mcp_call with env-configurable gateway"
```

---

## Task 2: Webhook config helpers (block, secret, URL, events)

Add the `webhook` block accessor, secret generation, the receiver URL builder, and the events constant.

**Files:**
- Modify: `engine.py` (add after `_get_accounts_payload`, before `fetch_accounts`)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_webhook_config_defaults(cfg):
    data = engine.load_config(cfg)
    wh = engine.webhook_config(data)
    assert wh == {"enabled": False, "id": None, "secret": None,
                  "url": None, "events": [], "synced_at": None}
    assert data["webhook"] is wh  # installed onto data


def test_gen_secret_is_64_hex_chars():
    s = engine._gen_secret()
    assert len(s) == 64 and all(c in "0123456789abcdef" for c in s)
    assert engine._gen_secret() != s  # random


def test_webhook_url_appends_path(monkeypatch):
    monkeypatch.setenv("EPAPHRAS_PUBLIC_URL", "https://host.example/")
    assert engine.webhook_url() == "https://host.example/zernio/webhook"


def test_webhook_url_missing_env_raises(monkeypatch):
    monkeypatch.delenv("EPAPHRAS_PUBLIC_URL", raising=False)
    with pytest.raises(engine.ConfigError, match="EPAPHRAS_PUBLIC_URL"):
        engine.webhook_url()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_webhook_config_defaults -v`
Expected: FAIL with `AttributeError: module 'engine' has no attribute 'webhook_config'`

- [ ] **Step 3: Write minimal implementation**

Add `import secrets` to the imports at the top of `engine.py` (alongside the existing stdlib imports). Then add these functions and the events constant after `_get_accounts_payload`:

```python
WEBHOOK_EVENTS = ["comment.received", "message.received", "reaction.received",
                  "review.new", "lead.received", "conversation.started"]
WEBHOOK_NAME = "Epaphras"


def webhook_config(data):
    """Return the webhook block, installing a disabled default if absent."""
    return data.setdefault("webhook", {
        "enabled": False, "id": None, "secret": None,
        "url": None, "events": [], "synced_at": None,
    })


def _gen_secret():
    return secrets.token_hex(32)


def webhook_url():
    base = os.environ.get("EPAPHRAS_PUBLIC_URL")
    if not base:
        raise ConfigError("EPAPHRAS_PUBLIC_URL not set")
    return base.rstrip("/") + "/zernio/webhook"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -k "webhook_config or gen_secret or webhook_url" -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add webhook config block, secret gen, and receiver URL helpers"
```

---

## Task 3: Event matcher + log appender (`handle_webhook`)

Add the tolerant text/platform extractors, the topic matcher, and `handle_webhook` which matches a delivery against the **active mode** and appends one JSONL line to the log file.

**Files:**
- Create: `tests/fixtures/comment_received.sample.json`
- Modify: `engine.py` (add after the Task 2 helpers)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/comment_received.sample.json`:

```json
{
  "id": "evt_abc123",
  "event": "comment.received",
  "timestamp": "2026-06-11T00:00:00Z",
  "account": { "platform": "threads", "username": "wintermelonely" },
  "comment": { "text": "This new esports drama is absolutely wild lol", "author": "rando" }
}
```

- [ ] **Step 2: Write the failing test**

The fixture `modes.sample.json` has `current_active_mode: culture_drama` whose active topics include `esports` ("Esports Drama") and platforms `tiktok` + `threads`.

```python
WH_FIXTURE = Path(__file__).parent / "fixtures" / "comment_received.sample.json"


def test_match_event_matches_active_topic(cfg):
    data = engine.load_config(cfg)
    payload = json.loads(WH_FIXTURE.read_text())
    out = engine.match_event(data, payload)
    assert out["notify"] is True
    assert "Esports Drama" in out["matched_topics"]
    assert out["platform"] == "threads"
    assert out["event"] == "comment.received"
    assert out["event_id"] == "evt_abc123"
    assert "esports drama" in out["snippet"].lower()


def test_match_event_platform_not_in_mode_skips(cfg):
    data = engine.load_config(cfg)
    payload = json.loads(WH_FIXTURE.read_text())
    payload["account"]["platform"] = "linkedin"  # not in culture_drama
    out = engine.match_event(data, payload)
    assert out["notify"] is False
    assert out["matched_topics"] == []


def test_match_event_no_topic_match(cfg):
    data = engine.load_config(cfg)
    payload = json.loads(WH_FIXTURE.read_text())
    payload["comment"]["text"] = "just a cute cat photo"
    out = engine.match_event(data, payload)
    assert out["notify"] is False


def test_match_event_unknown_platform_does_not_filter(cfg):
    data = engine.load_config(cfg)
    payload = json.loads(WH_FIXTURE.read_text())
    del payload["account"]  # no resolvable platform
    out = engine.match_event(data, payload)
    assert out["notify"] is True  # text still matches, platform filter skipped


def test_handle_webhook_appends_jsonl(cfg, tmp_path, monkeypatch):
    log = tmp_path / "wh.jsonl"
    monkeypatch.setenv("EPAPHRAS_WEBHOOK_LOG", str(log))
    data = engine.load_config(cfg)
    payload = json.loads(WH_FIXTURE.read_text())
    out = engine.handle_webhook(data, payload)
    assert out["notify"] is True
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event_id"] == "evt_abc123"
    assert rec["notify"] is True
    assert "ts" in rec
    # a second delivery appends a second line
    engine.handle_webhook(data, payload)
    assert len(log.read_text().strip().splitlines()) == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_match_event_matches_active_topic -v`
Expected: FAIL with `AttributeError: module 'engine' has no attribute 'match_event'`

- [ ] **Step 4: Write minimal implementation**

Add `from datetime import datetime, timezone` to the imports at the top of `engine.py`. Then add after the Task 2 helpers:

```python
TEXT_KEYS = {"text", "body", "content", "message", "caption", "title",
             "comment", "question", "name", "subject"}


def _gather_text(obj):
    """Recursively collect string values under known text-bearing keys."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in TEXT_KEYS and isinstance(v, str):
                out.append(v)
            else:
                out.extend(_gather_text(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_gather_text(v))
    return out


def _event_platform(payload):
    for path in (("account", "platform"), ("data", "account", "platform"),
                 ("data", "platform"), ("platform",)):
        cur = payload
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, str):
            return cur
    return None


def _topic_matches(label, text_lower):
    """Match if any label token (len >= 3) appears as a whole word in text."""
    for tok in re.findall(r"[a-z0-9]+", label.lower()):
        if len(tok) >= 3 and re.search(rf"\b{re.escape(tok)}\b", text_lower):
            return True
    return False


def match_event(data, payload):
    """Match a delivered event against the active mode's platforms + active topics."""
    event = payload.get("event")
    event_id = payload.get("id")
    platform = _event_platform(payload)
    text = " ".join(_gather_text(payload))
    text_lower = text.lower()
    snippet = text[:140]
    result = {"notify": False, "matched_topics": [], "platform": platform,
              "event": event, "event_id": event_id, "snippet": snippet}

    mode_id = data.get("current_active_mode")
    mode = data.get("modes", {}).get(mode_id)
    if not mode:
        return result
    mode_platforms = {p["platform"] if isinstance(p, dict) else p
                      for p in mode.get("platforms", [])}
    if platform and mode_platforms and platform not in mode_platforms:
        return result  # delivery is for a platform this mode does not watch
    matched = [t["label"] for t in mode.get("topics", {}).values()
               if t.get("active") and _topic_matches(t["label"], text_lower)]
    result["matched_topics"] = matched
    result["notify"] = bool(matched)
    return result


def _webhook_log_path():
    env = os.environ.get("EPAPHRAS_WEBHOOK_LOG")
    return Path(env) if env else Path(__file__).parent / "webhook_events.jsonl"


def handle_webhook(data, payload):
    """Match an event and append the decision (one JSONL line) to the log."""
    result = match_event(data, payload)
    record = dict(result, ts=datetime.now(timezone.utc).isoformat())
    path = _webhook_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -k "match_event or handle_webhook" -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_engine.py tests/fixtures/comment_received.sample.json
git commit -m "feat: add event matcher and JSONL logger (handle_webhook)"
```

---

## Task 4: Registration operations (enable / disable / sync / list)

Add the functions that create/update/delete the zernio webhook through `_mcp_call`. Tests monkeypatch `_mcp_call` with a fake gateway that records calls.

**Files:**
- Modify: `engine.py` (add after `handle_webhook`)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
class FakeGateway:
    """Records _mcp_call invocations and simulates the webhooks_* tools."""
    def __init__(self, existing=None):
        self.calls = []
        self.webhooks = list(existing or [])
        self._next_id = 100

    def __call__(self, name, arguments=None):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "webhooks_get_webhook_settings":
            return {"webhooks": self.webhooks}
        if name == "webhooks_create_webhook_settings":
            self._next_id += 1
            wh = {"id": f"wh_{self._next_id}", "url": arguments["url"],
                  "events": arguments["events"], "isActive": True}
            self.webhooks.append(wh)
            return wh
        if name == "webhooks_update_webhook_settings":
            for wh in self.webhooks:
                if wh["id"] == arguments["id"]:
                    wh.update({k: v for k, v in arguments.items() if v is not None})
            return {"id": arguments["id"]}
        if name == "webhooks_delete_webhook_settings":
            self.webhooks = [w for w in self.webhooks if w["id"] != arguments["id"]]
            return {"ok": True}
        raise AssertionError(f"unexpected tool {name}")


@pytest.fixture
def gw(monkeypatch):
    g = FakeGateway()
    monkeypatch.setattr(engine, "_mcp_call", g)
    monkeypatch.setenv("EPAPHRAS_PUBLIC_URL", "https://host.example")
    return g


def test_enable_webhook_creates_and_persists(cfg, gw):
    data = engine.load_config(cfg)
    out = engine.enable_webhook(data)
    wh = engine.webhook_config(data)
    assert wh["enabled"] is True
    assert wh["id"] == "wh_101"
    assert wh["secret"] and len(wh["secret"]) == 64
    assert wh["url"] == "https://host.example/zernio/webhook"
    assert wh["events"] == engine.WEBHOOK_EVENTS
    created = [c for c in gw.calls if c[0] == "webhooks_create_webhook_settings"]
    assert created and created[0][1]["secret"] == wh["secret"]
    assert out["ok"] is True


def test_enable_webhook_updates_when_url_exists(cfg, gw):
    gw.webhooks.append({"id": "wh_9", "url": "https://host.example/zernio/webhook",
                        "events": [], "isActive": False})
    data = engine.load_config(cfg)
    engine.enable_webhook(data)
    assert engine.webhook_config(data)["id"] == "wh_9"
    assert any(c[0] == "webhooks_update_webhook_settings" for c in gw.calls)
    assert not any(c[0] == "webhooks_create_webhook_settings" for c in gw.calls)


def test_enable_webhook_reuses_existing_secret(cfg, gw):
    data = engine.load_config(cfg)
    engine.webhook_config(data)["secret"] = "f" * 64
    engine.enable_webhook(data)
    assert engine.webhook_config(data)["secret"] == "f" * 64


def test_disable_webhook_deletes_and_clears(cfg, gw):
    data = engine.load_config(cfg)
    engine.enable_webhook(data)
    wid = engine.webhook_config(data)["id"]
    engine.disable_webhook(data)
    wh = engine.webhook_config(data)
    assert wh["enabled"] is False
    assert wh["id"] is None
    assert any(c == ("webhooks_delete_webhook_settings", {"id": wid}) for c in gw.calls)


def test_sync_webhook_noop_when_disabled(cfg, gw):
    data = engine.load_config(cfg)
    out = engine.sync_webhook(data)
    assert out["skipped"] is True
    assert gw.calls == []


def test_sync_webhook_recreates_when_missing(cfg, gw):
    data = engine.load_config(cfg)
    engine.enable_webhook(data)
    gw.webhooks.clear()           # webhook deleted in the zernio dashboard
    engine.sync_webhook(data)
    assert any(c[0] == "webhooks_create_webhook_settings" for c in gw.calls)
    assert engine.webhook_config(data)["id"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_enable_webhook_creates_and_persists -v`
Expected: FAIL with `AttributeError: module 'engine' has no attribute 'enable_webhook'`

- [ ] **Step 3: Write minimal implementation**

Add after `handle_webhook` in `engine.py`:

```python
def _list_webhooks():
    payload = _mcp_call("webhooks_get_webhook_settings", {})
    return payload.get("webhooks", []) if isinstance(payload, dict) else []


def _find_webhook_by_url(webhooks, url):
    return next((w for w in webhooks if w.get("url") == url), None)


def _wh_id(wh):
    return wh.get("id") or wh.get("_id")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def enable_webhook(data):
    """Create-or-update the zernio webhook and persist the block. Raises ConfigError."""
    wh = webhook_config(data)
    url = webhook_url()
    secret = wh.get("secret") or _gen_secret()
    existing = _find_webhook_by_url(_list_webhooks(), url)
    if existing:
        wid = _wh_id(existing)
        _mcp_call("webhooks_update_webhook_settings", {
            "id": wid, "url": url, "events": WEBHOOK_EVENTS,
            "secret": secret, "is_active": True})
    else:
        res = _mcp_call("webhooks_create_webhook_settings", {
            "name": WEBHOOK_NAME, "url": url, "events": WEBHOOK_EVENTS,
            "secret": secret, "is_active": True})
        wid = _wh_id(res) if isinstance(res, dict) else None
        if not wid:  # tolerate create responses that omit the id
            wid = _wh_id(_find_webhook_by_url(_list_webhooks(), url) or {})
    wh.update({"enabled": True, "id": wid, "secret": secret,
               "url": url, "events": list(WEBHOOK_EVENTS), "synced_at": _now_iso()})
    return {"ok": True, "enabled": True, "id": wid, "url": url}


def disable_webhook(data):
    """Delete the zernio webhook and mark the block disabled."""
    wh = webhook_config(data)
    wid = wh.get("id")
    if wid:
        _mcp_call("webhooks_delete_webhook_settings", {"id": wid})
    wh.update({"enabled": False, "id": None, "synced_at": _now_iso()})
    return {"ok": True, "enabled": False}


def sync_webhook(data):
    """Idempotent drift-correct: only writes when the webhook is missing/inactive
    or its events drifted. No-op when notifications are disabled."""
    wh = webhook_config(data)
    if not wh.get("enabled"):
        return {"ok": True, "skipped": True}
    url = webhook_url()
    existing = _find_webhook_by_url(_list_webhooks(), url)
    if existing is None:
        return enable_webhook(data)
    drifted = (not existing.get("isActive", True)
               or sorted(existing.get("events", [])) != sorted(WEBHOOK_EVENTS))
    if drifted:
        _mcp_call("webhooks_update_webhook_settings", {
            "id": _wh_id(existing), "url": url, "events": WEBHOOK_EVENTS,
            "secret": wh.get("secret"), "is_active": True})
    wh.update({"id": _wh_id(existing), "events": list(WEBHOOK_EVENTS),
               "synced_at": _now_iso()})
    return {"ok": True, "synced": True, "drifted": drifted}


def webhook_status(data):
    wh = webhook_config(data)
    return {"enabled": wh.get("enabled", False), "id": wh.get("id"),
            "url": wh.get("url"), "events": wh.get("events", []),
            "synced_at": wh.get("synced_at")}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -k "enable_webhook or disable_webhook or sync_webhook" -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add webhook registration ops (enable/disable/sync/status)"
```

---

## Task 5: `cb_notif` callback + notifications button

Route the `cb_notif` callback to toggle notifications, and render a `🔔 Notifications: On/Off` button on Screen 1.

**Files:**
- Modify: `engine.py` — `render_modes` (lines 363-376), `handle_callback` (add a branch near line 273)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_render_modes_shows_notifications_off_by_default(cfg):
    data = engine.load_config(cfg)
    screen = engine.render_modes(data)
    flat = [b for row in screen["buttons"] for b in row]
    notif = next(b for b in flat if b["callback_data"] == "cb_notif")
    assert "Off" in notif["text"] and "🔔" in notif["text"]


def test_render_modes_shows_notifications_on_when_enabled(cfg):
    data = engine.load_config(cfg)
    engine.webhook_config(data)["enabled"] = True
    flat = [b for row in engine.render_modes(data)["buttons"] for b in row]
    notif = next(b for b in flat if b["callback_data"] == "cb_notif")
    assert "On" in notif["text"]


def test_cb_notif_enables_then_disables(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "enable_webhook",
                        lambda d: calls.append("enable") or {"ok": True})
    monkeypatch.setattr(engine, "disable_webhook",
                        lambda d: calls.append("disable") or {"ok": True})
    data = engine.load_config(cfg)
    engine.handle_callback(data, "cb_notif")        # was disabled -> enable
    assert calls == ["enable"]
    engine.webhook_config(data)["enabled"] = True    # simulate enable side effect
    engine.handle_callback(data, "cb_notif")         # now enabled -> disable
    assert calls == ["enable", "disable"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_render_modes_shows_notifications_off_by_default -v`
Expected: FAIL (no button with `callback_data == "cb_notif"`; `StopIteration`)

- [ ] **Step 3: Write minimal implementation**

In `engine.py`, add `toggle_notifications` near the other callback helpers (e.g. before `handle_callback`):

```python
def toggle_notifications(data):
    if webhook_config(data).get("enabled"):
        disable_webhook(data)
    else:
        enable_webhook(data)
    return render_modes(data)
```

In `handle_callback`, add this branch alongside the other no-arg callbacks (after the `cb_cancel` branch, before the `if ":" not in cb` check):

```python
    if cb == "cb_notif":
        return toggle_notifications(data)
```

In `render_modes`, replace the final `rows.append([...])` / `return` (lines 374-376) with:

```python
    rows.append([{"text": "➕ New mode", "callback_data": "cb_newmode"}])
    on = webhook_config(data).get("enabled")
    rows.append([{"text": f"🔔 Notifications: {'On' if on else 'Off'}",
                  "callback_data": "cb_notif"}])
    return {"text": "Epaphras — Listening Config\nPick a mode:",
            "buttons": rows, "inline_keyboard": rows}
```

- [ ] **Step 3b: Update the two existing assertions that count `render_modes` rows**

Adding the notifications row makes `render_modes` return one extra row (now 6 for the
4-mode fixture), and the new-mode affordance is no longer the last row. Update both:

In `test_render_modes_marks_active`, change:
```python
    # 4 mode rows + 1 "New mode" row
    assert len(rows) == 5
```
to:
```python
    # 4 mode rows + "New mode" + "Notifications"
    assert len(rows) == 6
```
and change:
```python
    # new-mode affordance present
    assert rows[-1][0]["callback_data"] == "cb_newmode"
```
to:
```python
    # new-mode affordance present (now second-to-last; notifications is last)
    assert rows[-2][0]["callback_data"] == "cb_newmode"
    assert rows[-1][0]["callback_data"] == "cb_notif"
```

In `test_cli_render_modes`, change:
```python
    assert len(out["buttons"]) == 5
```
to:
```python
    assert len(out["buttons"]) == 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -k "notifications or cb_notif or render_modes or cli_render_modes" -v`
Expected: PASS (6 passed — 3 new + the 2 updated existing + the cli one)

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: add notifications toggle button and cb_notif routing"
```

---

## Task 6: Post-mutation sync hook + CLI wiring

Wire the new commands into `main()` and run a best-effort `sync_webhook` after a config-mutating `handle-callback`/`handle-text` (so the registration self-heals when modes/topics change). Sync failures must never break the panel.

**Files:**
- Modify: `engine.py` — `main()` argparse `choices` (line 449-451) and the dispatch block (lines 498-515), plus a `_maybe_sync` helper
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cli_handle_webhook(cfg, tmp_path):
    log = tmp_path / "wh.jsonl"
    env = dict(os.environ, EPAPHRAS_MODES_FILE=str(cfg),
               EPAPHRAS_WEBHOOK_LOG=str(log))
    payload = WH_FIXTURE.read_text()
    proc = subprocess.run(
        [sys.executable, str(Path(engine.__file__)), "handle-webhook", payload],
        capture_output=True, text=True, env=env,
        cwd=str(Path(engine.__file__).parent),
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["notify"] is True
    assert log.read_text().strip()  # a line was written


def test_cli_webhook_status(cfg):
    rc, out = run_cli(cfg, "webhook-status")
    assert rc == 0
    assert out["enabled"] is False


def test_maybe_sync_swallows_errors(cfg, monkeypatch):
    def boom(d):
        raise engine.ConfigError("network down")
    monkeypatch.setattr(engine, "sync_webhook", boom)
    data = engine.load_config(cfg)
    engine.webhook_config(data)["enabled"] = True
    engine._maybe_sync(data)  # must not raise


def test_maybe_sync_skips_when_disabled(cfg, monkeypatch):
    called = []
    monkeypatch.setattr(engine, "sync_webhook", lambda d: called.append(1))
    data = engine.load_config(cfg)
    engine._maybe_sync(data)
    assert called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_cli_webhook_status -v`
Expected: FAIL — argparse rejects `webhook-status` (`SystemExit: 2`), so `run_cli` returns a non-zero rc / unparseable output.

- [ ] **Step 3: Write minimal implementation**

Add the `_maybe_sync` helper near `toggle_notifications` in `engine.py`:

```python
def _maybe_sync(data):
    """Best-effort drift-correct after a mutation; never raises."""
    if not webhook_config(data).get("enabled"):
        return
    try:
        sync_webhook(data)
    except ConfigError:
        pass  # registration is a background concern; never block the panel
```

In `main()`, extend the argparse `choices` list to include the new commands:

```python
        choices=["render-modes", "render-topics", "setmode", "toggle", "init",
                 "store-msgid", "get-msgid",
                 "handle-callback", "handle-text", "render-platforms",
                 "webhook-status", "webhook-enable", "webhook-disable",
                 "webhook-sync", "handle-webhook"],
```

In the dispatch block, change the existing `handle-callback` and `handle-text` branches to call `_maybe_sync` before `save_config`, and add the new command branches. Replace the current `handle-callback` and `handle-text` branches (lines 498-511) with:

```python
        elif args.command == "handle-callback":
            if not args.arg:
                _emit({"error": "handle-callback requires a callback_data argument"})
                return 1
            out = handle_callback(data, args.arg)
            if args.arg != "cb_notif":  # cb_notif already (re)synced via enable
                _maybe_sync(data)
            save_config(path, data)
            _emit(out)
        elif args.command == "handle-text":
            text = args.arg or ""
            step = data.get("wizard", {}).get("step", "idle")
            out = handle_text(data, text)
            if step != "idle":
                if out.get("handled"):
                    _maybe_sync(data)
                save_config(path, data)
            _emit(out)
        elif args.command == "webhook-status":
            _emit(webhook_status(data))
        elif args.command == "webhook-enable":
            out = enable_webhook(data)
            save_config(path, data)
            _emit(out)
        elif args.command == "webhook-disable":
            out = disable_webhook(data)
            save_config(path, data)
            _emit(out)
        elif args.command == "webhook-sync":
            out = sync_webhook(data)
            save_config(path, data)
            _emit(out)
        elif args.command == "handle-webhook":
            payload = json.loads(args.arg or "{}")
            _emit(handle_webhook(data, payload))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -k "cli_handle_webhook or cli_webhook_status or maybe_sync" -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (all green — existing + new)

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: wire webhook CLI commands and post-mutation sync hook"
```

---

## Task 7: Template, gitignore, and docs

Persist a default `webhook` block in the seed template, ignore the log file, and document the new behavior.

**Files:**
- Modify: `templates/modes.default.json` (top-level keys)
- Modify: `.gitignore`
- Modify: `SKILL.md`, `README.md`

- [ ] **Step 1: Add the default webhook block to the template**

In `templates/modes.default.json`, add a top-level `"webhook"` key after the `"wizard"` line (line 3). The opening looks like `{ "current_active_mode": ..., "wizard": { "step": "idle" }, "modes": {...} }` — insert between `wizard` and `modes`:

```json
  "wizard": { "step": "idle" },
  "webhook": { "enabled": false, "id": null, "secret": null, "url": null, "events": [], "synced_at": null },
  "modes": {
```

- [ ] **Step 2: Verify the template is still valid JSON**

Run: `python3 -c "import json; json.load(open('templates/modes.default.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Ignore the webhook log**

Add to `.gitignore` (after the `*.tmp` line):

```
webhook_events.jsonl
```

- [ ] **Step 4: Document in SKILL.md**

In `SKILL.md`, add a `cb_notif` row to the callback reference table (after the `cb_cancel` row):

```
| `cb_notif` | toggle zernio webhook on/off |
```

And add a new section after "## Error handling":

```markdown
## Notifications (zernio webhook)

Screen 1 shows **🔔 Notifications: On/Off**. Tapping it creates (On) or deletes (Off)
a zernio webhook via the MCP gateway, subscribing to inbound-engagement events
(`comment.received`, `message.received`, `reaction.received`, `review.new`,
`lead.received`, `conversation.started`).

Zernio delivers events to `POST {EPAPHRAS_PUBLIC_URL}/zernio/webhook` on the OpenClaw
runtime (mounted by the gateway patch). The receiver verifies the `X-Zernio-Signature`
HMAC, dedups on `X-Zernio-Event-Id`, acks `200`, then runs
`engine.py handle-webhook <payload>`, which matches the event against the **active
mode's** platforms and active topic labels and appends one JSON line per delivery to
`EPAPHRAS_WEBHOOK_LOG` (default `webhook_events.jsonl`). Pushing matches to Telegram is
not done yet — review the log file.

Topic/mode/platform edits take effect on the *next* delivery with no zernio call (the
receiver reads `modes.json` live); a best-effort `webhook-sync` after each change
re-creates the webhook if it was deleted externally.

### Env vars
- `EPAPHRAS_MCP_GATEWAY_URL` — zernio MCP gateway (default: this deployment's gateway).
- `EPAPHRAS_PUBLIC_URL` — public base URL of the runtime; the receiver path
  `/zernio/webhook` is appended. **Required to enable notifications.**
- `EPAPHRAS_WEBHOOK_LOG` — filter-decision log path (default `webhook_events.jsonl`).

### Engine commands
`webhook-status`, `webhook-enable`, `webhook-disable`, `webhook-sync`,
`handle-webhook <payload-json>`.
```

- [ ] **Step 5: Document in README.md**

In `README.md`, add a short bullet under "## Install" after the zernio MCP note:

```markdown
5. (Optional) To enable zernio notifications, set `EPAPHRAS_PUBLIC_URL` to the
   runtime's public base URL. Tap **🔔 Notifications** in the panel to register the
   webhook. Filter decisions are logged to `webhook_events.jsonl` (configurable via
   `EPAPHRAS_WEBHOOK_LOG`); Telegram delivery of matches is not yet implemented.
```

- [ ] **Step 6: Commit**

```bash
git add templates/modes.default.json .gitignore SKILL.md README.md
git commit -m "docs: document webhook notifications, env vars, and seed template block"
```

---

## Task 8: Receiver gateway patch (Patch 4)

Add a patch that mounts `POST /zernio/webhook` on the OpenClaw runtime HTTP server (the one serving the "OpenClaw Control" dashboard at the public URL). This is JS injected by Python; it has no unit test and is validated by the manual checklist in Task 9.

**Files:**
- Modify: `scripts/full_patch_v2.py` (append a new patch block at the end, before the final summary print if any)

- [ ] **Step 1: Spike — locate the HTTP server anchor (do this first)**

The receiver must hook the HTTP server that serves the dashboard. Identify the anchor in the live runtime before writing the patch:

Run (from your machine, using the prod kubeconfig + the target pod):
```bash
KC=~/Documents/kubeconfig_prod.yaml
NS=agent-core-111735
POD=openclaw-873441b0-62a0-4a16-ade7-7a2f40d27a9b-5bc87bbc79-zm7h4
# Find which bundle creates the HTTP listener / serves the SPA:
kubectl --kubeconfig $KC -n $NS exec $POD -c gateway -- \
  sh -c 'grep -rlE "createServer|\.listen\(|app\.(get|post)\(|fastify|express|Hono|serve\(" /app/dist 2>/dev/null | head'
```
Record the file and the exact framework/route-registration call. The patch below assumes a Node `http.createServer((req,res)=>{...})` request handler reached via a top-level request listener; **adapt the anchor and the route-injection to whatever the spike finds** (Express → `app.post(...)`; Hono → `app.post(...)`; raw `http` → branch on `req.method`/`req.url` at the top of the handler). If no HTTP server is found in `/app/dist`, stop and report — the receiver cannot be mounted and the feature is registration-only until that's resolved.

- [ ] **Step 2: Append the receiver patch to `scripts/full_patch_v2.py`**

Append at the end of `scripts/full_patch_v2.py`. This template targets a raw `http.createServer` request handler; **edit `RECV_ANCHOR` and the framework specifics per the Step 1 spike.** It is idempotent (marker-guarded) and self-contained.

```python
# ─── Patch 4: zernio webhook receiver (_EPAPHRAS_WEBHOOK_V1) ─────────────────────
# Mounts POST /zernio/webhook on the runtime HTTP server: verify HMAC -> dedup ->
# ack 200 -> shell to engine.py handle-webhook (which matches + logs). No Telegram.
import glob as _g4
WH_MARKER = '_EPAPHRAS_WEBHOOK_V1'
# NOTE: set this to the bundle + anchor identified by the Task 8 Step 1 spike.
RECV_BUNDLE_GLOB = '/app/dist/pi-embedded-*.js'
_recv_matches = _g4.glob(RECV_BUNDLE_GLOB)
if not _recv_matches:
    print("WARNING: receiver bundle not found — skipping Patch 4")
else:
    RECV_FILE = _recv_matches[0]
    with open(RECV_FILE, 'r') as f:
        rsrc = f.read()
    if WH_MARKER in rsrc:
        print(f"receiver already patched ({WH_MARKER}) — skipping")
    else:
        # Anchor: the first line inside the createServer request handler.
        # ADAPT to the spike result (Express/Hono register a route instead).
        RECV_ANCHOR = 'http.createServer((req, res) => {'
        if RECV_ANCHOR not in rsrc:
            print("WARNING: receiver HTTP anchor not found — skipping Patch 4 "
                  "(re-run the Task 8 spike and update RECV_ANCHOR)")
        else:
            INJECT = (
                'http.createServer((req, res) => {\n'
                '// PATCH: ' + WH_MARKER + '\n'
                'if (req.method === "POST" && (req.url || "").split("?")[0] === "/zernio/webhook") {\n'
                '  const _ENGINE = "' + ENGINE_PATH + '";\n'
                '  const _MODES = process.env.EPAPHRAS_MODES_FILE || "' + MODES_PATH + '";\n'
                '  const _chunks = [];\n'
                '  req.on("data", (c) => _chunks.push(c));\n'
                '  req.on("end", () => {\n'
                '    try {\n'
                '      const _crypto = __require("crypto");\n'
                '      const _fs = __require("fs");\n'
                '      const _raw = Buffer.concat(_chunks);\n'
                '      let _secret = null, _eid = req.headers["x-zernio-event-id"] || req.headers["x-late-event-id"];\n'
                '      try { _secret = JSON.parse(_fs.readFileSync(_MODES, "utf8")).webhook?.secret || null; } catch (_) {}\n'
                '      if (_secret) {\n'
                '        const _sig = req.headers["x-zernio-signature"] || "";\n'
                '        const _calc = _crypto.createHmac("sha256", _secret).update(_raw).digest("hex");\n'
                '        const _a = Buffer.from(_calc), _b = Buffer.from(String(_sig));\n'
                '        if (_a.length !== _b.length || !_crypto.timingSafeEqual(_a, _b)) {\n'
                '          res.writeHead(401); res.end("bad signature"); return;\n'
                '        }\n'
                '      }\n'
                '      globalThis.__epaphrasSeen = globalThis.__epaphrasSeen || new Set();\n'
                '      const _seen = globalThis.__epaphrasSeen;\n'
                '      if (_eid && _seen.has(_eid)) { res.writeHead(200); res.end("dup"); return; }\n'
                '      if (_eid) { _seen.add(_eid); if (_seen.size > 1000) _seen.delete(_seen.values().next().value); }\n'
                '      res.writeHead(200); res.end("ok");  // ack before processing (5s budget)\n'
                '      try {\n'
                '        const { execFileSync: _es } = __require("child_process");\n'
                '        _es("python3", [_ENGINE, "handle-webhook", _raw.toString("utf8")], { timeout: 8000 });\n'
                '      } catch (_pe) {\n'
                '        try { __require("fs").appendFileSync("/tmp/wh_err.log", String(_pe) + "\\n"); } catch (_) {}\n'
                '      }\n'
                '    } catch (_e) {\n'
                '      try { res.writeHead(500); res.end("err"); } catch (_) {}\n'
                '    }\n'
                '  });\n'
                '  return;\n'
                '}\n'
                '// END PATCH: ' + WH_MARKER + '\n'
            )
            rsrc = rsrc.replace(RECV_ANCHOR, INJECT, 1)
            with open(RECV_FILE, 'w') as f:
                f.write(rsrc)
            print(f"Patched receiver with {WH_MARKER}")
            import os as _os4
            for _jiti in _g4.glob('/tmp/jiti/dist-pi-embedded-*.cjs'):
                try:
                    _os4.remove(_jiti); print(f"Deleted JITI cache: {_jiti}")
                except Exception as _e:
                    print(f"WARNING: could not delete JITI cache {_jiti}: {_e}")
```

- [ ] **Step 3: Syntax-check the patch script**

Run: `python3 -c "import ast; ast.parse(open('scripts/full_patch_v2.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add scripts/full_patch_v2.py
git commit -m "feat: add zernio webhook receiver gateway patch (Patch 4)"
```

---

## Task 9: Live apply + manual verification

The JS receiver, HMAC verification, and dedup are not unit-testable. Verify end-to-end against the running pod.

**Files:** none (operational).

- [ ] **Step 1: Set the runtime env vars** (operator action)

Ensure the `gateway` container has `EPAPHRAS_PUBLIC_URL=https://openclaw-111735-epaphras.agentbase-runtime.aiplatform.vngcloud.vn` and (optionally) `EPAPHRAS_MCP_GATEWAY_URL`, `EPAPHRAS_WEBHOOK_LOG`. Confirm:
```bash
KC=~/Documents/kubeconfig_prod.yaml; NS=agent-core-111735
POD=openclaw-873441b0-62a0-4a16-ade7-7a2f40d27a9b-5bc87bbc79-zm7h4
kubectl --kubeconfig $KC -n $NS exec $POD -c gateway -- printenv | grep EPAPHRAS_
```

- [ ] **Step 2: Apply the patch and restart the runtime** (per existing patch workflow — copy `engine.py` + run `scripts/full_patch_v2.py` in the pod, then restart the gateway process). Confirm the marker landed:
```bash
kubectl --kubeconfig $KC -n $NS exec $POD -c gateway -- \
  sh -c 'grep -l _EPAPHRAS_WEBHOOK_V1 /app/dist/pi-embedded-*.js'
```

- [ ] **Step 3: Verify the route is live**
```bash
U=https://openclaw-111735-epaphras.agentbase-runtime.aiplatform.vngcloud.vn
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$U/zernio/webhook" -d '{}'
```
Expected: `200` (no secret set yet → signature skipped) or `401` (secret set, missing signature) — **not 404**. A 404 means the anchor/route is wrong; revisit Task 8.

- [ ] **Step 4: Telegram dry-run checklist**
  - [ ] `/epaphras` shows Screen 1 with a **🔔 Notifications: Off** button.
  - [ ] Tap it → no `⚠️`; button flips to **On**; `webhook-status` shows `enabled:true` with an `id`.
  - [ ] Confirm in zernio: `webhooks_get_webhook_settings` lists one webhook with the runtime URL and the 6 events.
  - [ ] Trigger `webhooks_test_webhook` (or the dashboard "send test") → a line appears in `webhook_events.jsonl` in the pod.
  - [ ] Post a real comment containing an active topic word on a connected account → a matching line with `notify:true` and the topic in `matched_topics`.
  - [ ] A delivery whose text matches no active topic → a line with `notify:false`.
  - [ ] Send a delivery with a wrong `X-Zernio-Signature` → `401`, no log line.
  - [ ] Re-deliver the same `X-Zernio-Event-Id` → single log line (dedup).
  - [ ] Toggle a topic / switch modes, then re-trigger the same event → the new active mode/topics are reflected with no re-registration.
  - [ ] Tap **🔔 Notifications: On** → flips to **Off**; `webhooks_get_webhook_settings` returns `{'webhooks': []}`.

- [ ] **Step 5: Record results** in the PR / commit description (which checks passed; any anchor adjustments made in Task 8).

---

## Self-review notes (for the implementer)

- **Spec coverage:** §2 constraints → Task 1 (gateway drift), Task 8 (HMAC/5s-ack/dedup). §4 data model → Task 2 (block) + Task 7 (template). §4 matcher → Task 3. §5 receiver → Task 8. §6 commands/`cb_notif`/sync hook → Tasks 4–6. §7 env vars → Tasks 1,2,3,7. Caveats (plan-gating, account-global, no-web-listening) → documented in SKILL.md (Task 7) and surfaced as `⚠️` via `ConfigError` on create failure (Task 4).
- **Names are consistent across tasks:** `_mcp_call`, `webhook_config`, `_gen_secret`, `webhook_url`, `WEBHOOK_EVENTS`, `match_event`, `handle_webhook`, `enable_webhook`, `disable_webhook`, `sync_webhook`, `webhook_status`, `toggle_notifications`, `_maybe_sync`, marker `_EPAPHRAS_WEBHOOK_V1`.
- **Main risk (Task 8 anchor):** the HTTP-server anchor is unknown until the Step-1 spike; the patch must be adapted to the framework found. Everything else is fully unblocked and unit-tested.
```
