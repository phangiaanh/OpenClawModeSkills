import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import engine
import socialcrawl

FIXTURE = Path(__file__).parent / "fixtures" / "modes.sample.json"


@pytest.fixture
def cfg(tmp_path):
    """A live config file seeded from the fixture, returned as a path."""
    dst = tmp_path / "modes.json"
    dst.write_text(FIXTURE.read_text())
    return dst


def cfg_path():
    return FIXTURE


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
    bad = tmp_path / "bad.json"
    bad.write_text("{unclosed json")
    with pytest.raises(engine.ConfigError):
        engine.load_config(bad)


def test_save_config_writes_backup(cfg):
    data = engine.load_config(cfg)
    engine.save_config(cfg, data)
    assert Path(str(cfg) + ".bak").exists()


def test_save_config_writes_valid_json_with_indent(cfg):
    data = engine.load_config(cfg)
    engine.save_config(cfg, data)
    text = cfg.read_text()
    parsed = json.loads(text)
    assert parsed["current_active_mode"] == "culture_drama"
    assert "  " in text  # indented (2-space)


def test_load_config_rejects_missing_modes_key(tmp_path):
    bad = tmp_path / "no_modes.json"
    bad.write_text('{"current_active_mode": "foo"}\n')
    with pytest.raises(engine.ConfigError, match="missing"):
        engine.load_config(bad)


def test_ensure_file_raises_config_error_when_template_missing(tmp_path):
    target = tmp_path / "modes.yaml"
    missing_template = tmp_path / "nonexistent.yaml"
    with pytest.raises(engine.ConfigError, match="template not found"):
        engine.ensure_file(target, template=str(missing_template))


def test_render_modes_marks_active():
    data = engine.load_config(cfg_path())
    out = engine.render_modes(data)
    assert "buttons" in out and "text" in out and "inline_keyboard" in out
    rows = out["buttons"]
    # 4 mode rows + "New mode" + "Notifications"
    assert len(rows) == 6
    flat = [b for row in rows for b in row]
    active = next(b for b in flat if b["callback_data"] == "cb_setmode:culture_drama")
    assert "▶️" in active["text"]
    inactive = next(b for b in flat if b["callback_data"] == "cb_setmode:deep_research")
    assert "▶️" not in inactive["text"]
    # every mode row has a delete button
    assert any(b["callback_data"] == "cb_delmode:culture_drama" for b in flat)
    # new-mode affordance present (now second-to-last; notifications is last)
    assert rows[-2][0]["callback_data"] == "cb_newmode"
    assert rows[-1][0]["callback_data"] == "cb_notif"
    assert out["inline_keyboard"] == out["buttons"]


def test_render_topics_shows_toggle_marks():
    data = engine.load_config(cfg_path())
    out = engine.render_topics(data, "culture_drama")
    assert out["inline_keyboard"] == out["buttons"]
    flat = [b for row in out["buttons"] for b in row]
    esports = next(b for b in flat if b["callback_data"] == "cb_toggle:esports")
    assert esports["text"].startswith("✅")   # active: true in fixture
    memes = next(b for b in flat if b["callback_data"] == "cb_toggle:viral_memes")
    assert memes["text"].startswith("⬜")      # active: false
    # per-topic delete carries mode + topic id
    assert any(b["callback_data"] == "cb_deltopic:culture_drama:esports" for b in flat)
    # add-topic then back are the last two rows
    assert out["buttons"][-2][0]["callback_data"] == "cb_addtopic:culture_drama"
    assert out["buttons"][-1][0]["callback_data"] == "cb_back"
    # legacy string platforms still render (fixture has ["TikTok", "Threads"])
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


def run_cli(cfg, *args):
    """Invoke engine.py as a subprocess against cfg; return (rc, parsed_json)."""
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "engine.py"), *args, "--file", str(cfg)],
        capture_output=True, text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_cli_render_modes(cfg):
    rc, out = run_cli(cfg, "render-modes")
    assert rc == 0
    assert len(out["buttons"]) == 6


def test_cli_setmode_persists_and_returns_topics(cfg):
    rc, out = run_cli(cfg, "setmode", "global_news")
    assert rc == 0
    assert "🚨" in out["text"]
    assert engine.load_config(cfg)["current_active_mode"] == "global_news"


def test_cli_toggle_persists(cfg):
    rc, out = run_cli(cfg, "toggle", "viral_memes")
    assert rc == 0
    assert engine.load_config(cfg)["modes"]["culture_drama"]["topics"]["viral_memes"]["active"] is True


def test_cli_render_topics_with_mode_flag(cfg):
    rc, out = run_cli(cfg, "render-topics", "--mode", "deep_research")
    assert rc == 0
    assert "📚" in out["text"]


def test_cli_unknown_id_returns_error_envelope(cfg):
    rc, out = run_cli(cfg, "setmode", "ghost")
    assert rc == 1
    assert "error" in out


def test_cli_init_seeds(tmp_path):
    target = tmp_path / "fresh.yaml"
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "engine.py"), "init", "--file", str(target)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert target.exists()


def test_toggle_falls_back_to_search_when_active_mode_missing():
    data = {"modes": {"deep_research": {"topics": {"academic_papers": {"active": True}}}}}
    engine.toggle(data, "academic_papers")
    assert data["modes"]["deep_research"]["topics"]["academic_papers"]["active"] is False
    assert data["current_active_mode"] == "deep_research"


def test_toggle_raises_config_error_when_topic_in_no_mode():
    data = {"current_active_mode": None, "modes": {"deep_research": {"topics": {"academic_papers": {"active": True}}}}}
    with pytest.raises(engine.ConfigError):
        engine.toggle(data, "nonexistent_topic")


def test_cli_setmode_without_arg_returns_error(cfg):
    rc, out = run_cli(cfg, "setmode")
    assert rc == 1
    assert "error" in out
    assert "mode_id" in out["error"]


def test_cli_toggle_without_arg_returns_error(cfg):
    rc, out = run_cli(cfg, "toggle")
    assert rc == 1
    assert "error" in out
    assert "topic_id" in out["error"]


def test_cli_render_topics_with_positional_arg_returns_error(cfg):
    rc, out = run_cli(cfg, "render-topics", "culture_drama")
    assert rc == 1
    assert "error" in out
    assert "--mode" in out["error"]


def test_platform_label_object_with_handle():
    entry = {"accountId": "abc", "platform": "threads", "handle": "wintermelonely"}
    assert engine.platform_label(entry) == "🧵 threads · @wintermelonely"


def test_platform_label_object_unknown_platform_uses_globe():
    assert engine.platform_label({"platform": "mastodon"}) == "🌐 mastodon"


def test_platform_label_legacy_string_passthrough():
    assert engine.platform_label("LinkedIn") == "LinkedIn"


def test_slugify_basic():
    assert engine._slugify("My Cool Mode!") == "my_cool_mode"


def test_slugify_caps_length_and_fallback():
    assert len(engine._slugify("x" * 50)) <= 18
    assert engine._slugify("!!!") == "mode"


def test_gen_id_unique_suffix():
    existing = {"news", "news_2"}
    assert engine.gen_id(existing, "news") == "news_3"
    assert engine.gen_id(existing, "tech") == "tech"


def test_get_wizard_defaults_to_idle():
    data = {"modes": {}}
    assert engine.get_wizard(data)["step"] == "idle"
    assert data["wizard"]["step"] == "idle"  # written through


def test_reset_wizard_clears_state():
    data = {"modes": {}, "wizard": {"step": "await_name", "draft": {"name": "x"}}}
    engine.reset_wizard(data)
    assert data["wizard"] == {"step": "idle"}


import json as _json


def test_start_new_mode_enters_await_name():
    data = {"current_active_mode": "x", "modes": {}}
    out = engine.start_new_mode(data)
    assert data["wizard"]["step"] == "await_name"
    assert out["buttons"][-1][0]["callback_data"] == "cb_cancel"


def test_submit_name_advances_to_pick_platforms():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    out = engine.submit_name(data, "  Crypto Watch  ")
    assert data["wizard"]["step"] == "pick_platforms"
    assert data["wizard"]["draft"]["name"] == "Crypto Watch"
    assert any(b["callback_data"] == "cb_createmode"
               for row in out["buttons"] for b in row)


def test_submit_name_rejects_empty():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    out = engine.submit_name(data, "   ")
    assert out["text"].startswith("⚠️")
    assert data["wizard"]["step"] == "await_name"  # stays



def test_start_add_topic_sets_target():
    data = _json.loads(FIXTURE.read_text())
    out = engine.start_add_topic(data, "global_news")
    assert data["wizard"]["step"] == "await_topic"
    assert data["wizard"]["target_mode_id"] == "global_news"
    assert "topic name" in out["text"].lower()


def test_start_add_topic_unknown_mode_raises():
    data = _json.loads(FIXTURE.read_text())
    with pytest.raises(engine.ConfigError):
        engine.start_add_topic(data, "ghost")


def test_submit_topic_adds_active_topic():
    data = _json.loads(FIXTURE.read_text())
    engine.start_add_topic(data, "global_news")
    out = engine.submit_topic(data, "Oil Prices")
    topics = data["modes"]["global_news"]["topics"]
    assert "oil_prices" in topics
    assert topics["oil_prices"] == {"label": "Oil Prices", "active": True}
    assert data["wizard"]["step"] == "idle"
    assert "🚨" in out["text"]


def test_submit_topic_rejects_empty():
    data = _json.loads(FIXTURE.read_text())
    engine.start_add_topic(data, "global_news")
    out = engine.submit_topic(data, "   ")
    assert out["text"].startswith("⚠️")
    assert data["wizard"]["step"] == "await_topic"  # stays


def test_confirm_delete_mode_offers_yes_no():
    data = _json.loads(FIXTURE.read_text())
    out = engine.confirm_delete_mode(data, "global_news")
    flat = [b for row in out["buttons"] for b in row]
    assert any(b["callback_data"] == "cb_confirmdel:mode:global_news" for b in flat)
    assert any(b["callback_data"] == "cb_cancel" for b in flat)
    # nothing deleted yet
    assert "global_news" in data["modes"]


def test_perform_delete_mode_removes_and_reassigns_active():
    data = _json.loads(FIXTURE.read_text())  # active = culture_drama
    out = engine.perform_delete(data, "mode:culture_drama")
    assert "culture_drama" not in data["modes"]
    assert data["current_active_mode"] in data["modes"]
    assert out["buttons"][-2][0]["callback_data"] == "cb_newmode"  # Screen 1 (notifications is last)
    assert out["buttons"][-1][0]["callback_data"] == "cb_notif"


def test_perform_delete_topic_removes_only_target():
    data = _json.loads(FIXTURE.read_text())
    engine.perform_delete(data, "topic:global_news:disasters")
    topics = data["modes"]["global_news"]["topics"]
    assert "disasters" not in topics
    assert "market_meltdown" in topics  # sibling intact


def test_handle_callback_routes_setmode():
    data = _json.loads(FIXTURE.read_text())
    out = engine.handle_callback(data, "cb_setmode:global_news")
    assert data["current_active_mode"] == "global_news"
    assert "🚨" in out["text"]


def test_handle_callback_routes_toggle():
    data = _json.loads(FIXTURE.read_text())
    engine.handle_callback(data, "cb_toggle:viral_memes")
    assert data["modes"]["culture_drama"]["topics"]["viral_memes"]["active"] is True


def test_handle_callback_deltopic_parses_compound_arg():
    data = _json.loads(FIXTURE.read_text())
    out = engine.handle_callback(data, "cb_deltopic:global_news:disasters")
    flat = [b for row in out["buttons"] for b in row]
    assert any(b["callback_data"] == "cb_confirmdel:topic:global_news:disasters" for b in flat)


def test_handle_callback_cancel_resets_to_modes():
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_callback(data, "cb_cancel")
    assert data["wizard"]["step"] == "idle"
    assert out["buttons"][-2][0]["callback_data"] == "cb_newmode"
    assert out["buttons"][-1][0]["callback_data"] == "cb_notif"


def test_handle_callback_unknown_raises():
    data = _json.loads(FIXTURE.read_text())
    with pytest.raises(engine.ConfigError):
        engine.handle_callback(data, "cb_bogus:1")


def test_handle_text_idle_not_handled():
    data = _json.loads(FIXTURE.read_text())
    assert engine.handle_text(data, "hello") == {"handled": False}


def test_handle_text_await_name_handled():
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_text(data, "Crypto Watch")
    assert out["handled"] is True
    assert data["wizard"]["step"] == "pick_platforms"
    assert "buttons" in out and "inline_keyboard" in out


def test_handle_text_slash_command_cancels_and_passes_through():
    data = _json.loads(FIXTURE.read_text())
    engine.start_new_mode(data)
    out = engine.handle_text(data, "/epaphras")
    assert out == {"handled": False}
    assert data["wizard"]["step"] == "idle"  # wizard reset


def test_cli_handle_callback_newmode_persists(cfg):
    rc, out = run_cli(cfg, "handle-callback", "cb_newmode")
    assert rc == 0
    assert engine.load_config(cfg)["wizard"]["step"] == "await_name"


def test_cli_handle_text_idle_not_handled_no_write(cfg):
    before = cfg.read_text()
    rc, out = run_cli(cfg, "handle-text", "random chatter")
    assert rc == 0
    assert out == {"handled": False}
    assert cfg.read_text() == before  # idle => no save


def test_cli_handle_callback_unknown_error_envelope(cfg):
    rc, out = run_cli(cfg, "handle-callback", "cb_bogus:1")
    assert rc == 1
    assert "error" in out




import socialcrawl


def test_sc_get_parses_envelope(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return (b'{"success": true, "platform": "threads", '
                    b'"data": {"results": [{"id": "x"}]}, "credits_remaining": 940}')

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["key"] = req.headers.get("X-api-key")
        return FakeResp()

    monkeypatch.setattr(socialcrawl.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "sc_test")
    env = socialcrawl._sc_get("/threads/search", {"query": "esports", "start_date": None})
    assert env["credits_remaining"] == 940
    assert env["data"]["results"] == [{"id": "x"}]
    assert "query=esports" in captured["url"]
    assert "start_date" not in captured["url"]   # None params dropped
    assert captured["key"] == "sc_test"


def test_sc_get_missing_key_raises(monkeypatch):
    monkeypatch.delenv("SOCIALCRAWL_API_KEY", raising=False)
    with pytest.raises(socialcrawl.SocialCrawlError, match="SOCIALCRAWL_API_KEY"):
        socialcrawl._sc_get("/threads/search", {"query": "x"})


def test_sc_get_unsuccessful_envelope_raises(monkeypatch):
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"success": false, "error": "bad query"}'
    monkeypatch.setattr(socialcrawl.urllib.request, "urlopen",
                        lambda req, timeout=None, context=None: FakeResp())
    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "sc_test")
    with pytest.raises(socialcrawl.SocialCrawlError, match="api error"):
        socialcrawl._sc_get("/threads/search", {"query": "x"})


def _sc_fixture(name):
    return _json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_normalize_post_surfaces_language():
    item = _sc_fixture("threads_search.sample.json")["data"]["items"][0]
    assert socialcrawl.normalize_threads(item)["language"] == "en"
    # missing computed.language -> "" (never KeyError)
    bare = {"post": {"id": "x"}, "computed": {}}
    assert socialcrawl.normalize_threads(bare)["language"] == ""


def test_normalize_threads_maps_unified_fields():
    item = _sc_fixture("threads_search.sample.json")["data"]["items"][0]
    rec = socialcrawl.normalize_threads(item)
    assert rec["post_id"] == "th_1"
    assert rec["text"] == "huge esports drama unfolding right now"
    assert rec["author"] == {"handle": "gamer", "followers": 12000}
    assert rec["likes"] == 820 and rec["comments"] == 140 and rec["shares"] == 260
    assert rec["views"] == 50000
    assert rec["created"].startswith("2025-")


def test_normalize_tiktok_maps_stats_and_epoch():
    item = _sc_fixture("tiktok_search.sample.json")["data"]["items"][0]
    rec = socialcrawl.normalize_tiktok(item)
    assert rec["likes"] == 120000 and rec["comments"] == 8000 and rec["shares"] == 30000
    assert rec["views"] == 1500000 and rec["reach"] == 1500000
    assert rec["created"].startswith("2025-")


def test_normalize_reddit_maps_fields():
    item = _sc_fixture("reddit_search.sample.json")["data"]["items"][0]
    rec = socialcrawl.normalize_reddit(item)
    assert rec["likes"] == 2400 and rec["comments"] == 540 and rec["shares"] == 0
    assert rec["text"] == "Esports org implodes full breakdown of the drama"
    assert rec["author"] == {"handle": "redditor", "followers": 0}


def test_search_adapter_returns_records_and_credits(monkeypatch):
    monkeypatch.setattr(socialcrawl, "_sc_get",
                        lambda path, params: _sc_fixture("reddit_search.sample.json"))
    records, credits = socialcrawl.search_reddit("esports", "24h")
    assert credits == 939
    assert len(records) == 1 and records[0]["post_id"] == "rd_1"


def test_search_adapters_capability_map_keys():
    assert set(socialcrawl.SEARCH_ADAPTERS) == {"threads", "tiktok", "reddit"}


def test_topic_query_prefers_query_then_label():
    assert engine.topic_query({"label": "Art", "query": "digital art"}) == "digital art"
    assert engine.topic_query({"label": "Esports"}) == "Esports"


def test_raw_engagement_weights_comments_and_shares():
    rec = {"likes": 100, "comments": 10, "shares": 5, "reach": 1000}
    w = {"w_like": 1, "w_comment": 2, "w_share": 2, "w_reach": 1}
    # 100*1 + 10*2 + 5*2 + 1000*1 = 1130
    assert engine.raw_engagement(rec, w) == 1130


def test_platform_baseline_is_median_with_guard():
    assert engine.platform_baseline([10, 20, 30]) == 20
    assert engine.platform_baseline([10, 20, 30, 40]) == 25
    assert engine.platform_baseline([]) == 1.0       # empty guard
    assert engine.platform_baseline([0, 0]) == 1.0    # zero-median guard


def test_magnitude_divides_by_baseline():
    assert engine.magnitude(100, 20) == 5.0
    assert engine.magnitude(100, 0) == 100            # baseline 0 -> raw


def test_velocity_is_clamped_nonnegative_rate():
    assert engine.velocity(300, 100, 2.0) == 100.0    # (300-100)/2
    assert engine.velocity(50, 100, 2.0) == 0.0       # falling -> 0
    assert engine.velocity(300, 100, 0) == 0.0        # no elapsed time -> 0


def test_recency_decays_with_age():
    fresh = engine.recency(0, 1.5)
    old = engine.recency(48, 1.5)
    assert fresh > old > 0


def test_trend_score_blends_magnitude_and_velocity():
    # (0.6*10 + 0.4*5) * 1.0 = 8.0
    assert engine.trend_score(10, 5, 0.6, 1.0) == 8.0


def test_passes_floor_or_semantics():
    floor = {"views": 100000, "likes": 10000}
    assert engine.passes_floor({"views": 150000, "likes": 0}, floor) is True   # views clears
    assert engine.passes_floor({"views": 0, "likes": 12000}, floor) is True    # likes clears
    assert engine.passes_floor({"views": 5, "likes": 5}, floor) is False
    assert engine.passes_floor({"likes": 1}, {}) is True                        # no floor -> pass


def test_hours_since_parses_iso_and_z():
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    assert engine._hours_since("2026-06-13T10:00:00+00:00", now) == 2.0
    assert engine._hours_since("2026-06-13T10:00:00Z", now) == 2.0
    assert engine._hours_since(None, now) == 0.0      # missing -> 0


def test_load_state_missing_returns_empty(tmp_path):
    assert engine.load_state(tmp_path / "nope.json") == {"posts": {}}


def test_load_state_corrupt_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert engine.load_state(p) == {"posts": {}}


def test_update_state_inserts_then_tracks_peak():
    state = {"posts": {}}
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    e1 = engine.update_state(state, "tiktok:1", 500, now, 0.4, "esports")
    assert e1["first_seen"] == now.isoformat() and e1["last_raw"] == 500
    later = datetime(2026, 6, 13, 13, 0, tzinfo=timezone.utc)
    e2 = engine.update_state(state, "tiktok:1", 900, later, 0.2, "esports")
    assert e2["first_seen"] == now.isoformat()       # unchanged
    assert e2["last_raw"] == 900
    assert e2["peak_score"] == 0.4                    # max kept


def test_age_out_state_drops_stale_entries():
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    state = {"posts": {
        "tiktok:fresh": {"last_seen": "2026-06-13T11:00:00+00:00"},
        "tiktok:stale": {"last_seen": "2026-06-11T11:00:00+00:00"},
    }}
    engine.age_out_state(state, now, max_age_hours=24)
    assert "tiktok:fresh" in state["posts"]
    assert "tiktok:stale" not in state["posts"]


def test_save_then_load_state_roundtrips(tmp_path):
    p = tmp_path / "state.json"
    engine.save_state(p, {"posts": {"x:1": {"last_raw": 5}}})
    assert engine.load_state(p)["posts"]["x:1"]["last_raw"] == 5


def test_poll_config_installs_defaults():
    data = {"modes": {}}
    pc = engine.poll_config(data)
    assert pc["interval_minutes"] == 60
    assert pc["top_n_per_platform_topic"] == 3
    assert pc["window"]["tz"] == "Asia/Ho_Chi_Minh"
    assert data["poll"] is pc                       # installed onto data
    # defaults are independent copies, not the shared module constant
    pc["interval_minutes"] = 5
    assert engine.DEFAULT_POLL["interval_minutes"] == 60


def test_in_window_respects_local_time():
    win = {"start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh"}  # UTC+7
    # 02:00 UTC == 09:00 ICT -> inside
    assert engine.in_window(datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc), win) is True
    # 14:00 UTC == 21:00 ICT -> outside
    assert engine.in_window(datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc), win) is False


def test_poll_gate_blocks_disabled_and_no_mode():
    now = datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc)   # 09:00 ICT, inside window
    data = {"modes": {}, "poll": {"enabled": False,
            "window": {"start": "08:00", "end": "20:00", "tz": "Asia/Ho_Chi_Minh"}}}
    assert engine.poll_gate(data, now)["reason"] == "disabled"
    data["poll"]["enabled"] = True
    data["current_active_mode"] = None
    assert engine.poll_gate(data, now)["reason"] == "no active mode"


def _poll_data():
    return {
        "current_active_mode": "culture_drama",
        "modes": {"culture_drama": {
            "name": "Drama & Cultural Pulse", "icon": "🎭",
            "platforms": ["tiktok", "reddit"],
            "topics": {"esports": {"label": "Esports", "query": "esports", "active": True},
                       "music": {"label": "Music", "query": "music", "active": False}},
        }},
        "poll": copy.deepcopy(engine.DEFAULT_POLL),
    }


def _now_inside():
    return datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc)  # 09:00 ICT


def test_run_poll_skips_outside_window(tmp_path):
    data = _poll_data()
    out = engine.run_poll(
        data, now=datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc),
        search_fn=lambda *a: ([], 100), capable_platforms={"tiktok", "reddit"},
        state={"posts": {}}, log_path=tmp_path / "log.jsonl")
    assert out["skipped"] is True and out["reason"] == "outside window"


def test_run_poll_logs_top_n_per_platform_and_applies_floor(tmp_path):
    data = _poll_data()
    data["poll"]["top_n_per_platform_topic"] = 1
    # tiktok: one post clears the 100k-views floor, one does not
    tiktok = [
        {"post_id": "tt_big", "url": "u", "text": "t", "author": {"handle": "a", "followers": 1},
         "created": "2026-06-13T01:00:00+00:00", "likes": 50000, "comments": 9000,
         "shares": 9000, "views": 2000000, "reach": 2000000},
        {"post_id": "tt_small", "url": "u", "text": "t", "author": {"handle": "b", "followers": 1},
         "created": "2026-06-13T01:00:00+00:00", "likes": 1, "comments": 1,
         "shares": 1, "views": 10, "reach": 10},
    ]
    reddit = [
        {"post_id": "rd_1", "url": "u", "text": "t", "author": {"handle": "c", "followers": 0},
         "created": "2026-06-13T01:00:00+00:00", "likes": 3000, "comments": 800,
         "shares": 0, "views": 0, "reach": 0},
    ]

    def search_fn(platform, query, lookback):
        return ({"tiktok": tiktok, "reddit": reddit}[platform], 500)

    log = tmp_path / "log.jsonl"
    out = engine.run_poll(data, now=_now_inside(), search_fn=search_fn,
                          capable_platforms={"tiktok", "reddit"},
                          state={"posts": {}}, log_path=log)
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    ids = {l["post_id"] for l in lines}
    assert ids == {"tt_big", "rd_1"}          # tt_small filtered by floor; top-1 each platform
    assert all(l["topic"] == "esports" for l in lines)   # music inactive, not polled
    assert out["logged"] == 2 and out["polled"] == 2     # 1 topic x 2 platforms


def test_run_poll_continues_when_one_platform_fails(tmp_path):
    data = _poll_data()
    reddit = [{"post_id": "rd_1", "url": "u", "text": "t",
               "author": {"handle": "c", "followers": 0},
               "created": "2026-06-13T01:00:00+00:00", "likes": 3000,
               "comments": 800, "shares": 0, "views": 0, "reach": 0}]

    def search_fn(platform, query, lookback):
        if platform == "tiktok":
            raise socialcrawl.SocialCrawlError("boom")
        return (reddit, 500)

    log = tmp_path / "log.jsonl"
    out = engine.run_poll(data, now=_now_inside(), search_fn=search_fn,
                          capable_platforms={"tiktok", "reddit"},
                          state={"posts": {}}, log_path=log)
    assert any("tiktok" in m for m in out["markers"])
    assert out["logged"] == 1                  # reddit still logged


def test_run_poll_computes_velocity_from_state(tmp_path):
    data = _poll_data()
    data["modes"]["culture_drama"]["platforms"] = ["reddit"]
    prev = datetime(2026, 6, 13, 1, 0, tzinfo=timezone.utc)
    state = {"posts": {"reddit:rd_1": {"first_seen": prev.isoformat(),
             "last_seen": prev.isoformat(), "last_raw": 100.0, "peak_score": 0.1,
             "topic": "esports"}}}
    reddit = [{"post_id": "rd_1", "url": "u", "text": "t",
               "author": {"handle": "c", "followers": 0},
               "created": "2026-06-13T01:00:00+00:00", "likes": 3000,
               "comments": 800, "shares": 0, "views": 0, "reach": 0}]
    log = tmp_path / "log.jsonl"
    engine.run_poll(data, now=_now_inside(), search_fn=lambda *a: (reddit, 500),
                    capable_platforms={"reddit"}, state=state, log_path=log)
    rec = json.loads(log.read_text().splitlines()[0])
    assert rec["velocity"] > 0                 # raw grew vs last_raw over 1h
    assert rec["hours_trending"] == 1.0        # first_seen 1h before now


def test_cli_poll_skips_when_disabled(cfg, monkeypatch):
    # disable polling in the live config, then run the CLI: no network, rc 0
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = False
    engine.save_config(cfg, data)
    rc, out = run_cli(cfg, "poll")
    assert rc == 0
    assert out["skipped"] is True and out["reason"] == "disabled"


def test_cli_poll_missing_key_errors_when_work_due(cfg, monkeypatch):
    data = engine.load_config(cfg)
    pc = engine.poll_config(data)
    pc["enabled"] = True
    pc["window"] = {"start": "00:00", "end": "23:59", "tz": "UTC"}  # always inside
    engine.save_config(cfg, data)
    env = dict(os.environ); env.pop("SOCIALCRAWL_API_KEY", None)
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "engine.py"), "poll", "--file", str(cfg)],
        capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    assert "SOCIALCRAWL_API_KEY" in json.loads(proc.stdout)["error"]


def test_cli_poll_lock_blocks_second_run(cfg, monkeypatch):
    data = engine.load_config(cfg)
    pc = engine.poll_config(data)
    pc["window"] = {"start": "00:00", "end": "23:59", "tz": "UTC"}
    engine.save_config(cfg, data)
    lock = engine._poll_lock_path()
    lock.write_text("999999")
    try:
        rc, out = run_cli(cfg, "poll")
        assert rc == 0 and out["reason"] == "locked"
    finally:
        lock.unlink(missing_ok=True)


def test_render_modes_shows_polling_off_by_default(cfg):
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = False      # explicitly off
    flat = [b for row in engine.render_modes(data)["buttons"] for b in row]
    poll_btn = next(b for b in flat if b["callback_data"] == "cb_notif")
    assert "📡" in poll_btn["text"] and "Off" in poll_btn["text"]


def test_render_modes_shows_polling_on_when_enabled(cfg):
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = True
    flat = [b for row in engine.render_modes(data)["buttons"] for b in row]
    poll_btn = next(b for b in flat if b["callback_data"] == "cb_notif")
    assert "On" in poll_btn["text"]


def test_cb_notif_toggles_poll_enabled(cfg):
    data = engine.load_config(cfg)
    engine.poll_config(data)["enabled"] = False
    engine.handle_callback(data, "cb_notif")
    assert engine.poll_config(data)["enabled"] is True
    engine.handle_callback(data, "cb_notif")
    assert engine.poll_config(data)["enabled"] is False


def _picking_data(name="My Mode"):
    return {"current_active_mode": "x", "modes": {},
            "wizard": {"step": "pick_platforms", "draft": {"name": name, "platforms": []}}}


def test_render_platforms_lists_capability_map():
    data = _picking_data()
    flat = [b for row in engine.render_platforms(data)["buttons"] for b in row]
    cbs = {b["callback_data"] for b in flat}
    assert "cb_pickplat:threads" in cbs
    assert "cb_pickplat:tiktok" in cbs
    assert "cb_pickplat:reddit" in cbs
    assert "cb_createmode" in cbs
    assert flat[-1]["callback_data"] == "cb_cancel"


def test_pick_platform_toggles_string_names():
    data = _picking_data()
    engine.pick_platform(data, "tiktok")
    engine.pick_platform(data, "reddit")
    assert data["wizard"]["draft"]["platforms"] == ["tiktok", "reddit"]
    engine.pick_platform(data, "tiktok")            # toggle off
    assert data["wizard"]["draft"]["platforms"] == ["reddit"]


def test_pick_platform_rejects_uncapable():
    data = _picking_data()
    with pytest.raises(engine.ConfigError):
        engine.pick_platform(data, "facebook")


def test_create_mode_stores_string_platforms_and_activates():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Crypto Watch")
    engine.pick_platform(data, "reddit")
    engine.create_mode(data)
    new_id = data["current_active_mode"]
    assert new_id == "crypto_watch"
    assert data["modes"][new_id]["platforms"] == ["reddit"]
    assert data["modes"][new_id]["topics"] == {}


def test_create_mode_requires_a_platform():
    data = {"current_active_mode": "x", "modes": {}}
    engine.start_new_mode(data)
    engine.submit_name(data, "Empty")
    engine.create_mode(data)                         # none picked
    assert data["wizard"]["step"] == "pick_platforms"
    assert "Empty" not in [m.get("name") for m in data["modes"].values()]


def test_default_template_is_single_mode_with_poll_block():
    tmpl = _json.loads(
        (Path(__file__).parent.parent / "templates" / "modes.default.json").read_text())
    assert list(tmpl["modes"]) == ["culture_drama"]
    mode = tmpl["modes"]["culture_drama"]
    assert mode["platforms"] == ["threads", "tiktok", "reddit"]
    assert set(mode["topics"]) == {"esports", "showbiz", "music", "art", "technology"}
    assert mode["topics"]["showbiz"]["query"] == "celebrity"
    assert mode["topics"]["art"]["active"] is False
    assert tmpl["poll"]["interval_minutes"] == 60
    assert tmpl["poll"]["window"]["tz"] == "Asia/Ho_Chi_Minh"
    assert "webhook" not in tmpl
    # the seeded template renders cleanly
    out = engine.render_topics(tmpl, "culture_drama")
    assert "Platforms:" in out["text"]
