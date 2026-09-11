"""دوال الظل في db.py — بمحاكاة requests (لا شبكة)."""
import json
import db


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text
    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _configure(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_KEY", "k")


def test_update_watch_conditional_filter_and_updated_at(monkeypatch):
    _configure(monkeypatch)
    calls = {}
    def fake_patch(url, headers=None, json=None, timeout=None):
        calls["url"], calls["json"], calls["headers"] = url, json, headers
        return _Resp(200, body=[{"id": 7}])
    monkeypatch.setattr(db.requests, "patch", fake_patch)
    ok = db.shadow_update_watch(7, {"state": "touched"}, expect_state="armed")
    assert ok is True
    assert calls["url"].endswith("/rest/v1/shadow_watches?id=eq.7&state=eq.armed")
    assert calls["json"]["state"] == "touched" and calls["json"]["updated_at"].endswith("Z")
    assert calls["headers"]["Prefer"] == "return=representation"


def test_update_watch_returns_false_when_state_moved(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(db.requests, "patch", lambda *a, **k: _Resp(200, body=[]))
    assert db.shadow_update_watch(7, {"state": "entered"}, expect_state="touched") is False
    monkeypatch.setattr(db.requests, "patch", lambda *a, **k: _Resp(400, text="bad"))
    assert db.shadow_update_watch(7, {"state": "entered"}) is False


def test_insert_event_treats_409_as_success(monkeypatch):
    _configure(monkeypatch)
    sent = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"], sent["json"], sent["prefer"] = url, json, headers.get("Prefer")
        return _Resp(409, text="duplicate key")
    monkeypatch.setattr(db.requests, "post", fake_post)
    assert db.shadow_insert_event(3, "bar", bar_ts="2026-09-10T14:05:00Z",
                                  state_before="touched", state_after="touched",
                                  checks={"cisd": False}) is True
    assert sent["url"].endswith("/rest/v1/shadow_events") and sent["prefer"] == "return=minimal"
    assert sent["json"]["watch_id"] == 3 and sent["json"]["checks"] == {"cisd": False}
    monkeypatch.setattr(db.requests, "post", lambda *a, **k: _Resp(500, text="boom"))
    assert db.shadow_insert_event(3, "bar") is False


def test_insert_watch_and_get_watches(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(db.requests, "post", lambda *a, **k: _Resp(201, body=[{"id": 11}]))
    assert db.shadow_insert_watch({"symbol": "SPY", "direction": "call", "state": "armed"}) == 11
    seen = {}
    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        return _Resp(206, body=[{"id": 11, "state": "armed"}])
    monkeypatch.setattr(db.requests, "get", fake_get)
    rows = db.shadow_get_watches(["armed", "touched"], since_iso="2026-09-10T04:00:00Z")
    assert rows == [{"id": 11, "state": "armed"}]
    assert "state=in.(armed,touched)" in seen["url"] and "registered_at=gte.2026-09-10T04:00:00Z" in seen["url"]


def test_shadow_functions_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "")
    monkeypatch.setattr(db, "SUPABASE_KEY", "")
    monkeypatch.setattr(db, "_get_secret", lambda k: "")
    assert db.shadow_insert_watch({"x": 1}) is None
    assert db.shadow_update_watch(1, {"x": 1}) is False
    assert db.shadow_get_watches() == []
    assert db.shadow_insert_event(1, "bar") is False
