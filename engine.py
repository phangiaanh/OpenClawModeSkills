"""Epaphras Modes engine: modes.json IO, mutation, and Telegram payload rendering."""
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "modes.default.json"
DEFAULT_FILE = Path(__file__).parent / "modes.json"


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
        try:
            shutil.copyfile(src, path)
        except (FileNotFoundError, OSError) as e:
            raise ConfigError(f"template not found: {src}") from e
    return path


def load_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ConfigError(f"config unreadable: {e}")
    if not data or "modes" not in data:
        raise ConfigError("config missing 'modes'")
    return data


def save_config(path, data):
    path = Path(path)
    if path.exists():
        shutil.copyfile(path, Path(str(path) + ".bak"))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)  # atomic on POSIX


def _btn(label, value):
    return {"label": label, "value": value}


def _block(label, value):
    return {"type": "buttons", "buttons": [_btn(label, value)]}


def render_modes(data):
    active = data.get("current_active_mode")
    blocks = []
    for mode_id, mode in data["modes"].items():
        label = f"{mode['icon']} {mode['name']}"
        if mode_id == active:
            label += " ▶️"
        blocks.append(_block(label, f"cb_setmode:{mode_id}"))
    return {
        "message": "Epaphras — Listening Config\nPick a mode:",
        "presentation": {"blocks": blocks},
    }


def render_topics(data, mode_id=None):
    mode_id = mode_id or data.get("current_active_mode")
    if mode_id is None:
        raise ConfigError("no active mode set")
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    mode = data["modes"][mode_id]
    platforms = " + ".join(mode["platforms"])
    message = f"{mode['icon']} {mode['name']}\nPlatforms: {platforms}\nTap a topic to toggle:"
    blocks = []
    for topic_id, topic in mode["topics"].items():
        mark = "✅" if topic["active"] else "⬜"
        blocks.append(_block(f"{mark} {topic['label']}", f"cb_toggle:{topic_id}"))
    blocks.append(_block("⬅️ Back to modes", "cb_back"))
    return {"message": message, "presentation": {"blocks": blocks}}


def setmode(data, mode_id):
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    data["current_active_mode"] = mode_id
    return data


def toggle(data, topic_id):
    mode_id = data.get("current_active_mode")
    if not mode_id or mode_id not in data["modes"]:
        raise ConfigError(f"no valid active mode set (got: {mode_id!r})")
    topics = data["modes"][mode_id]["topics"]
    if topic_id not in topics:
        raise ConfigError(f"unknown topic: {topic_id} in mode {mode_id}")
    topics[topic_id]["active"] = not bool(topics[topic_id]["active"])
    return data


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Epaphras Modes engine")
    parser.add_argument(
        "command",
        choices=["render-modes", "render-topics", "setmode", "toggle", "init"],
    )
    parser.add_argument("arg", nargs="?", help="mode_id or topic_id")
    parser.add_argument("--file", help="path to modes.yaml")
    parser.add_argument("--mode", help="mode id for render-topics")
    args = parser.parse_args(argv)

    path = resolve_path(args.file)
    try:
        if args.command == "init":
            ensure_file(path)
            _emit({"message": f"initialized {path}"})
            return 0

        ensure_file(path)
        data = load_config(path)

        if args.command == "render-modes":
            _emit(render_modes(data))
        elif args.command == "render-topics":
            if args.arg:
                _emit({"error": "render-topics takes --mode <id>, not a positional argument"})
                return 1
            _emit(render_topics(data, args.mode))
        elif args.command == "setmode":
            if not args.arg:
                _emit({"error": "setmode requires a mode_id argument"})
                return 1
            setmode(data, args.arg)
            save_config(path, data)
            _emit(render_topics(data, args.arg))
        elif args.command == "toggle":
            if not args.arg:
                _emit({"error": "toggle requires a topic_id argument"})
                return 1
            toggle(data, args.arg)
            save_config(path, data)
            _emit(render_topics(data))
        return 0
    except ConfigError as e:
        _emit({"error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
