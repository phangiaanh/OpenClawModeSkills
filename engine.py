"""Epaphras Modes engine: modes.json IO, mutation, and Telegram payload rendering."""
import ast
import copy
import json
import os
import re
import secrets
import shutil
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from datetime import time as _time
from pathlib import Path
from zoneinfo import ZoneInfo

import socialcrawl

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


def topic_query(topic):
    """The string sent to SocialCrawl for a topic: its `query`, else its label."""
    return topic.get("query") or topic.get("label", "")


def raw_engagement(record, weights):
    """Weighted raw engagement for one unified record."""
    return (weights["w_like"] * record.get("likes", 0)
            + weights["w_comment"] * record.get("comments", 0)
            + weights["w_share"] * record.get("shares", 0)
            + weights["w_reach"] * record.get("reach", 0))


def platform_baseline(raws):
    """Median of a platform's raw-engagement batch; never returns 0."""
    vals = sorted(v for v in raws if v is not None)
    if not vals:
        return 1.0
    n = len(vals)
    mid = n // 2
    med = vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2
    return med or 1.0


def magnitude(raw, baseline):
    return raw / baseline if baseline else raw


def velocity(raw_now, last_raw, dhours):
    """Non-negative engagement-gain rate since the last sighting."""
    if not dhours or dhours <= 0:
        return 0.0
    return max(0.0, (raw_now - last_raw) / dhours)


def recency(age_hours, gravity):
    return 1.0 / (age_hours + 2.0) ** gravity


def trend_score(magnitude_val, velocity_norm, beta, recency_factor):
    return (beta * magnitude_val + (1 - beta) * velocity_norm) * recency_factor


def passes_floor(record, floor):
    """True if the record meets/exceeds ANY configured floor metric (OR semantics)."""
    if not floor:
        return True
    return any(record.get(metric, 0) >= threshold for metric, threshold in floor.items())


def _hours_since(iso_str, now):
    """Hours between an ISO timestamp and `now` (>= 0). 0 if unparseable/missing."""
    if not iso_str:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def load_state(path):
    """Load the poll state store; empty/corrupt -> a fresh empty store."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"posts": {}}
    data.setdefault("posts", {})
    return data


def save_state(path, state):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    tmp.replace(path)


def update_state(state, key, raw, now, score, topic):
    """Insert or refresh a tracked post; keep first_seen and peak_score."""
    posts = state.setdefault("posts", {})
    nowiso = now.isoformat()
    entry = posts.get(key)
    if entry is None:
        entry = {"first_seen": nowiso, "topic": topic, "peak_score": score}
        posts[key] = entry
    entry["last_seen"] = nowiso
    entry["last_raw"] = raw
    entry["peak_score"] = max(entry.get("peak_score", 0.0), score)
    return entry


def age_out_state(state, now, max_age_hours=24):
    posts = state.get("posts", {})
    for key in [k for k, e in posts.items()
                if _hours_since(e.get("last_seen"), now) > max_age_hours]:
        del posts[key]


def get_wizard(data):
    return data.setdefault("wizard", {"step": "idle"})


def reset_wizard(data):
    data["wizard"] = {"step": "idle"}
    return data


DEFAULT_MCP_GATEWAY_URL = "https://gw-watermelon-111735.agentbase-gateway.aiplatform.vngcloud.vn/zernio"
DEFAULT_PUBLIC_URL = "https://openclaw-111735-epaphras.agentbase-runtime.aiplatform.vngcloud.vn"


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
    if "result" not in envelope:
        raise ConfigError(f"mcp error: {envelope.get('error')}")
    return ast.literal_eval(envelope["result"]["content"][0]["text"])


def _get_accounts_payload():
    """POST to the zernio MCP gateway for the account list."""
    return _mcp_call("accounts_list_accounts", {})


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
    base = os.environ.get("EPAPHRAS_PUBLIC_URL", DEFAULT_PUBLIC_URL)
    return base.rstrip("/") + "/zernio/webhook"


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
    mode_platforms = {(p["platform"] if isinstance(p, dict) else p).lower()
                      for p in mode.get("platforms", [])}
    if platform and mode_platforms and platform.lower() not in mode_platforms:
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
    # Single-user bot: no concurrent callers; the list-then-create TOCTOU window is safe.
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
        if not wid:
            wid = _wh_id(_find_webhook_by_url(_list_webhooks(), url) or {})
        if not wid:
            raise ConfigError("webhook created but id unresolvable")
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
    wh.update({"id": _wh_id(existing), "url": url, "events": list(WEBHOOK_EVENTS),
               "synced_at": _now_iso()})
    return {"ok": True, "synced": True, "drifted": drifted}


def webhook_status(data):
    wh = webhook_config(data)
    return {"enabled": wh.get("enabled", False), "id": wh.get("id"),
            "url": wh.get("url"), "events": wh.get("events", []),
            "synced_at": wh.get("synced_at")}


def fetch_accounts():
    """Return usable accounts as [{accountId, platform, handle}]. Raises ConfigError
    on network/HTTP failure or invalid response."""
    try:
        payload = _get_accounts_payload()
    except (json.JSONDecodeError, KeyError, ValueError, SyntaxError) as e:
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


def confirm_delete_mode(data, mode_id):
    if mode_id not in data["modes"]:
        raise ConfigError(f"unknown mode: {mode_id}")
    name = data["modes"][mode_id]["name"]
    rows = [[
        {"text": "✅ Yes, delete", "callback_data": f"cb_confirmdel:mode:{mode_id}"},
        {"text": "✖ No", "callback_data": "cb_cancel"},
    ]]
    return {"text": f"Delete mode \"{name}\"?", "buttons": rows, "inline_keyboard": rows}


def confirm_delete_topic(data, mode_id, topic_id):
    if mode_id not in data["modes"] or topic_id not in data["modes"][mode_id]["topics"]:
        raise ConfigError("unknown topic")
    label = data["modes"][mode_id]["topics"][topic_id]["label"]
    rows = [[
        {"text": "✅ Yes, delete", "callback_data": f"cb_confirmdel:topic:{mode_id}:{topic_id}"},
        {"text": "✖ No", "callback_data": "cb_cancel"},
    ]]
    return {"text": f"Delete topic \"{label}\"?", "buttons": rows, "inline_keyboard": rows}


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


def cancel(data):
    reset_wizard(data)
    return render_modes(data)


def _maybe_sync(data):
    """Best-effort drift-correct after a mutation; never raises."""
    if not webhook_config(data).get("enabled"):
        return
    try:
        sync_webhook(data)
    except ConfigError:
        pass  # registration is a background concern; never block the panel


def toggle_notifications(data):
    if webhook_config(data).get("enabled"):
        disable_webhook(data)
    else:
        enable_webhook(data)
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
    if cb == "cb_notif":
        return toggle_notifications(data)
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
    on = webhook_config(data).get("enabled")
    rows.append([{"text": f"🔔 Notifications: {'On' if on else 'Off'}",
                  "callback_data": "cb_notif"}])
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
                 "store-msgid", "get-msgid",
                 "handle-callback", "handle-text", "render-platforms",
                 "webhook-status", "webhook-enable", "webhook-disable",
                 "webhook-sync", "handle-webhook"],
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
        elif args.command == "handle-callback":
            if not args.arg:
                _emit({"error": "handle-callback requires a callback_data argument"})
                return 1
            out = handle_callback(data, args.arg)
            if args.arg != "cb_notif":  # cb_notif already (re)synced via enable/disable
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
        elif args.command == "render-platforms":
            out = render_platforms(data)
            save_config(path, data)  # persist cached account snapshot
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
        return 0
    except ConfigError as e:
        _emit({"error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
