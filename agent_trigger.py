"""Fire-and-202 trigger to the SocialAnalyzeAgent /analyze endpoint (stdlib only)."""
import json
import os
import ssl
import urllib.error
import urllib.request


def agent_url() -> str:
    return os.environ.get("EPAPHRAS_AGENT_URL", "")


def callback_url() -> str:
    return os.environ.get("OPENCLAW_PUBLIC_URL", "")


def callback_token() -> str:
    return os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")


def build_payload(*, job_id, mode, topic, tick_id, post, chat_id, message_id,
                  callback_url, callback_token, agent_url):
    return {
        "job_id": job_id,
        "mode": mode,
        "topic": topic,
        "tick_id": tick_id,
        "post": {
            "platform": post.get("platform", ""), "post_id": post.get("post_id", ""),
            "url": post.get("url", ""), "text": post.get("text", ""),
            "author": post.get("author", ""), "language": post.get("language", ""),
            "likes": post.get("likes", 0), "views": post.get("views", 0),
            "comments": post.get("comments", 0), "shares": post.get("shares", 0),
            "score": post.get("score", 0.0), "age_hours": post.get("age_hours", 0.0),
        },
        "delivery": {"chat_id": chat_id, "message_id": message_id},
        "callback": {"url": callback_url, "token": callback_token},
    }


def post_job(url: str, payload: dict, timeout: int = 8) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {"status": "trigger_failed", "error": str(e)}
