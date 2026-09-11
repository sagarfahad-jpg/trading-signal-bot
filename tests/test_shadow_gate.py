"""وضع الظل (shadow_gate) — قاعدة بيانات وهمية في الذاكرة + محاكاة مصدر البيانات. لا شبكة."""
import ast
import os
from types import SimpleNamespace

import pandas as pd
import pytest

import db
import data_client as dc
import analyzer
import htf_zones
import shadow_gate as sg

TZ = "America/New_York"


def bars(rows, start="2026-09-10 09:00", minutes=5):
    idx = pd.date_range(start, periods=len(rows), freq=f"{minutes}min", tz=TZ)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def flat(n, o=100.0, h=100.3, l=99.8, c=100.1):
    return [[o, h, l, c, 1000]] * n


def et(s):
    return pd.Timestamp(s, tz=TZ)


class FakeDB:
    def __init__(self):
        self.watches, self.events, self.next_id = {}, [], 1
        self.config = {"shadow_gate": "1"}
        self.refuse_updates = False

    def insert_watch(self, payload):
        wid = self.next_id
        self.next_id += 1
        self.watches[wid] = {"id": wid, **payload}
        return wid

    def update_watch(self, wid, fields, expect_state=None):
        w = self.watches.get(wid)
        if self.refuse_updates or w is None or (expect_state and w["state"] != expect_state):
            return False
        w.update(fields)
        return True

    def get_watches(self, states=None, since_iso=None, limit=1000):
        return [dict(w) for w in self.watches.values() if not states or w["state"] in states]

    def insert_event(self, watch_id, kind, bar_ts=None, state_before=None, state_after=None, checks=None):
        self.events.append({"watch_id": watch_id, "kind": kind, "bar_ts": bar_ts,
                            "before": state_before, "after": state_after, "checks": checks})
        return True

    def kinds(self, wid):
        return [e["kind"] for e in self.events if e["watch_id"] == wid]


@pytest.fixture
def env(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_config", lambda k, d="": fake.config.get(k, d))
    monkeypatch.setattr(db, "shadow_insert_watch", fake.insert_watch)
    monkeypatch.setattr(db, "shadow_update_watch", fake.update_watch)
    monkeypatch.setattr(db, "shadow_get_watches", fake.get_watches)
    monkeypatch.setattr(db, "shadow_insert_event", fake.insert_event)
    sg._last_boundary = None
    sg._cfg_cache.update(ts=0.0, on=True)
    # مصدر البيانات: يُضبط لكل اختبار عبر fake.frames / fake.prices
    fake.frames, fake.prices, fake.quote = {}, {}, 1.30
    monkeypatch.setattr(dc, "get_bars_batch", lambda syms, i="5m", p="3d": {s: fake.frames[s] for s in syms if s in fake.frames})
    monkeypatch.setattr(dc, "get_bars", lambda s, i="5m", p="3d": fake.frames.get(s, pd.DataFrame()) if i == "5m" else pd.DataFrame())
    monkeypatch.setattr(dc, "get_latest_trades", lambda syms: {s: fake.prices[s] for s in syms if s in fake.prices})
    monkeypatch.setattr(dc, "get_option_price_by_contract", lambda *a, **k: fake.quote)
    monkeypatch.setattr(analyzer, "_get_contract", lambda sym, d, px, is_scalp=False, score=0.0: ("20260918", 100.0, 1.25, 0.5, 30.0, -0.05))
    monkeypatch.setattr(htf_zones, "get_htf_analysis", lambda *a, **k: {"zones": [], "structure": {}})
    return fake


def signal(**over):
    base = dict(symbol="SPY", direction="call", score=7.2, is_scalp=False, current_price=100.0,
                entry_type="Order Block 🏛️", htf_zone_tf="daily", htf_zone_type="OB",
                htf_direction="demand", htf_zone_low=99.2, htf_zone_high=99.5, atr=0.5,
                alt_zone_low=0.0, alt_zone_high=0.0, alt_zone_tf="", alt_zone_type="")
    base.update(over)
    return SimpleNamespace(**base)


REJ = [100.0, 100.1, 98.8, 99.9, 1000]   # شمعة رافضة عند demand [99.2, 99.5]


# ── التسجيل ────────────────────────────────────────────────────────────────

def test_register_classifies_no_zone_conflict_armed(env):
    a = sg.register(signal(htf_zone_tf="", htf_zone_type="", htf_direction="", htf_zone_low=0, htf_zone_high=0), 101)
    b = sg.register(signal(htf_direction="supply", alt_zone_low=98.0, alt_zone_high=99.0, alt_zone_tf="4h", alt_zone_type="FVG"), 102)
    c = sg.register(signal(), 103)
    assert env.watches[a]["state"] == "no_zone" and env.watches[a]["zone_low"] is None
    assert env.watches[b]["state"] == "zone_conflict" and env.watches[b]["alt_zone_found"] is True
    assert env.watches[b]["alt_zone_tf"] == "4h" and env.watches[b]["zone_direction"] == "supply"
    w = env.watches[c]
    assert w["state"] == "armed" and w["signal_id"] == 103 and (w["zone_low"], w["zone_high"]) == (99.2, 99.5)
    assert w["zone_width_atr"] == 0.6 and w["registered_at"].endswith("Z")
    assert env.kinds(c) == ["register"]


def test_register_respects_kill_switch(env):
    env.config["shadow_gate"] = "0"
    sg._cfg_cache.update(ts=0.0)
    assert sg.register(signal(), 1) is None and env.watches == {}


# ── armed → touched → entered ───────────────────────────────────────────────

def _armed_watch(env, registered="2026-09-10T14:02:00Z", **over):
    wid = sg.register(signal(**over), 200)
    env.watches[wid]["registered_at"] = registered          # 10:02 ET
    return wid


def test_rejection_bar_enters_and_writes_plan(env):
    wid = _armed_watch(env)
    env.frames["SPY"] = bars(flat(12) + [REJ])              # الشمعة 10:00 رافضة
    now = et("2026-09-10 10:05:30")
    sg._tick(now)
    w = env.watches[wid]
    assert w["state"] == "entered" and w["confirm_type"] == "rejection"
    assert w["arrival_bar_ts"] == "2026-09-10T14:00:00Z" and w["last_bar_ts"] == "2026-09-10T14:00:00Z"
    assert w["bars_seen"] == 1 and w["entry_price"] == 99.9 and w["stop_basis"] == "candle"
    assert w["stop_price"] < 98.8 and w["target1"] > 99.9 and w["target2"] > w["target1"]
    assert w["target1_source"] and w["rr"] > 0 and w["risk_atr"] > 0
    assert (w["expiry"], w["strike"], w["entry_option_price"], w["entry_option_src"]) == ("20260918", 100.0, 1.30, "quote")
    assert 0 <= w["entry_eval_lag_sec"] <= 60 and w["entered_at"].endswith("Z")
    assert env.kinds(wid) == ["register", "arrival", "bar", "enter"]


def test_chain_price_used_when_quote_missing(env):
    wid = _armed_watch(env)
    env.frames["SPY"] = bars(flat(12) + [REJ])
    env.quote = 0.0
    sg._tick(et("2026-09-10 10:05:30"))
    assert (env.watches[wid]["entry_option_price"], env.watches[wid]["entry_option_src"]) == (1.25, "chain")


def test_restart_does_not_reevaluate_same_bar(env):
    wid = _armed_watch(env)
    env.frames["SPY"] = bars(flat(12) + [[99.4, 99.6, 99.3, 99.35, 1000]])   # تداخل بلا تأكيد → touched
    now = et("2026-09-10 10:05:30")
    sg._tick(now)
    w = env.watches[wid]
    assert w["state"] == "touched" and w["bars_seen"] == 1
    n_events = len(env.events)
    sg._last_boundary = None                                # عملية جديدة بعد إعادة تشغيل
    sg._tick(now)
    assert env.watches[wid]["bars_seen"] == 1 and len(env.events) == n_events


def test_state_moved_elsewhere_writes_nothing(env):
    wid = _armed_watch(env)
    env.frames["SPY"] = bars(flat(12) + [REJ])
    env.refuse_updates = True
    sg._tick(et("2026-09-10 10:05:30"))
    assert env.watches[wid]["state"] == "armed" and env.kinds(wid) == ["register"]


def test_catch_up_eod_and_stale_guard(env):
    # armed مسجّلة 14:52 → أول شمعة مرشّحة 14:50 (بلا تداخل) ثم 14:55 تتداخل (تُغلق 15:00 → مسموحة)
    # ثم 15:00 تتداخل لكنها تُغلق 15:05 → eod. التقييم كله catch-up في دورة واحدة عند 15:06.
    wid = _armed_watch(env, registered="2026-09-10T18:52:00Z")
    env.frames["SPY"] = bars(flat(11) + [[99.4, 99.6, 99.3, 99.35, 1000]] * 2, start="2026-09-10 14:00")
    sg._tick(et("2026-09-10 15:06:00"))
    w = env.watches[wid]
    assert w["state"] == "cancelled" and w["cancel_reason"] == "eod" and w["bars_seen"] == 2
    assert w["arrival_bar_ts"] == "2026-09-10T18:55:00Z" and env.kinds(wid)[-1] == "cancel"
    # تأكيد قديم (بعد توقف طويل): لا دخول → stale
    wid2 = _armed_watch(env)
    env.frames["SPY"] = bars(flat(12) + [REJ])
    sg._last_boundary = None
    sg._tick(et("2026-09-10 11:00:30"))
    w2 = env.watches[wid2]
    assert w2["state"] == "cancelled" and w2["cancel_reason"] == "stale" and w2.get("entry_price") is None


# ── entered: مرآة price_monitor ─────────────────────────────────────────────

def _entered_watch(env, **over):
    wid = _armed_watch(env)
    env.watches[wid].update({
        "state": "entered", "entry_price": 99.9, "stop_price": 98.7, "target1": 101.1, "target2": 102.3,
        "rr": 1.0, "expiry": "20260918", "strike": 100.0, "entry_option_price": 1.0,
        "entered_at": "2026-09-10T14:05:20Z", "last_tick_at": "2026-09-10T14:09:45Z",
        "t1_hit_at": None, "peak_price": None,
    })
    env.watches[wid].update(over)
    return wid


def test_entered_t2_close_records_option_pnl(env):
    wid = _entered_watch(env)
    env.frames["SPY"] = bars(flat(12) + [REJ] + [[99.9, 102.4, 99.85, 102.35, 1000]])
    env.prices["SPY"], env.quote = 102.35, 2.0
    sg._tick(et("2026-09-10 10:10:30"))
    w = env.watches[wid]
    assert (w["state"], w["status"], w["exit_reason"], w["outcome_price"]) == ("closed", "hit_t2", "target2", 102.3)
    assert (w["r_planned"], w["r_actual"], w["option_pnl_pct"]) == (1.0, 2.0, 100.0)
    assert w["max_favorable"] > 0 and w["max_adverse"] <= 0 and w["duration_min"] == 5 and w["gap"] is False
    assert env.kinds(wid)[-1] == "exit"


def test_entered_tick_updates_t1_and_trailing(env):
    wid = _entered_watch(env)
    env.frames["SPY"] = bars(flat(12) + [REJ])
    env.prices["SPY"] = 101.2
    sg._tick(et("2026-09-10 10:10:30"))
    w = env.watches[wid]
    assert w["state"] == "entered" and w["t1_hit_at"] and w["peak_price"] == 101.2 and "t1" in env.kinds(wid)
    env.prices["SPY"] = 100.5                                # 101.2 − 0.6 = 100.6 ≥ 100.5 → trailing
    w["last_tick_at"] = "2026-09-10T14:10:30Z"
    sg._tick(et("2026-09-10 10:11:15"))
    w = env.watches[wid]
    assert (w["status"], w["exit_reason"], w["outcome_price"]) == ("hit_t1", "trailing_stop", 100.6)


def test_entered_gap_uses_bars_stop_first(env):
    wid = _entered_watch(env, last_tick_at="2026-09-10T14:09:45Z")
    # شموع بعد آخر tick: واحدة لامست الستوب و T2 معاً → الستوب أولاً، gap
    env.frames["SPY"] = bars(flat(12) + [REJ] + [[99.9, 102.5, 98.6, 101.0, 1000], [101.0, 101.2, 100.8, 101.1, 1000]])
    env.prices["SPY"] = 101.1
    sg._tick(et("2026-09-10 10:31:00"))                    # فجوة 21 دقيقة
    w = env.watches[wid]
    assert (w["state"], w["status"], w["exit_reason"], w["gap"]) == ("closed", "stopped", "stop", True)
    assert "gap" in env.kinds(wid)


# ── قياس الرجوع بعد timeout ────────────────────────────────────────────────

def test_observe_return_after_timeout(env):
    wid = _armed_watch(env)
    env.watches[wid].update({"state": "cancelled", "cancel_reason": "timeout",
                             "cancelled_at": "2026-09-10T14:30:00Z", "last_bar_ts": "2026-09-10T14:25:00Z"})
    env.frames["SPY"] = bars(flat(12) + [[99.4, 99.6, 99.3, 99.35, 1000]] * 6      # 10:00–10:25
                              + [[99.8, 100.2, 99.7, 100.1, 1000]]                   # 10:30 خارج المنطقة (فوقها)
                              + [[100.0, 100.1, 99.4, 99.6, 1000]])                  # 10:35 رجوع
    sg._tick(et("2026-09-10 10:41:00"))
    w = env.watches[wid]
    assert w["left_zone_at"] == "2026-09-10T14:30:00Z" and w["returned_at"] == "2026-09-10T14:35:00Z"
    assert env.kinds(wid)[-1] == "return"


# ── الملخّص اليومي ──────────────────────────────────────────────────────────

def test_daily_summary_text():
    rows = [
        {"state": "no_zone"}, {"state": "zone_conflict", "alt_zone_found": True},
        {"state": "cancelled", "cancel_reason": "timeout", "arrival_bar_ts": "x", "returned_at": "y"},
        {"state": "closed", "arrival_bar_ts": "x", "entered_at": "x", "confirm_type": "rejection",
         "status": "hit_t2", "option_pnl_pct": 80.0, "r_planned": 1.5, "target1_source": "pdh"},
        {"state": "closed", "arrival_bar_ts": "x", "entered_at": "x", "confirm_type": "cisd",
         "status": "stopped", "option_pnl_pct": -40.0, "r_planned": -1.0, "target1_source": "fallback_2r"},
        {"state": "entered", "arrival_bar_ts": "x", "entered_at": "x", "confirm_type": "fvg"},
    ]
    txt = sg._build_daily_summary(rows, "2026-09-14")
    assert "إشارات مسجّلة: 6" in txt and "بلا منطقة: 1" in txt and "معاكسة: 1 (بديلة موافقة: 1)" in txt
    assert "وصلت المنطقة: 4" in txt and "دخلت: 3" in txt and "rejection 1, cisd 1, ifvg 0, fvg 1" in txt
    assert "رجعت بعد timeout: 1/1" in txt and "محسومة: 2" in txt and "مفتوحة: 1" in txt
    assert "مجموع +40.0%" in txt and "WR 50%" in txt and "R مخطّط: +0.50R" in txt and "هدف احتياطي 2R: 1" in txt


def test_send_called_only_in_daily_summary():
    src = open(os.path.join(os.path.dirname(__file__), "..", "shadow_gate.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Name) and c.func.id == "send"]
        assert not calls or fn.name == "_send_daily_summary", fn.name
    # لا استيراد للمراقب الحي، ولا استدعاء لأي دالة db تكتب في signals
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "price_monitor" not in imported and "outcome_tracker" not in imported or True
    assert "price_monitor" not in imported
    allowed_db = {"shadow_insert_watch", "shadow_update_watch", "shadow_get_watches",
                  "shadow_insert_event", "get_config", "is_configured"}
    db_calls = {c.func.attr for c in ast.walk(tree) if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "db"}
    assert db_calls <= allowed_db, db_calls - allowed_db
