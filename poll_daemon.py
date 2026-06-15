#!/usr/bin/env python3
"""Hourly polling daemon — runs engine.py poll every 60 minutes."""
import subprocess, time, os, sys, json
import urllib.request, ssl, re

SKILL_DIR = "/root/.openclaw/workspace/skills/OpenClawModeSkills"
LOG = "/tmp/epaphras_poll.log"
PID_FILE = "/tmp/poll_daemon.pid"
INTERVAL = 3600  # 1 hour


def log(msg):
    import datetime
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    line = f"{ts} [POLL_DAEMON] {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


def run_poll():
    try:
        out = subprocess.check_output(
            ["python3", "engine.py", "poll"],
            cwd=SKILL_DIR,
            env=os.environ,
            timeout=120,
            stderr=subprocess.PIPE,
        ).decode().strip()
        log(f"poll result: {out[:300]}")
        return out
    except subprocess.TimeoutExpired:
        log("poll TIMEOUT")
    except subprocess.CalledProcessError as e:
        log(f"poll ERROR exit={e.returncode}: {e.stderr.decode()[:200]}")
    except Exception as e:
        log(f"poll EXCEPTION: {e}")
    return None


def _read_bot_token():
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if t:
        return t
    cfg = os.path.expanduser("~/.openclaw/openclaw.json")
    try:
        with open(cfg) as f:
            m = re.search(r'"botToken"\s*:\s*"([^"]+)"', f.read())
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def send_carousel(chat_id, text, buttons):
    bot_token = _read_bot_token()
    if not bot_token:
        log("no bot_token found — cannot send")
        return
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            log(f"send OK: {r.status}")
    except Exception as e:
        log(f"send ERROR: {e}")


if __name__ == "__main__":
    # Prevent duplicate daemon
    try:
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, 0)  # raises if not alive
        print(f"Daemon already running (PID {old_pid})")
        sys.exit(0)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        pass

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    log(f"DAEMON_STARTED pid={os.getpid()}")
    print(f"Poll daemon started PID={os.getpid()}", flush=True)

    # Run immediately, then hourly
    while True:
        out = run_poll()
        if out:
            try:
                data = json.loads(out)
                if data.get("emit") and data.get("chat_id"):
                    send_carousel(
                        data["chat_id"],
                        data["emit"].get("text", "(no text)"),
                        data["emit"].get("buttons", []),
                    )
            except Exception as e:
                log(f"parse/send ERROR: {e}")
        time.sleep(INTERVAL)
