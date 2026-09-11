"""اختبارات بوابة الدخول (entry_gate) — منطق نقي على شموع 5m مصنوعة."""
import os
import pandas as pd
import pytest

import entry_gate as eg
from entry_gate import GateZone
from htf_zones import inversion_fvg_confirms_zone

TZ = "America/New_York"


def bars(rows, start="2026-09-10 10:00", minutes=5):
    idx = pd.date_range(start, periods=len(rows), freq=f"{minutes}min", tz=TZ)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def flat(n, o=100.0, h=100.3, l=99.8, c=100.1):
    """شموع هادئة: ذيل سفلي 0.2 وعلوي 0.2."""
    return [[o, h, l, c, 1000]] * n


def armed(direction, zone):
    return {"state": "armed", "direction": direction, "zone": zone, "arrival_pos": None, "bars_seen": 0}


def run(df, state, positions):
    """يمشي على المواضع بالترتيب ويُرجع (الحالة الأخيرة, قائمة القرارات)."""
    decs = []
    for pos in positions:
        atr = eg.atr_5m(df.iloc[:pos + 1])
        state, dec = eg.step_bar(state, df, pos, atr)
        decs.append(dec)
        if state["state"] in ("entered", "cancelled"):
            break
    return state, decs


# ── وقت / جلسة / شموع مغلقة ────────────────────────────────────────────────

def test_closed_bars_excludes_forming_bar():
    df = bars(flat(3))                                   # 10:00, 10:05, 10:10
    now = pd.Timestamp("2026-09-10 10:14:30", tz=TZ)
    assert len(eg.closed_bars(df, now)) == 2             # شمعة 10:10 تُغلق 10:15
    now = pd.Timestamp("2026-09-10 10:15:10", tz=TZ)
    assert len(eg.closed_bars(df, now, grace_sec=20)) == 2
    assert len(eg.closed_bars(df, now + pd.Timedelta(seconds=20), grace_sec=20)) == 3


def test_session_and_cutoff_rules():
    t = lambda s: pd.Timestamp(f"2026-09-10 {s}", tz=TZ)
    assert not eg.is_session_bar(t("09:25"))
    assert eg.is_session_bar(t("09:30"))
    assert eg.is_session_bar(t("15:55"))
    assert not eg.is_session_bar(t("16:00"))
    assert not eg.after_cutoff(t("14:55"))               # تُغلق 15:00 → مسموحة
    assert eg.after_cutoff(t("15:00"))                   # تُغلق 15:05 → لا


def test_candidate_positions_skip_premarket_and_evaluated():
    df = bars(flat(12), start="2026-09-10 09:15")         # 09:15 … 10:10
    since = eg.floor_5m(pd.Timestamp("2026-09-10 09:32", tz=TZ))   # التسجيل 09:32 → شمعة 09:30
    pos = eg.candidate_positions(df, since_ts=since)
    assert df.index[pos[0]].strftime("%H:%M") == "09:30"
    pos2 = eg.candidate_positions(df, since_ts=since, last_bar_ts=df.index[5])   # 09:40 قُيّمت
    assert df.index[pos2[0]].strftime("%H:%M") == "09:45"
    assert all(eg.is_session_bar(df.index[p]) for p in pos)


# ── rejection ──────────────────────────────────────────────────────────────

def test_rejection_call_on_arrival_bar_enters():
    zone = GateZone(low=99.2, high=99.5, direction="demand", timeframe="daily", zone_type="ob")
    rows = flat(10) + [[100.0, 100.1, 98.8, 99.9, 1000]]  # ذيل 1.1 > جسم 0.1 > أطول ذيل سابق 0.2
    df = bars(rows)
    st, decs = run(df, armed("call", zone), [10])
    d = decs[-1]
    assert d.touched and d.bars_seen == 1
    assert st["state"] == "entered" and d.action == "enter" and d.reason == "rejection"
    assert d.checks["rejection"]["ok"] and d.checks["rejection"]["close_loc"] == "beyond"
    atr = eg.atr_5m(df.iloc[:11])
    assert d.details["stop_basis"] == "candle"
    assert d.details["stop_price"] == round(98.8 - 0.15 * atr, 2)
    assert d.details["entry_price"] == 99.9


def test_rejection_negative_cases():
    zone = GateZone(low=99.2, high=99.5, direction="demand")
    # أ) ذيل سابق أطول (1.5) → ليس الأطول → لا دخول
    rows = flat(9) + [[100.0, 100.3, 98.5, 100.1, 1000]] + [[100.0, 100.1, 98.8, 99.9, 1000]]
    st, decs = run(bars(rows), armed("call", zone), [10])
    assert st["state"] == "touched" and decs[-1].action == "none"
    assert not decs[-1].checks["rejection"]["ok"]
    # ب) الجسم أكبر من الذيل → لا دخول
    rows = flat(10) + [[100.5, 100.6, 98.8, 99.3, 1000]]
    st, decs = run(bars(rows), armed("call", zone), [10])
    assert decs[-1].action == "none" and not decs[-1].checks["rejection"]["ok"]
    # ج) إغلاق تحت المنطقة → اختراق (break) لا rejection
    rows = flat(10) + [[100.0, 100.1, 98.8, 99.0, 1000]]
    st, decs = run(bars(rows), armed("call", zone), [10])
    assert st["state"] == "cancelled" and decs[-1].reason == "break"
    # د) إغلاق داخل المنطقة → close_loc = inside
    rows = flat(10) + [[99.9, 100.0, 98.8, 99.4, 1000]]
    st, decs = run(bars(rows), armed("call", zone), [10])
    assert decs[-1].reason == "rejection" and decs[-1].checks["rejection"]["close_loc"] == "inside"


def test_rejection_put_mirror():
    zone = GateZone(low=100.5, high=100.8, direction="supply")
    rows = flat(10, o=100.0, h=100.2, l=99.7, c=99.9) + [[100.0, 101.2, 99.9, 100.1, 1000]]
    df = bars(rows)
    st, decs = run(df, armed("put", zone), [10])
    d = decs[-1]
    assert st["state"] == "entered" and d.reason == "rejection"
    assert d.checks["rejection"]["close_loc"] == "beyond"
    atr = eg.atr_5m(df.iloc[:11])
    assert d.details["stop_price"] == round(101.2 + 0.15 * atr, 2)


# ── break / timeout / eod ──────────────────────────────────────────────────

def test_timeout_after_six_bars_without_confirmation():
    zone = GateZone(low=99.2, high=99.5, direction="demand")
    inside = [[99.4, 99.6, 99.3, 99.35, 1000]] * 7      # تتداخل مع المنطقة، بلا تأكيد
    df = bars(flat(10) + inside)
    st, decs = run(df, armed("call", zone), range(10, 17))
    assert [d.bars_seen for d in decs] == [1, 2, 3, 4, 5, 6]
    assert all(d.action == "none" for d in decs[:-1])
    assert st["state"] == "cancelled" and decs[-1].reason == "timeout"


def test_break_cancels_on_close_beyond_zone():
    zone = GateZone(low=99.2, high=99.5, direction="demand")
    df = bars(flat(10) + [[99.4, 99.6, 99.3, 99.4, 1000], [99.4, 99.45, 98.9, 99.0, 1000]])
    st, decs = run(df, armed("call", zone), [10, 11])
    assert decs[0].touched and decs[0].action == "none"
    assert st["state"] == "cancelled" and decs[1].reason == "break" and decs[1].bars_seen == 2


def test_eod_rules_for_armed_and_touched():
    zone = GateZone(low=99.2, high=99.5, direction="demand")
    rej = [100.0, 100.1, 98.8, 99.9, 1000]
    # armed: شمعة 15:00 (تُغلق 15:05) → eod حتى لو تداخلت
    df = bars(flat(10) + [rej], start="2026-09-10 14:10")          # آخر شمعة 15:00
    st, decs = run(df, armed("call", zone), [10])
    assert st["state"] == "cancelled" and decs[-1].reason == "eod"
    # touched: التأكيد على شمعة 14:55 (تُغلق 15:00) مسموح
    df = bars(flat(10) + [rej], start="2026-09-10 14:05")          # آخر شمعة 14:55
    st, decs = run(df, armed("call", zone), [10])
    assert st["state"] == "entered"
    # touched من شمعة سابقة ثم شمعة 15:00 مؤكِّدة → eod لا دخول
    df = bars(flat(10) + [[99.4, 99.6, 99.3, 99.35, 1000], rej], start="2026-09-10 14:05")  # 14:55 ثم 15:00
    st, decs = run(df, armed("call", zone), [10, 11])
    assert decs[0].touched and st["state"] == "cancelled" and decs[1].reason == "eod"


# ── cisd / ifvg / fvg ──────────────────────────────────────────────────────

def test_cisd_confirmation_after_sweep_and_reclaim():
    zone = GateZone(low=98.5, high=99.2, direction="demand")
    rows = flat(20, o=100.0, h=101.0, l=99.0, c=100.0)
    rows += [[100.0, 100.2, 98.9, 99.6, 1000],        # الوصول (شمعة ١)
             [99.3, 99.4, 98.4, 99.0, 1000],          # مسح تحت القاع المرجعي 99
             [99.0, 101.3, 98.95, 101.2, 1000]]       # إغلاق فوق القمة المرجعية 101
    df = bars(rows)
    st, decs = run(df, armed("call", zone), [20, 21, 22])
    assert decs[0].touched and decs[0].action == "none"
    assert decs[1].action == "none" and not decs[1].checks["rejection"]["ok"]
    assert st["state"] == "entered" and decs[2].reason == "cisd" and decs[2].bars_seen == 3
    assert decs[2].details["stop_basis"] == "zone"
    atr = eg.atr_5m(df.iloc[:23])
    assert decs[2].details["stop_price"] == round(98.5 - 0.15 * atr, 2)
    assert decs[2].details["close_loc"] == "beyond"


def test_ifvg_since_idx_requires_post_arrival_inversion_close():
    rows = [[100.0, 101.0, 99.5, 100.0, 1000],
            [100.0, 100.0, 97.0, 97.5, 1000],
            [97.5, 98.0, 97.0, 97.6, 1000],         # bearish FVG: (98.0, 99.5)
            [97.6, 98.6, 97.5, 98.5, 1000],
            [98.5, 99.9, 98.4, 99.8, 1000],         # إغلاق فوق الفجوة (انقلاب) عند j=4
            [99.8, 99.9, 99.2, 99.3, 1000],
            [99.3, 99.5, 99.1, 99.2, 1000]]
    df = bars(rows)
    zone = GateZone(low=98.2, high=99.0, direction="demand")
    assert inversion_fvg_confirms_zone(df, zone, "call") is True                 # السلوك الأصلي
    assert inversion_fvg_confirms_zone(df, zone, "call", since_idx=4) is True
    assert inversion_fvg_confirms_zone(df, zone, "call", since_idx=5) is False
    assert eg.ifvg_after(df, pos=6, arrival_pos=4, zone=zone, direction="call") is True
    assert eg.ifvg_after(df, pos=6, arrival_pos=5, zone=zone, direction="call") is False


def test_fvg_formed_after_arrival_confirms():
    # السياق بقمم 100.6 كي لا يتحقق cisd (الإغلاق لا يتجاوز القمة المرجعية)،
    # وقمة شمعة الوصول 99.9 كي لا تتكوّن فجوة هابطة تنقلب (ifvg) — نعزل مسار fvg.
    zone = GateZone(low=99.0, high=100.0, direction="demand", timeframe="daily", zone_type="fvg")
    rows = flat(10, o=100.0, h=100.6, l=99.8, c=100.1)
    rows += [[99.5, 99.9, 99.2, 99.4, 1000],          # الوصول (شمعة ١)
             [99.4, 100.1, 99.35, 100.05, 1000],      # شمعة الإزاحة
             [100.05, 100.4, 100.0, 100.3, 1000]]     # low 100.0 > high[الوصول] 99.9 → FVG (99.9, 100.0) داخل المنطقة
    df = bars(rows)
    st, decs = run(df, armed("call", zone), [10, 11, 12])
    assert decs[0].touched and decs[0].action == "none" and decs[1].action == "none"
    assert st["state"] == "entered" and decs[2].reason == "fvg" and decs[2].bars_seen == 3
    assert decs[2].checks["fvg"] is True and decs[2].checks["cisd"] is False
    assert decs[2].checks["ifvg"] is False and decs[2].checks["rejection"]["ok"] is False


def test_first_confirmation_order():
    assert eg.first_confirmation({"rejection": {"ok": True}, "cisd": True}) == "rejection"
    assert eg.first_confirmation({"rejection": {"ok": False}, "cisd": False, "ifvg": True, "fvg": True}) == "ifvg"
    assert eg.first_confirmation({"rejection": {"ok": False}, "displacement": True}) is None


# ── الستوب والأهداف ────────────────────────────────────────────────────────

def test_compute_stop_variants():
    bar = pd.Series({"Open": 100.0, "High": 100.5, "Low": 98.8, "Close": 99.9})
    narrow = GateZone(low=99.2, high=99.5, direction="demand")
    wide   = GateZone(low=98.0, high=99.6, direction="demand")
    atr = 0.5
    assert eg.compute_stop("call", "rejection", bar, narrow, atr) == (round(98.8 - 0.075, 2), "candle")
    assert eg.compute_stop("call", "cisd", bar, narrow, atr) == (round(99.2 - 0.075, 2), "zone")
    assert eg.compute_stop("call", "cisd", bar, wide, atr) == (round(98.8 - 0.075, 2), "wide_zone_candle")
    sup = GateZone(low=100.6, high=100.9, direction="supply")
    assert eg.compute_stop("put", "fvg", bar, sup, atr) == (round(100.9 + 0.075, 2), "zone")
    assert eg.compute_stop("put", "rejection", bar, sup, atr) == (round(100.5 + 0.075, 2), "candle")


def test_structural_targets_ladder_and_fallback():
    lv = [(100.5, "swing_5m"), (101.2, "pdh"), (101.5, "swing_5m"), (103.0, "pwh")]
    t = eg.structural_targets("call", entry=100.0, stop=99.0, atr=0.4, levels=lv)
    assert (t["target1"], t["target1_source"]) == (101.2, "pdh")        # أول مستوى يبعد ≥ 1R
    assert (t["target2"], t["target2_source"]) == (101.5, "swing_5m")   # التالي بعد T1 بمسافة ≥ 0.5×ATR
    assert t["rr"] == 1.2 and t["risk_atr"] == 2.5
    # لا مستوى ضمن 4R → fallback 2R، و T2 من المستوى البعيد (ضمن 6R)
    t = eg.structural_targets("call", 100.0, 99.0, 0.4, [(106.0, "pmh")])
    assert (t["target1"], t["target1_source"]) == (102.0, "fallback_2r")
    assert (t["target2"], t["target2_source"]) == (106.0, "pmh")
    # لا مستويات إطلاقاً → امتداد
    t = eg.structural_targets("call", 100.0, 99.0, 0.4, [])
    assert (t["target1"], t["target2"], t["target2_source"]) == (102.0, 104.0, "extension")
    # بوت: المستويات تحت السعر، الأقرب أولاً
    t = eg.structural_targets("put", 100.0, 101.0, 0.4, [(98.8, "pdl"), (97.0, "pml")])
    assert (t["target1"], t["target2"]) == (98.8, 97.0)
    assert eg.structural_targets("call", 100.0, 100.5, 0.4, lv) is None   # ستوب فوق الدخول


def test_collect_levels_in_direction_sorted_nearest_first():
    rows = flat(60, o=100.0, h=100.3, l=99.7, c=100.1)
    rows[30] = [100.0, 102.0, 99.7, 100.1, 1000]     # pivot high 102
    rows[45] = [100.0, 100.3, 97.0, 100.1, 1000]     # pivot low 97 (يُستبعد للكول)
    df5 = bars(rows)
    d_idx = pd.date_range("2026-08-01", periods=30, freq="B")
    df1d = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1}, index=d_idx)
    df1d.iloc[-2, df1d.columns.get_loc("High")] = 103.0
    zones = [GateZone(low=104.0, high=104.6, direction="supply", timeframe="daily", zone_type="ob"),
             GateZone(low=95.0, high=96.0, direction="demand", timeframe="4h", zone_type="fvg")]
    lv = eg.collect_levels("call", 100.1, df5, None, df1d, zones)
    prices = [p for p, _ in lv]
    assert prices == sorted(prices) and all(p > 100.1 for p in prices)
    assert (102.0, "swing_5m") in lv and (103.0, "pdh") in lv and (104.0, "htf_daily_ob") in lv
    assert not any(p in (97.0, 95.0, 96.0) for p in prices)
    lv_put = eg.collect_levels("put", 100.1, df5, None, df1d, zones)
    p2 = [p for p, _ in lv_put]
    assert p2 == sorted(p2, reverse=True) and (97.0, "swing_5m") in lv_put and (96.0, "htf_4h_fvg") in lv_put


# ── إدارة الصفقة بعد الدخول (مرآة price_monitor) ───────────────────────────

def test_evaluate_exit_call_sequence():
    kw = dict(direction="call", entry=100.0, stop=99.0, t1=101.5, t2=103.0, rr=1.5)
    assert eg.evaluate_exit(price=100.5, **kw)["action"] == "none"
    r = eg.evaluate_exit(price=101.6, **kw)
    assert r["action"] == "t1" and r["t1_hit"] and r["peak"] == 101.6
    r = eg.evaluate_exit(price=102.0, t1_hit=True, peak=101.6, **kw)
    assert r["action"] == "none" and r["peak"] == 102.0
    r = eg.evaluate_exit(price=101.2, t1_hit=True, peak=102.0, **kw)      # 102 − 0.75 = 101.25
    assert (r["action"], r["status"], r["exit_reason"], r["outcome_price"], r["r_planned"]) == \
           ("close", "hit_t1", "trailing_stop", 101.25, 0.75)
    r = eg.evaluate_exit(price=98.9, t1_hit=True, peak=102.0, **kw)
    assert (r["status"], r["exit_reason"], r["outcome_price"]) == ("hit_t1", "stop_after_t1", 101.5)
    r = eg.evaluate_exit(price=98.9, **kw)
    assert (r["status"], r["exit_reason"], r["outcome_price"], r["r_planned"]) == ("stopped", "stop", 99.0, -1.0)
    r = eg.evaluate_exit(price=103.2, **kw)
    assert (r["status"], r["outcome_price"], r["r_planned"]) == ("hit_t2", 103.0, 1.5)


def test_evaluate_exit_put_trailing():
    kw = dict(direction="put", entry=100.0, stop=101.0, t1=98.5, t2=97.0, rr=1.5)
    r = eg.evaluate_exit(price=98.4, **kw)
    assert r["action"] == "t1" and r["peak"] == 98.4
    r = eg.evaluate_exit(price=98.0, t1_hit=True, peak=98.4, **kw)
    assert r["action"] == "none" and r["peak"] == 98.0
    r = eg.evaluate_exit(price=98.8, t1_hit=True, peak=98.0, **kw)       # 98 + 0.75 = 98.75
    assert r["action"] == "close" and r["exit_reason"] == "trailing_stop" and r["outcome_price"] == 98.75


def test_evaluate_exit_bars_stop_first_in_ambiguous_bar():
    kw = dict(direction="call", entry=100.0, stop=99.0, t1=101.5, t2=103.0, rr=1.5)
    amb = bars([[100.0, 103.5, 98.5, 101.0, 1000]])                        # لامست الستوب و T2 معاً
    r = eg.evaluate_exit_bars(bars=amb, **kw)
    assert r["status"] == "stopped" and r["gap"] is True
    seq = bars([[100.0, 101.6, 99.9, 101.4, 1000],                          # T1
                [101.4, 102.4, 101.3, 102.0, 1000],                         # قمة 102.4
                [102.0, 102.1, 101.5, 101.6, 1000]])                        # 102.4 − 0.75 = 101.65 ≥ 101.5 → trailing
    r = eg.evaluate_exit_bars(bars=seq, **kw)
    assert (r["status"], r["exit_reason"], r["outcome_price"], r["gap"]) == ("hit_t1", "trailing_stop", 101.65, True)
    r = eg.evaluate_exit_bars(bars=bars([[100.0, 100.4, 99.8, 100.2, 1000]]), **kw)
    assert r["action"] == "none" and r["gap"] is False


def test_excursions_and_r_actual():
    seq = bars([[100.0, 101.5, 99.6, 101.0, 1000], [101.0, 101.2, 100.0, 100.5, 1000]])
    ex = eg.excursions("call", seq, entry=100.0, stop=99.0)
    assert (ex["max_favorable"], ex["max_adverse"]) == (1.5, -0.4)
    ex = eg.excursions("call", seq, entry=100.0, stop=99.0, extra_price=99.3)
    assert ex["max_adverse"] == -0.7 and ex["lowest_price"] == 99.3
    assert eg.r_actual("call", 100.0, 99.0, 101.25) == 1.25
    assert eg.r_actual("put", 100.0, 101.0, 98.75) == 1.25
    assert eg.r_actual("call", 100.0, 99.0, 99.0) == -1.0


# ── لا قنوات جانبية ────────────────────────────────────────────────────────

def test_entry_gate_is_pure():
    src = open(os.path.join(os.path.dirname(__file__), "..", "entry_gate.py"), encoding="utf-8").read()
    for banned in ("import db", "telegram", "requests", "data_client", "import config"):
        assert banned not in src, banned
