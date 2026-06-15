import json
import agent_trigger


def test_build_payload_shape():
    snap_post = {"platform": "tiktok", "post_id": "p1", "url": "u", "text": "t",
                 "author": "@a", "language": "en", "likes": 1, "views": 2,
                 "comments": 3, "shares": 4, "score": 0.04, "age_hours": 1.0}
    payload = agent_trigger.build_payload(
        job_id="j1",
        mode={"id": "esports", "label": "Esports", "icon": "🎯"},
        topic={"id": "esports", "label": "Esports", "icon": "🎯"},
        tick_id="123", post=snap_post, chat_id=7, message_id=9,
        callback_url="https://oc", callback_token="TOK", agent_url="https://agent/analyze")
    assert payload["job_id"] == "j1"
    assert payload["post"]["platform"] == "tiktok"
    assert payload["delivery"] == {"chat_id": 7, "message_id": 9}
    assert payload["callback"] == {"url": "https://oc", "token": "TOK"}


def test_post_uses_urlopen(monkeypatch):
    sent = {}
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"status":"accepted"}'
    def fake_urlopen(req, timeout=0, context=None):
        sent["url"] = req.full_url
        sent["data"] = json.loads(req.data.decode())
        return FakeResp()
    monkeypatch.setattr(agent_trigger.urllib.request, "urlopen", fake_urlopen)
    out = agent_trigger.post_job("https://agent/analyze", {"job_id": "j1"})
    assert sent["url"] == "https://agent/analyze"
    assert out["status"] == "accepted"
