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
