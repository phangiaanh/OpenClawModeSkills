#!/usr/bin/env python3
"""Standalone poll daemon — survives container restarts via /root/.openclaw/."""
import json
import os
import ssl
import subprocess
import time
import urllib.request

SKILL_DIR = os.environ.get(
    "EPAPHRAS_SKILL_DIR",
    "/root/.openclaw/workspace/skills/OpenClawModeSkills",
)
LOG = "/tmp/epaphras_poll.log"
INTERVAL = 3600  # seconds


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def send_telegram(token, chat_id, text, buttons):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
    }).encode()
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Telegram API error: {e}")


def tick(token):
    try:
        result = subprocess.run(
            ["python3", os.path.join(SKILL_DIR, "engine.py"), "poll"],
            cwd=SKILL_DIR,
            capture_output=True, text=True, timeout=120,
        )
        out = result.stdout.strip()
        log(out[:500] if out else "(no output)")
        if not out:
            return
        payload = json.loads(out)
        emit = payload.get("emit")
        chat_id = payload.get("chat_id")
        if emit and chat_id:
            send_telegram(token, chat_id, emit["text"], emit["buttons"])
            log(f"SENT to {chat_id}")
    except Exception as e:
        log(f"ERR {e}")


def get_bot_token():
    cfg = os.path.expanduser("~/.openclaw/openclaw.json")
    try:
        import re
        with open(cfg) as f:
            m = re.search(r'"botToken"\s*:\s*"([^"]+)"', f.read())
            if m:
                return m.group(1)
    except Exception:
        pass
    return os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")


def main():
    token = get_bot_token()
    if not token:
        log("ERROR: botToken not found in openclaw.json and OPENCLAW_GATEWAY_TOKEN not set")
        return
    log("DAEMON_STARTED")
    # Run first tick immediately, then every INTERVAL seconds
    tick(token)
    while True:
        time.sleep(INTERVAL)
        tick(token)


if __name__ == "__main__":
    main()
