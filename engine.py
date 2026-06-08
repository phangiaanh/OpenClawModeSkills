"""Epaphras Modes engine: modes.json IO, mutation, and Telegram payload rendering."""
import json
import os
import re
import shutil
import sys
from pathlib import Path

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "modes.default.json"
DEFAULT_FILE = Path(__file__).parent / "modes.json"


PLATFORM_EMOJI = {
    "threads": "🧵", "tiktok": "🎵", "x": "✖️", "twitter": "✖️",
    "instagram": "📸", "youtube": "▶️", "linkedin": "💼", "facebook": "📘",
}
DEFAULT_ICON = "🎯"


def platform_label(entry):
    if isinstance(entry, str):
        return entry
    platform = entry.get("platform", "?")
    emoji = PLATFORM_EMOJI.get(platform, "🌐")
    handle = entry.get("handle")
    return f"{emoji} {platform} · @{handle}" if handle else f"{emoji} {platform}"


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


def get_wizard(data):
    return data.setdefault("wizard", {"step": "idle"})


def reset_wizard(data):
    data["wizard"] = {"step": "idle"}
    return data


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


def store_panel_msgid(data, msgid):
    try:
        data["panel_message_id"] = int(msgid)
    except (ValueError, TypeError):
        raise ConfigError(f"invalid message id: {msgid!r}")
    return data


def get_panel_msgid(data):
    mid = data.get("panel_message_id")
    return {"message_id": mid}


def setmode(data, mode_id):
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    data["current_active_mode"] = mode_id
    return data


def toggle(data, topic_id):
    mode_id = data.get("current_active_mode")
    # Try current active mode first, then search all modes for the topic
    if mode_id and mode_id in data["modes"] and topic_id in data["modes"][mode_id]["topics"]:
        target_mode_id = mode_id
    else:
        target_mode_id = next(
            (mid for mid, m in data["modes"].items() if topic_id in m.get("topics", {})),
            None,
        )
        if target_mode_id is None:
            raise ConfigError(f"unknown topic: {topic_id}")
        data["current_active_mode"] = target_mode_id
    data["modes"][target_mode_id]["topics"][topic_id]["active"] = not bool(
        data["modes"][target_mode_id]["topics"][topic_id]["active"]
    )
    return data


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Epaphras Modes engine")
    parser.add_argument(
        "command",
        choices=["render-modes", "render-topics", "setmode", "toggle", "init",
                 "store-msgid", "get-msgid"],
    )
    parser.add_argument("arg", nargs="?", help="mode_id or topic_id")
    parser.add_argument("--file", help="path to modes.yaml")
    parser.add_argument("--mode", help="mode id for render-topics")
    args = parser.parse_args(argv)

    path = resolve_path(args.file)
    try:
        if args.command == "init":
            ensure_file(path)
            _emit({"text": f"initialized {path}"})
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
        elif args.command == "store-msgid":
            if not args.arg:
                _emit({"error": "store-msgid requires a message_id argument"})
                return 1
            store_panel_msgid(data, args.arg)
            save_config(path, data)
            _emit({"ok": True, "message_id": data["panel_message_id"]})
        elif args.command == "get-msgid":
            _emit(get_panel_msgid(data))
        return 0
    except ConfigError as e:
        _emit({"error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
