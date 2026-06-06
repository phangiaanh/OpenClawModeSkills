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
