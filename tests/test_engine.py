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
