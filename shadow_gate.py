"""
Shadow Gate — وضع الظل لبوابة الدخول بالتأكيد عند المنطقة (المرحلة ٢).

قارئ بحت: يرى، يقرّر، يسجّل في Supabase (shadow_watches / shadow_events).
لا يلمس signals ولا price_monitor ولا يرسل إشارات فردية على Telegram؛ الرسالة
الوحيدة ملخّص يومي عند 15:52 ET (_send_daily_summary). المنطق النقي في entry_gate.

الدورة (خيط منفصل عن المراقب الحي، كل 45 ثانية):
  • عند كل حدّ 5 دقائق + 20 ثانية: جلب مجمّع لشموع 5m لرموز المراقبات غير الطرفية،
    تقييم الشموع المغلقة الجديدة بالترتيب (entry_gate.step_bar)، كتابة مشروطة بالحالة
    المتوقعة (انتقال ذرّي — لا ازدواج لو تداخلت حاويتان لحظة النشر).
  • كل دورة: صفقات الظل النشطة (entered) بسعر آخر صفقة بنفس قواعد price_monitor،
    واقتباس العقد عند الخروج. فجوة > 3 دقائق بين دورتين → حكم من الشموع الفائتة
    أولاً (الستوب أولاً، gap=true).
  • armed/touched: تُلغى eod بأول شمعة تُغلق بعد 15:00 ET (بلا حمل لليوم التالي).
    entered: تنتهي expired عند موت العقد (نفس تعريف outcome_tracker).
  • cancelled(timeout) اليوم: نراقب خروج السعر من المنطقة ثم رجوعه (قياس فقط،
    الزيارة الثانية مؤجَّلة).

التسجيل: main.scan() يستدعي register(signal, signal_id) بعد حفظ الإشارة الحية.
إعادة التشغيل: الحقيقة كلها في الصف (state, last_bar_ts, bars_seen, arrival_bar_ts…)؛
الذاكرة لا تحمل حقيقة، وكل دورة تعيد قراءة الصفوف. مفتاح إيقاف: bot_config.shadow_gate.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pytz

import config
import db
import data_client as dc
import entry_gate as eg
from entry_gate import GateZone
from telegram_bot import send

ET                = pytz.timezone("America/New_York")
POLL_SECONDS      = 45
BAR_GRACE_SEC     = 20        # مهلة بعد إغلاق الشمعة قبل جلبها (توفّر IEX خلال ثوانٍ)
GAP_SECONDS       = 180       # فجوة بين دورتين تستدعي الحكم من الشموع الفائتة
MAX_ENTRY_LAG_SEC = 900       # تأكيد أقدم من 15 دقيقة (catch-up بعد توقف طويل) → لا دخول: stale
CONFIG_TTL        = 300
BAR_STATES        = ("armed", "touched")
ACTIVE_STATES     = ("armed", "touched", "entered")

_last_boundary: Optional[pd.Timestamp] = None
_cfg_cache: dict = {"ts": 0.0, "on": True}
_summary_sent_date: str = ""


# ─── أدوات ────────────────────────────────────────────────────────────────────

def _now_et() -> pd.Timestamp:
    return pd.Timestamp.now(tz=ET)


def _iso(ts) -> Optional[str]:
    """ISO UTC بـ Z (صيغة الصفوف الحية)."""
    if ts is None:
        return None
    return eg.to_et(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s) -> Optional[pd.Timestamp]:
    if s is None or s == "":
        return None
    try:
        t = pd.Timestamp(s)
        return t.tz_localize("UTC") if t.tzinfo is None else t
    except Exception:
        return None


def _jsonable(o):
    """يحوّل numpy/pandas إلى أنواع JSON أصلية (للأعمدة jsonb)."""
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, float):
        return None if not np.isfinite(o) else o
    if isinstance(o, pd.Timestamp):
        return _iso(o)
    return o


def _enabled() -> bool:
    """bot_config.shadow_gate ('1' افتراضياً) — cache 5 دقائق."""
    now = time.time()
    if now - float(_cfg_cache["ts"]) < CONFIG_TTL:
        return bool(_cfg_cache["on"])
    try:
        _cfg_cache["on"] = (db.get_config("shadow_gate", "1") or "1").strip() != "0"
    except Exception:
        _cfg_cache["on"] = True
    _cfg_cache["ts"] = now
    return bool(_cfg_cache["on"])


def _watch_zone(w: dict) -> GateZone:
    return GateZone(low=float(w["zone_low"]), high=float(w["zone_high"]),
                    direction=w.get("zone_direction") or "",
                    timeframe=w.get("zone_tf") or "",
                    zone_type=(w.get("zone_type") or "").lower())


def _today_start_utc_iso(now_et: pd.Timestamp) -> str:
    return _iso(now_et.replace(hour=0, minute=0, second=0, microsecond=0))


def _event(watch_id: int, kind: str, bar_ts=None, before=None, after=None, checks=None) -> None:
    try:
        db.shadow_insert_event(watch_id, kind, bar_ts=_iso(bar_ts) if bar_ts is not None else None,
                               state_before=before, state_after=after,
                               checks=_jsonable(checks) if checks is not None else None)
    except Exception as e:
        print(f"  [shadow] event {kind} #{watch_id}: {e}")


# ─── Public API ───────────────────────────────────────────────────────────────

def start() -> None:
    """يُشغَّل مرة واحدة عند بدء البوت (بعد price_monitor.start)."""
    if not db.is_configured():
        print("  [shadow] Supabase غير مضبوط — وضع الظل معطّل")
        return
    threading.Thread(target=_loop, daemon=True).start()
    threading.Thread(target=_summary_loop, daemon=True).start()
    print("  [shadow] وضع الظل بدأ (تقييم شموع 5m مغلقة + صفقات ظل كل 45 ثانية)")


def register(signal, signal_id: Optional[int]) -> Optional[int]:
    """
    يسجّل مراقبة ظل لإشارة حية حُفظت للتو. التصنيف:
      no_zone       لا منطقة HTF مع الإشارة
      zone_conflict المنطقة النشطة تعاكس الاتجاه النهائي (يُسجَّل هل وُجدت بديلة موافقة)
      armed         منطقة موافقة → ننتظر أول شمعة 5m مغلقة تتداخل معها
    """
    if not db.is_configured() or not _enabled():
        return None
    try:
        direction = signal.direction
        exp_dir   = "demand" if direction == "call" else "supply"
        zone_tf   = signal.htf_zone_tf or ""
        zone_dir  = signal.htf_direction or ""
        z_low, z_high = float(signal.htf_zone_low or 0), float(signal.htf_zone_high or 0)
        atr = float(signal.atr or 0)
        if not zone_tf or z_high <= 0 or z_high <= z_low:
            state = "no_zone"
        elif zone_dir != exp_dir:
            state = "zone_conflict"
        else:
            state = "armed"
        has_zone = state != "no_zone"
        alt_found = bool(float(signal.alt_zone_high or 0) > 0)
        payload = {
            "signal_id":      signal_id,
            "symbol":         signal.symbol,
            "direction":      direction,
            "visit_no":       1,
            "state":          state,
            "zone_low":       z_low if has_zone else None,
            "zone_high":      z_high if has_zone else None,
            "zone_tf":        zone_tf or None,
            "zone_type":      (signal.htf_zone_type or None),
            "zone_direction": zone_dir or None,
            "zone_width_atr": round((z_high - z_low) / atr, 3) if has_zone and atr > 0 else None,
            "alt_zone_found": alt_found,
            "alt_zone_low":   float(signal.alt_zone_low) if alt_found else None,
            "alt_zone_high":  float(signal.alt_zone_high) if alt_found else None,
            "alt_zone_tf":    (signal.alt_zone_tf or None) if alt_found else None,
            "alt_zone_type":  (signal.alt_zone_type or None) if alt_found else None,
            "atr":            atr or None,
            "score":          round(float(signal.score), 2),
            "is_scalp":       bool(signal.is_scalp),
            "signal_price":   float(signal.current_price),
            "entry_type":     signal.entry_type,
            "registered_at":  _iso(_now_et()),
            "bars_seen":      0,
        }
        wid = db.shadow_insert_watch(payload)
        if wid:
            _event(wid, "register", after=state,
                   checks={"zone_tf": zone_tf, "zone_dir": zone_dir, "alt_zone_found": alt_found,
                           "zone_width_atr": payload["zone_width_atr"]})
            print(f"  [shadow] #{wid} {signal.symbol} {direction} → {state}"
                  + (f" | zone {z_low:.2f}–{z_high:.2f} ({zone_tf} {signal.htf_zone_type})" if has_zone else "")
                  + (" | بديلة موافقة موجودة" if state == "zone_conflict" and alt_found else ""))
        return wid
    except Exception as e:
        print(f"  [shadow] register {getattr(signal, 'symbol', '?')}: {e}")
        return None


# ─── الحلقة ───────────────────────────────────────────────────────────────────

def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as e:
            print(f"  [shadow] tick error: {e}")
        time.sleep(POLL_SECONDS)


def _tick(now: Optional[pd.Timestamp] = None) -> None:
    global _last_boundary
    if not _enabled():
        return
    now = now if now is not None else _now_et()

    watches = db.shadow_get_watches(list(ACTIVE_STATES))
    bar_watches = [w for w in watches if w.get("state") in BAR_STATES]
    entered     = [w for w in watches if w.get("state") == "entered"]

    # مراقبات timeout اليوم التي لم ترجع بعد (قياس الرجوع للمنطقة)
    observe: List[dict] = []
    try:
        observe = [w for w in db.shadow_get_watches(["cancelled"], since_iso=_today_start_utc_iso(now))
                   if w.get("cancel_reason") == "timeout" and not w.get("returned_at")]
    except Exception as e:
        print(f"  [shadow] observe fetch: {e}")

    # ── شموع 5m مغلقة: عند كل حدّ 5 دقائق + مهلة ─────────────────────────────
    boundary = eg.floor_5m(now)
    due = ((now - boundary).total_seconds() >= BAR_GRACE_SEC
           and (_last_boundary is None or boundary > _last_boundary))
    if due and (bar_watches or observe):
        symbols = sorted({w["symbol"] for w in bar_watches + observe})
        frames  = _fetch_5m(symbols)
        for w in bar_watches:
            df = frames.get(w["symbol"])
            if df is None or len(df) == 0:
                continue
            try:
                _evaluate_watch_bars(w, eg.closed_bars(df, now, grace_sec=BAR_GRACE_SEC), now)
            except Exception as e:
                print(f"  [shadow] bars #{w.get('id')} {w.get('symbol')}: {e}")
        for w in observe:
            df = frames.get(w["symbol"])
            if df is None or len(df) == 0:
                continue
            try:
                _observe_return(w, eg.closed_bars(df, now, grace_sec=BAR_GRACE_SEC), now)
            except Exception as e:
                print(f"  [shadow] observe #{w.get('id')} {w.get('symbol')}: {e}")
        _last_boundary = boundary
    elif due:
        _last_boundary = boundary

    # ── صفقات الظل النشطة: كل دورة بسعر آخر صفقة ───────────────────────────
    if entered:
        prices = dc.get_latest_trades(sorted({w["symbol"] for w in entered}))
        for w in entered:
            px = prices.get(w["symbol"])
            if not px:
                continue
            try:
                _check_entered(w, float(px), now)
            except Exception as e:
                print(f"  [shadow] entered #{w.get('id')} {w.get('symbol')}: {e}")


def _fetch_5m(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    try:
        frames = dc.get_bars_batch(symbols, "5m", "3d") or {}
    except Exception as e:
        print(f"  [shadow] batch 5m: {e}")
    for s in symbols:
        df = frames.get(s)
        if df is None or len(df) == 0:
            try:
                frames[s] = dc.get_bars(s, "5m", "3d")
            except Exception as e:
                print(f"  [shadow] 5m {s}: {e}")
    out = {}
    for s, df in frames.items():
        if df is not None and len(df):
            out[s] = df.sort_index()
    return out


# ─── armed / touched: تقييم الشموع المغلقة ────────────────────────────────────

def _evaluate_watch_bars(w: dict, df: pd.DataFrame, now: pd.Timestamp) -> None:
    zone = _watch_zone(w)
    since = eg.floor_5m(_parse(w["registered_at"]))
    positions = eg.candidate_positions(df, since_ts=since, last_bar_ts=_parse(w.get("last_bar_ts")))
    if not positions:
        return
    state = {"state": w["state"], "direction": w["direction"], "zone": zone,
             "bars_seen": int(w.get("bars_seen") or 0), "arrival_pos": None}
    arr = _parse(w.get("arrival_bar_ts"))
    if arr is not None:
        key = arr.tz_convert(df.index.tz) if df.index.tz is not None else arr.tz_convert(ET).tz_localize(None)
        state["arrival_pos"] = int(df.index.searchsorted(key))
    for pos in positions:
        atr = eg.atr_5m(df.iloc[:pos + 1])
        before = state["state"]
        state, dec = eg.step_bar(state, df, pos, atr)
        if not _apply_bar_decision(w, before, state, dec, df, pos, atr, now):
            return
        if state["state"] not in BAR_STATES:
            return


def _apply_bar_decision(w: dict, before: str, state: dict, dec: eg.BarDecision,
                        df: pd.DataFrame, pos: int, atr: float, now: pd.Timestamp) -> bool:
    """يكتب نتيجة شمعة واحدة (مشروطاً بالحالة السابقة). يُرجع False لو تحرّكت الحالة من خارجنا."""
    wid = w["id"]
    fields: dict = {"last_bar_ts": _iso(dec.bar_ts), "state": state["state"]}
    if dec.touched:
        fields["arrival_bar_ts"] = _iso(dec.bar_ts)
    if state["state"] in ("touched", "entered", "cancelled") and dec.bars_seen:
        fields["bars_seen"] = int(dec.bars_seen)

    lag = int((now - eg.bar_close_ts(dec.bar_ts)).total_seconds())
    if dec.action == "enter" and lag > MAX_ENTRY_LAG_SEC:
        # تأكيد قديم (catch-up بعد توقف طويل): سعر العقد الآن لا يمثّل لحظة الإغلاق → لا دخول
        state["state"] = "cancelled"
        fields["state"] = "cancelled"
        dec.action, dec.reason = "cancel", "stale"

    plan: dict = {}
    if dec.action == "enter":
        plan = _build_entry_plan(w, dec, df, pos, atr, now, lag)
        if not plan:
            state["state"] = "cancelled"
            fields["state"] = "cancelled"
            dec.action, dec.reason = "cancel", "bad_plan"
        else:
            fields.update(plan)

    if dec.action == "cancel":
        fields.update(cancel_reason=dec.reason, cancelled_at=_iso(now))

    ok = db.shadow_update_watch(wid, fields, expect_state=before)
    if not ok:
        print(f"  [shadow] #{wid} {w['symbol']}: الحالة تحرّكت من خارج هذه الدورة ({before}) — تُركت")
        return False
    w.update(fields)

    if dec.touched:
        _event(wid, "arrival", bar_ts=dec.bar_ts, before=before, after="touched",
               checks={"bar": _bar_dict(df.iloc[pos])})
    checks = dict(dec.checks or {})
    checks["decision"] = dec.action if dec.action != "none" else "wait"
    if dec.reason:
        checks["reason"] = dec.reason
    checks["bars_seen"] = dec.bars_seen
    checks["atr"] = round(float(atr), 4)
    _event(wid, "bar", bar_ts=dec.bar_ts, before=before, after=state["state"], checks=checks)
    if dec.action == "enter":
        _event(wid, "enter", bar_ts=dec.bar_ts, before=before, after="entered",
               checks={**dec.details, **plan})
        print(f"  [shadow] 🟢 #{wid} {w['symbol']} {w['direction']} دخول ظل ({dec.reason}) @ {plan.get('entry_price')} "
              f"stop {plan.get('stop_price')} T1 {plan.get('target1')} ({plan.get('target1_source')}) "
              f"T2 {plan.get('target2')} | عقد {plan.get('strike')} {plan.get('expiry')} @ {plan.get('entry_option_price')}")
    elif dec.action == "cancel":
        _event(wid, "cancel", bar_ts=dec.bar_ts, before=before, after="cancelled",
               checks={"reason": dec.reason, "bars_seen": dec.bars_seen})
        print(f"  [shadow] ✖ #{wid} {w['symbol']} إلغاء ظل: {dec.reason} (شموع {dec.bars_seen})")
    elif dec.touched:
        print(f"  [shadow] 📍 #{wid} {w['symbol']} وصل المنطقة (شمعة {dec.bar_ts.strftime('%H:%M')} ET)")
    return True


def _bar_dict(bar) -> dict:
    return {k: round(float(bar[k]), 4) for k in ("Open", "High", "Low", "Close") if k in bar}


def _build_entry_plan(w: dict, dec: eg.BarDecision, df: pd.DataFrame, pos: int,
                      atr: float, now: pd.Timestamp, lag: int) -> dict:
    """الأهداف الهيكلية + العقد لحظة التأكيد. يُرجع {} لو الخطة غير صالحة."""
    symbol, direction = w["symbol"], w["direction"]
    entry, stop = float(dec.details["entry_price"]), float(dec.details["stop_price"])
    df1h = df1d = None
    zones: list = []
    try:
        df1h = dc.get_bars(symbol, "1h", "14d")
        df1d = dc.get_bars(symbol, "1d", "90d")
        df4h = dc.get_bars(symbol, "4h", "30d")
        from htf_zones import get_htf_analysis
        zones = get_htf_analysis(symbol, df1h, df4h, df1d).get("zones", [])
    except Exception as e:
        print(f"  [shadow] levels ctx {symbol}: {e}")
    levels = eg.collect_levels(direction, entry, df.iloc[:pos + 1], df1h, df1d, zones)
    tg = eg.structural_targets(direction, entry, stop, atr, levels)
    if not tg:
        return {}

    # العقد: نفس اختيار الحي (analyzer._get_contract) ثم اقتباس حي للعقد المحدد
    expiry = strike = None
    chain_px = 0.0
    is_scalp = False
    try:
        from analyzer import _get_contract, SCALP_ATR_PCT
        is_scalp = (atr / entry) < SCALP_ATR_PCT if entry > 0 else False
        expiry, strike, chain_px, _d, _iv, _th = _get_contract(
            symbol, direction, entry, is_scalp=is_scalp, score=float(w.get("score") or 0))
    except Exception as e:
        print(f"  [shadow] contract {symbol}: {e}")
    quote = 0.0
    if expiry and strike:
        try:
            quote = dc.get_option_price_by_contract(symbol, strike, expiry, direction)
        except Exception:
            quote = 0.0
    if quote > 0:
        entry_opt, opt_src = quote, "quote"
    elif chain_px and chain_px > 0:
        entry_opt, opt_src = float(chain_px), "chain"
    else:
        entry_opt, opt_src = None, None

    return {
        "confirm_type":       dec.reason,
        "confirm_bar_ts":     _iso(dec.bar_ts),
        "confirm_close_loc":  dec.details.get("close_loc"),
        "stop_basis":         dec.details.get("stop_basis"),
        "atr_at_confirm":     round(float(atr), 4),
        "entry_price":        entry,
        "stop_price":         stop,
        "target1":            tg["target1"],
        "target1_source":     tg["target1_source"],
        "target2":            tg["target2"],
        "target2_source":     tg["target2_source"],
        "rr":                 tg["rr"],
        "risk_atr":           tg["risk_atr"],
        "is_scalp":           bool(is_scalp),
        "expiry":             expiry,
        "strike":             strike,
        "entry_option_price": entry_opt,
        "entry_option_src":   opt_src,
        "entry_eval_lag_sec": int(lag),
        "entered_at":         _iso(now),
        "last_tick_at":       _iso(now),
    }


# ─── entered: مرآة price_monitor._check ──────────────────────────────────────

def _contract_dead(w: dict, now: pd.Timestamp) -> bool:
    from outcome_tracker import _contract_dead as _dead
    return _dead({"expiry": w.get("expiry"), "created_at": w.get("entered_at")}, now.to_pydatetime())


def _check_entered(w: dict, price: float, now: pd.Timestamp) -> None:
    wid, symbol, direction = w["id"], w["symbol"], w["direction"]
    entry, stop = float(w["entry_price"]), float(w["stop_price"])
    t1, t2, rr = float(w["target1"]), float(w["target2"]), float(w.get("rr") or 1.5)
    t1_hit = bool(w.get("t1_hit_at"))
    peak   = float(w["peak_price"]) if w.get("peak_price") is not None else None

    if _contract_dead(w, now):
        _close(w, "expired", "contract_expired", price, 0.0, price, now)
        return

    # فجوة (توقف/إعادة تشغيل): حكم تحفّظي من الشموع المغلقة الفائتة أولاً
    last_tick = _parse(w.get("last_tick_at")) or _parse(w.get("entered_at"))
    if last_tick is not None and (now - last_tick).total_seconds() > GAP_SECONDS:
        try:
            df = eg.closed_bars(dc.get_bars(symbol, "5m", "1d"), now)
            # كل شمعة أُغلقت بعد آخر tick (حتى لو بدأت قبله) — بدايتها > آخر tick − مدة الشمعة
            since_bar = (last_tick - pd.Timedelta(minutes=eg.BAR_MINUTES))
            bars = df[df.index > since_bar.tz_convert(df.index.tz)] if df is not None and len(df) else df
            if bars is not None and len(bars):
                res = eg.evaluate_exit_bars(direction, bars, entry, stop, t1, t2, rr, t1_hit, peak)
                _event(wid, "gap", before="entered", after="entered",
                       checks={"bars": int(len(bars)), "since": _iso(last_tick), "result": res.get("action")})
                if res["action"] == "close":
                    _close(w, res["status"], res["exit_reason"], float(res["outcome_price"]),
                           float(res["r_planned"]), price, now, gap=True)
                    return
                if res["t1_hit"] and not t1_hit:
                    t1_hit, peak = True, res["peak"]
                    db.shadow_update_watch(wid, {"t1_hit_at": _iso(now), "peak_price": peak}, expect_state="entered")
                    _event(wid, "t1", before="entered", after="entered", checks={"from_bars": True, "peak": peak})
                elif res.get("peak") is not None:
                    peak = res["peak"]
        except Exception as e:
            print(f"  [shadow] gap #{wid}: {e}")

    res = eg.evaluate_exit(direction, price, entry, stop, t1, t2, rr, t1_hit, peak)
    if res["action"] == "close":
        _close(w, res["status"], res["exit_reason"], float(res["outcome_price"]),
               float(res["r_planned"]), price, now)
        return
    fields = {"last_tick_at": _iso(now)}
    if res["action"] == "t1":
        fields.update(t1_hit_at=_iso(now), peak_price=res["peak"])
    elif res.get("peak") is not None and res["peak"] != peak:
        fields["peak_price"] = res["peak"]
    if db.shadow_update_watch(wid, fields, expect_state="entered") and res["action"] == "t1":
        _event(wid, "t1", before="entered", after="entered", checks={"price": price, "peak": res["peak"]})
        print(f"  [shadow] ✅ #{wid} {symbol} T1 ظل @ {price}")


def _close(w: dict, status: str, exit_reason: str, outcome_price: float, r_planned: float,
           price: float, now: pd.Timestamp, gap: bool = False) -> None:
    wid, symbol, direction = w["id"], w["symbol"], w["direction"]
    entry, stop = float(w["entry_price"]), float(w["stop_price"])
    real_opt = 0.0
    try:
        real_opt = dc.get_option_price_by_contract(symbol, w.get("strike"), w.get("expiry"), direction)
    except Exception:
        real_opt = 0.0
    entry_opt = w.get("entry_option_price")
    pnl = None
    if real_opt and real_opt > 0 and entry_opt and float(entry_opt) > 0:
        pnl = round((real_opt - float(entry_opt)) / float(entry_opt) * 100, 2)
    ex: dict = {}
    entered_at = _parse(w.get("entered_at"))
    try:
        # MFE/MAE من الشموع بعد شمعة التأكيد (الدخول = إغلاقها) + السعر الحالي
        df = eg.closed_bars(dc.get_bars(symbol, "5m", "1d"), now)
        conf = _parse(w.get("confirm_bar_ts"))
        if conf is None and entered_at is not None:
            conf = entered_at - pd.Timedelta(minutes=eg.BAR_MINUTES)
        bars = df[df.index > conf.tz_convert(df.index.tz)] if (df is not None and len(df) and conf is not None) else None
        ex = eg.excursions(direction, bars, entry, stop, extra_price=price)
    except Exception:
        ex = {}
    dur = int((now - entered_at).total_seconds() / 60) if entered_at is not None else None
    fields = {
        "state": "closed", "status": status, "exit_reason": exit_reason,
        "outcome_price": round(float(outcome_price), 2), "r_planned": round(float(r_planned), 3),
        "r_actual": eg.r_actual(direction, entry, stop, float(outcome_price)) if status != "expired" else None,
        "exit_option_price": round(real_opt, 2) if real_opt > 0 else None,
        "option_pnl_pct": pnl, "closed_at": _iso(now), "last_tick_at": _iso(now),
        "duration_min": dur, "gap": bool(gap), **ex,
    }
    if db.shadow_update_watch(wid, fields, expect_state="entered"):
        _event(wid, "exit", before="entered", after="closed",
               checks={"status": status, "exit_reason": exit_reason, "price": price,
                       "outcome_price": outcome_price, "option_pnl_pct": pnl, "gap": gap})
        print(f"  [shadow] {'✅' if status in ('hit_t1', 'hit_t2') else '❌'} #{wid} {symbol} خروج ظل: "
              f"{status}/{exit_reason} @ {outcome_price} | عقد {pnl if pnl is not None else '—'}%")


# ─── قياس الرجوع للمنطقة بعد timeout (لا إعادة تسليح) ─────────────────────────

def _observe_return(w: dict, df: pd.DataFrame, now: pd.Timestamp) -> None:
    zone, direction = _watch_zone(w), w["direction"]
    positions = eg.candidate_positions(df, since_ts=_parse(w.get("cancelled_at")),
                                       last_bar_ts=_parse(w.get("last_bar_ts")))
    if not positions:
        return
    fields: dict = {}
    left = _parse(w.get("left_zone_at"))
    for pos in positions:
        bar, ts = df.iloc[pos], df.index[pos]
        fields["last_bar_ts"] = _iso(ts)
        if left is None:
            outside = float(bar["Low"]) > zone.high if direction == "call" else float(bar["High"]) < zone.low
            if outside:
                left = ts
                fields["left_zone_at"] = _iso(ts)
        elif eg.overlaps(bar, zone):
            fields["returned_at"] = _iso(ts)
            break
    if db.shadow_update_watch(w["id"], fields, expect_state="cancelled") and fields.get("returned_at"):
        _event(w["id"], "return", bar_ts=_parse(fields["returned_at"]), before="cancelled", after="cancelled",
               checks={"left_zone_at": fields.get("left_zone_at") or w.get("left_zone_at")})
        print(f"  [shadow] ↩ #{w['id']} {w['symbol']} رجع للمنطقة بعد timeout")


# ─── الملخّص اليومي (الرسالة الوحيدة من الظل) ─────────────────────────────────

def _build_daily_summary(rows: list, today: str) -> str:
    n = len(rows)
    st = Counter(r.get("state") for r in rows)
    no_zone, conflict = st.get("no_zone", 0), st.get("zone_conflict", 0)
    alt = sum(1 for r in rows if r.get("state") == "zone_conflict" and r.get("alt_zone_found"))
    watched = n - no_zone - conflict
    touched = sum(1 for r in rows if r.get("arrival_bar_ts"))
    entered = [r for r in rows if r.get("entered_at")]
    ct = Counter(r.get("confirm_type") for r in entered)
    cancelled = [r for r in rows if r.get("state") == "cancelled"]
    cr = Counter(r.get("cancel_reason") for r in cancelled)
    timeouts = [r for r in cancelled if r.get("cancel_reason") == "timeout"]
    returned = sum(1 for r in timeouts if r.get("returned_at"))
    closed = [r for r in entered if r.get("state") == "closed"]
    decided = [r for r in closed if r.get("status") in ("hit_t1", "hit_t2", "stopped")]
    pnls = [float(r["option_pnl_pct"]) for r in decided if r.get("option_pnl_pct") is not None]
    wins = sum(1 for p in pnls if p > 0)
    r_planned = sum(float(r.get("r_planned") or 0) for r in decided)
    open_cnt = sum(1 for r in entered if r.get("state") == "entered")
    fb = sum(1 for r in entered if r.get("target1_source") == "fallback_2r")
    lines = [
        f"🕶 ملخّص الظل — {today}",
        f"{'─'*30}",
        f"إشارات مسجّلة: {n} | بلا منطقة: {no_zone} | معاكسة: {conflict} (بديلة موافقة: {alt}) | تحت المراقبة: {watched}",
        f"وصلت المنطقة: {touched} | دخلت: {len(entered)}"
        + (f" (rejection {ct.get('rejection', 0)}, cisd {ct.get('cisd', 0)}, ifvg {ct.get('ifvg', 0)}, fvg {ct.get('fvg', 0)})" if entered else ""),
        f"أُلغيت: {len(cancelled)} (break {cr.get('break', 0)}, timeout {cr.get('timeout', 0)}, eod {cr.get('eod', 0)}"
        + (f", stale {cr.get('stale', 0)}" if cr.get('stale') else "") + f") | رجعت بعد timeout: {returned}/{len(timeouts)}",
        f"{'─'*30}",
        f"محسومة: {len(decided)} | بلا بيانات عقد: {len(decided) - len(pnls)} | مفتوحة: {open_cnt}"
        + (f" | هدف احتياطي 2R: {fb}" if fb else ""),
    ]
    if pnls:
        lines.append(f"💵 ربح العقد: مجموع {sum(pnls):+.1f}% | متوسط {sum(pnls)/len(pnls):+.1f}% | WR {round(wins/len(pnls)*100)}% ({wins}/{len(pnls)})")
    lines.append(f"📐 R مخطّط: {r_planned:+.2f}R")
    lines.append("قياس فقط — الظل لا يرسل إشارات")
    return "\n".join(lines)


def _send_daily_summary(now: pd.Timestamp) -> None:
    rows = db.shadow_get_watches(since_iso=_today_start_utc_iso(now))
    if not rows:
        return
    send(_build_daily_summary(rows, now.strftime("%Y-%m-%d")), config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
    print("  [shadow] 📊 أُرسل ملخّص الظل اليومي")


def _summary_loop() -> None:
    global _summary_sent_date
    while True:
        time.sleep(60)
        try:
            now = _now_et()
            today = now.strftime("%Y-%m-%d")
            if now.weekday() < 5 and now.hour == 15 and now.minute >= 52 and today != _summary_sent_date:
                _summary_sent_date = today
                _send_daily_summary(now)
        except Exception as e:
            print(f"  [shadow] summary: {e}")
