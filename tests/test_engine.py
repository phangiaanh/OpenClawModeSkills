import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import engine

FIXTURE = Path(__file__).parent / "fixtures" / "modes.sample.json"


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


def test_toggle_raises_config_error_when_active_mode_key_missing():
    data = {"modes": {"deep_research": {"topics": {"academic_papers": {"active": True}}}}}
    with pytest.raises(engine.ConfigError):
        engine.toggle(data, "academic_papers")


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
