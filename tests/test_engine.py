import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import engine

FIXTURE = Path(__file__).parent / "fixtures" / "modes.sample.json"
WH_FIXTURE = Path(__file__).parent / "fixtures" / "comment_received.sample.json"


@pytest.fixture
def cfg(tmp_path):
    """A live config file seeded from the fixture, returned as a path."""
    dst = tmp_path / "modes.json"
    dst.write_text(FIXTURE.read_text())
    return dst


def cfg_path():
    return FIXTURE


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
    bad = tmp_path / "bad.json"
    bad.write_text("{unclosed json")
    with pytest.raises(engine.ConfigError):
        engine.load_config(bad)


def test_save_config_writes_backup(cfg):
    data = engine.load_config(cfg)
    engine.save_config(cfg, data)
    assert Path(str(cfg) + ".bak").exists()


def test_save_config_writes_valid_json_with_indent(cfg):
    data = engine.load_config(cfg)
    engine.save_config(cfg, data)
    text = cfg.read_text()
    parsed = json.loads(text)
    assert parsed["current_active_mode"] == "culture_drama"
    assert "  " in text  # indented (2-space)


def test_load_config_rejects_missing_modes_key(tmp_path):
    bad = tmp_path / "no_modes.json"
    bad.write_text('{"current_active_mode": "foo"}\n')
    with pytest.raises(engine.ConfigError, match="missing"):
        engine.load_config(bad)


def test_ensure_file_raises_config_error_when_template_missing(tmp_path):
    target = tmp_path / "modes.yaml"
    missing_template = tmp_path / "nonexistent.yaml"
    with pytest.raises(engine.ConfigError, match="template not found"):
        engine.ensure_file(target, template=str(missing_template))


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
    # legacy string platforms still render (fixture has ["TikTok", "Threads"])
    assert "TikTok + Threads" in out["text"]


def test_render_topics_defaults_to_active_mode(cfg):
    data = engine.load_config(cfg)
    out = engine.render_topics(data)  # no mode arg -> current_active_mode
    assert "🎭" in out["text"]


def test_render_topics_unknown_mode_raises(cfg):
    data = engine.load_config(cfg)
    with pytest.raises(engine.ConfigError):
        engine.render_topics(data, "nope")


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
    assert len(out["buttons"]) == 5


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


def test_toggle_falls_back_to_search_when_active_mode_missing():
    data = {"modes": {"deep_research": {"topics": {"academic_papers": {"active": True}}}}}
    engine.toggle(data, "academic_papers")
    assert data["modes"]["deep_research"]["topics"]["academic_papers"]["active"] is False
    assert data["current_active_mode"] == "deep_research"


def test_toggle_raises_config_error_when_topic_in_no_mode():
    data = {"current_active_mode": None, "modes": {"deep_research": {"topics": {"academic_papers": {"active": True}}}}}
    with pytest.raises(engine.ConfigError):
        engine.toggle(data, "nonexistent_topic")


def test_cli_setmode_without_arg_returns_error(cfg):
    rc, out = run_cli(cfg, "setmode")
    assert rc == 1
    assert "error" in out
    assert "mode_id" in out["error"]


def test_cli_toggle_without_arg_returns_error(cfg):
    rc, out = run_cli(cfg, "toggle")
    assert rc == 1
    assert "error" in out
    assert "topic_id" in out["error"]


def test_cli_render_topics_with_positional_arg_returns_error(cfg):
    rc, out = run_cli(cfg, "render-topics", "culture_drama")
    assert rc == 1
    assert "error" in out
    assert "--mode" in out["error"]


def test_platform_label_object_with_handle():
    entry = {"accountId": "abc", "platform": "threads", "handle": "wintermelonely"}
    assert engine.platform_label(entry) == "🧵 threads · @wintermelonely"


def test_platform_label_object_unknown_platform_uses_globe():
    assert engine.platform_label({"platform": "mastodon"}) == "🌐 mastodon"


def test_platform_label_legacy_string_passthrough():
    assert engine.platform_label("LinkedIn") == "LinkedIn"


def test_slugify_basic():
    assert engine._slugify("My Cool Mode!") == "my_cool_mode"


def test_slugify_caps_length_and_fallback():
    assert len(engine._slugify("x" * 50)) <= 18
    assert engine._slugify("!!!") == "mode"


def test_gen_id_unique_suffix():
    existing = {"news", "news_2"}
    assert engine.gen_id(existing, "news") == "news_3"
    assert engine.gen_id(existing, "tech") == "tech"


def test_get_wizard_defaults_to_idle():
    data = {"modes": {}}
    assert engine.get_wizard(data)["step"] == "idle"
    assert data["wizard"]["step"] == "idle"  # written through


def test_reset_wizard_clears_state():
    data = {"modes": {}, "wizard": {"step": "await_name", "draft": {"name": "x"}}}
    engine.reset_wizard(data)
    assert data["wizard"] == {"step": "idle"}


import json as _json

ACCOUNTS_FIXTURE = Path(__file__).parent / "fixtures" / "accounts.sample.json"


def _patch_payload(monkeypatch):
    payload = _json.loads(ACCOUNTS_FIXTURE.read_text())
    monkeypatch.setattr(engine, "_get_accounts_payload", lambda: payload)


def test_fetch_accounts_filters_and_maps(monkeypatch):
    _patch_payload(monkeypatch)
    accounts = engine.fetch_accounts()
    assert len(accounts) == 2  # disabled one filtered out
    assert accounts[0] == {"accountId": "6a2239332b2567671ad7b555",
                           "platform": "threads", "handle": "wintermelonely"}


def test_fetch_accounts_network_error_raises(monkeypatch):
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(engine, "_get_accounts_payload", boom)
    with pytest.raises(engine.ConfigError, match="accounts fetch failed"):
        engine.fetch_accounts()


def _wizard_picking(name="My Mode"):
    return {"current_active_mode": "culture_drama", "modes": {},
            "wizard": {"step": "pick_platforms", "draft": {"name": name, "platforms": []}}}


def test_render_platforms_lists_accounts(monkeypatch):
    _patch_payload(monkeypatch)
    data = _wizard_picking()
    out = engine.render_platforms(data)
    flat = [b for row in out["buttons"] for b in row]
    assert any(b["callback_data"] == "cb_pickplat:6a2239332b2567671ad7b555" for b in flat)
    assert any(b["callback_data"] == "cb_createmode" for b in flat)
    assert flat[-1]["callback_data"] == "cb_cancel"


def test_render_platforms_network_error_shows_warning(monkeypatch):
    def boom():
        raise OSError("unreachable")
    monkeypatch.setattr(engine, "_get_accounts_payload", boom)
    data = _wizard_picking()
    out = engine.render_platforms(data)
    assert out["text"].startswith("⚠️")
    assert out["buttons"][-1][0]["callback_data"] == "cb_cancel"


def test_pick_platform_toggles_and_caps_at_two(monkeypatch):
    _patch_payload(monkeypatch)
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


def test_start_new_mode_enters_await_name():
    data = {"current_active_mode": "x", "modes": {}}
    out = engine.start_new_mode(data)
    assert data["wizard"]["step"] == "await_name"
    assert out["buttons"][-1][0]["callback_data"] == "cb_cancel"


def test_submit_name_advances_to_pick_platforms(monkeypatch):
    _patch_payload(monkeypatch)
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
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Empty Mode")
    out = engine.create_mode(data)  # no platforms picked
    assert data["wizard"]["step"] == "pick_platforms"  # stays
    assert "Empty Mode" not in [m.get("name") for m in data["modes"].values()]


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


def test_handle_text_idle_not_handled():
    data = _json.loads(FIXTURE.read_text())
    assert engine.handle_text(data, "hello") == {"handled": False}


def test_handle_text_await_name_handled(monkeypatch):
    _patch_payload(monkeypatch)
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


def test_mcp_call_no_false_positive_on_null_error(monkeypatch):
    """A valid result envelope with an extra null 'error' key must not raise."""
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return (
            b"data: {\"jsonrpc\": \"2.0\", \"id\": 2, \"error\": null, \"result\": "
            b"{\"content\": [{\"type\": \"text\", \"text\": \"{'ok': True}\"}]}}\n\n"
        )
    monkeypatch.setattr(engine.urllib.request, "urlopen",
                        lambda req, timeout=None, context=None: FakeResp())
    out = engine._mcp_call("some_tool", {})
    assert out == {"ok": True}


def test_default_template_uses_object_platforms_and_idle_wizard():
    import json as _j
    tmpl = _j.loads((Path(__file__).parent.parent / "templates" / "modes.default.json").read_text())
    assert tmpl["wizard"]["step"] == "idle"
    for mode in tmpl["modes"].values():
        for p in mode["platforms"]:
            assert isinstance(p, dict) and "platform" in p
    # object platforms still render via render_topics
    first_id = next(iter(tmpl["modes"]))
    out = engine.render_topics(tmpl, first_id)
    assert "Platforms:" in out["text"]


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


def test_enable_webhook_raises_when_id_unresolvable(cfg, monkeypatch):
    monkeypatch.setenv("EPAPHRAS_PUBLIC_URL", "https://host.example")
    # create returns no id, and list returns empty
    def bad_mcp(name, arguments=None):
        if name == "webhooks_get_webhook_settings":
            return {"webhooks": []}
        if name == "webhooks_create_webhook_settings":
            return {"ok": True}  # no id field
        raise AssertionError(f"unexpected: {name}")
    monkeypatch.setattr(engine, "_mcp_call", bad_mcp)
    data = engine.load_config(cfg)
    with pytest.raises(engine.ConfigError, match="unresolvable"):
        engine.enable_webhook(data)
