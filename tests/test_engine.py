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
    # key order preserved: deep_research appears before culture_drama in the modes block
    modes_block = text[text.index("modes:"):]
    assert modes_block.index("deep_research") < modes_block.index("culture_drama")


def test_load_config_rejects_missing_modes_key(tmp_path):
    bad = tmp_path / "no_modes.yaml"
    bad.write_text("current_active_mode: foo\n")
    with pytest.raises(engine.ConfigError, match="missing"):
        engine.load_config(bad)


def test_ensure_file_raises_config_error_when_template_missing(tmp_path):
    target = tmp_path / "modes.yaml"
    missing_template = tmp_path / "nonexistent.yaml"
    with pytest.raises(engine.ConfigError, match="template not found"):
        engine.ensure_file(target, template=str(missing_template))


def _flat_buttons(out):
    """Flatten presentation blocks into a list of button dicts."""
    return [b for block in out["presentation"]["blocks"] for b in block["buttons"]]


def test_render_modes_marks_active(cfg):
    data = engine.load_config(cfg)
    out = engine.render_modes(data)
    assert "presentation" in out and "message" in out
    blocks = out["presentation"]["blocks"]
    assert len(blocks) == 4
    flat = _flat_buttons(out)
    active = next(b for b in flat if b["value"] == "cb_setmode:culture_drama")
    assert "▶️" in active["label"]
    inactive = next(b for b in flat if b["value"] == "cb_setmode:deep_research")
    assert "▶️" not in inactive["label"]


def test_render_topics_shows_toggle_marks(cfg):
    data = engine.load_config(cfg)
    out = engine.render_topics(data, "culture_drama")
    flat = _flat_buttons(out)
    esports = next(b for b in flat if b["value"] == "cb_toggle:esports")
    assert esports["label"].startswith("✅")  # active: true in fixture
    memes = next(b for b in flat if b["value"] == "cb_toggle:viral_memes")
    assert memes["label"].startswith("⬜")  # active: false
    back = flat[-1]
    assert back["value"] == "cb_back"
    assert "TikTok + Threads" in out["message"]


def test_render_topics_defaults_to_active_mode(cfg):
    data = engine.load_config(cfg)
    out = engine.render_topics(data)  # no mode arg -> current_active_mode
    assert "🎭" in out["message"]


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
    assert len(out["presentation"]["blocks"]) == 4


def test_cli_setmode_persists_and_returns_topics(cfg):
    rc, out = run_cli(cfg, "setmode", "global_news")
    assert rc == 0
    assert "🚨" in out["message"]
    assert engine.load_config(cfg)["current_active_mode"] == "global_news"


def test_cli_toggle_persists(cfg):
    rc, out = run_cli(cfg, "toggle", "viral_memes")
    assert rc == 0
    assert engine.load_config(cfg)["modes"]["culture_drama"]["topics"]["viral_memes"]["active"] is True


def test_cli_render_topics_with_mode_flag(cfg):
    rc, out = run_cli(cfg, "render-topics", "--mode", "deep_research")
    assert rc == 0
    assert "📚" in out["message"]


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
