"""حقول SignalResult الجديدة (المرحلة ٢) لا تغيّر payload حفظ الإشارة ولا رسالة Telegram."""
from analyzer import SignalResult
import db


def _sig(**over):
    base = dict(symbol="SPY", direction="call", confidence="medium", score=7.0,
                entry_low=99.8, entry_high=100.1, stop=99.3, target1=101.0, target2=101.6,
                entry_type="Order Block 🏛️", current_price=100.0, is_scalp=False,
                expiry="20260918", strike=100.0, rr=1.7, vix=18.0, mtf_score=2)
    base.update(over)
    return SignalResult(**base)


def test_new_fields_default_to_zero():
    s = _sig()
    assert (s.htf_zone_low, s.htf_zone_high, s.atr) == (0.0, 0.0, 0.0)
    assert (s.alt_zone_low, s.alt_zone_high, s.alt_zone_tf, s.alt_zone_type) == (0.0, 0.0, "", "")


def test_save_signal_payload_excludes_new_fields(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_KEY", "k")
    captured = {}

    class _R:
        status_code = 201
        text = ""
        def json(self):
            return [{"id": 1}]

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(json)
        return _R()

    monkeypatch.setattr(db.requests, "post", fake_post)
    s = _sig(htf_zone_tf="daily", htf_zone_type="OB", htf_direction="demand",
             htf_zone_low=99.0, htf_zone_high=100.2, alt_zone_low=98.0, alt_zone_high=99.5,
             alt_zone_tf="4h", alt_zone_type="FVG", atr=0.42)
    assert db.save_signal(s) == 1
    for k in ("htf_zone_low", "htf_zone_high", "alt_zone_low", "alt_zone_high",
              "alt_zone_tf", "alt_zone_type", "atr"):
        assert k not in captured, k
    assert captured["htf_zone_tf"] == "daily" and captured["status"] == "open"


def test_format_message_unaffected():
    from telegram_bot import format_message
    msg = format_message(_sig(htf_zone_tf="daily", htf_zone_type="OB", htf_direction="demand",
                              htf_zone_low=99.0, htf_zone_high=100.2, atr=0.42))
    assert "SPY" in msg and "HTF Zone" in msg and "99.0" not in msg.split("HTF Zone")[1].split("\n")[0]
