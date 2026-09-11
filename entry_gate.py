"""
Entry Gate — بوابة الدخول بالتأكيد عند منطقة HTF (المرحلة ٢).

منطق نقي بالكامل: DataFrame + حالة → قرار. لا شبكة، لا Supabase، لا Telegram.
يستهلكه shadow_gate.py (وضع الظل)، ومستقبلاً price_monitor عند التحويل الحي.

التدفّق (يُقيَّم على شموع 5m مُغلقة داخل الجلسة 09:30–16:00 ET فقط):
  armed   → أول شمعة يتداخل مداها مع منطقة HTF          → touched (شمعة ١ من ٦)
  touched → eod | break | تأكيد (rejection→cisd→ifvg→fvg) | timeout عند الشمعة ٦
  entered → T2 / Stop / T1 ثم Trailing (نفس قواعد price_monitor._check)

القرارات المقفولة (2026-09-11):
  • الوصول = شمعة ١ من ٦ · لا دخول بعد 15:00 ET · armed ينتهي عند 15:00 بلا حمل لليوم التالي
  • هامش الستوب 0.15×ATR(14, 5m) · منطقة عرضها > 1×ATR → الستوب خلف الشمعة المؤكِّدة
  • displacement يُسجَّل كعلم فقط ولا يُدخِل
  • الهدف هيكلي (المستوى التالي)؛ لا مستوى ضمن 4R → 2R موسوم fallback_2r (الظل فقط)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pytz

from htf_zones import (cisd_5m, displacement_5m, inversion_fvg_confirms_zone)
from market_structure import (_find_fvg, _pivot_levels, detect_structure_dual,
                              detect_equal_levels, prev_period_levels)

ET = pytz.timezone("America/New_York")

# ─── ثوابت البوابة ────────────────────────────────────────────────────────────
BAR_MINUTES        = 5
MAX_CONFIRM_BARS   = 6          # شمعة الوصول تُعدّ ١ من ٦
STOP_MARGIN_ATR    = 0.15       # نفس ثابت HTF Stop Override في analyzer
WIDE_ZONE_ATR      = 1.0        # عرض المنطقة > 1×ATR → الستوب خلف الشمعة المؤكِّدة
ENTRY_CUTOFF_ET    = (15, 0)    # لا دخول (ولا انتظار) بعد 15:00 ET
SESSION_OPEN_ET    = (9, 30)
SESSION_CLOSE_ET   = (16, 0)
REJECTION_LOOKBACK = 5          # أطول ذيل مماثل في آخر ٥ شموع
MIN_T1_R           = 1.0        # الهدف الأول يبعد ≥ 1R
MIN_T1_ATR         = 0.5        # و ≥ 0.5×ATR (تجاهل المستويات المجهرية)
MAX_T1_R           = 4.0        # نفس سقف MAX_RR في analyzer
MAX_T2_R           = 6.0
FALLBACK_T1_R      = 2.0
CONFIRM_ORDER      = ("rejection", "cisd", "ifvg", "fvg")


# ─── نماذج البيانات ───────────────────────────────────────────────────────────

@dataclass
class GateZone:
    """منطقة HTF كما تراها البوابة (duck-typed مع HTFZone: low/high فقط تُستهلك)."""
    low:       float
    high:      float
    direction: str = ""    # demand | supply
    timeframe: str = ""    # 1h | 4h | daily
    zone_type: str = ""    # ob | fvg | inv_fvg

    @property
    def width(self) -> float:
        return float(self.high) - float(self.low)


@dataclass
class BarDecision:
    """نتيجة تقييم شمعة مغلقة واحدة."""
    bar_ts:    pd.Timestamp
    action:    str = "none"      # none | enter | cancel
    reason:    str = ""          # enter: confirm_type | cancel: break | timeout | eod
    touched:   bool = False      # هذه الشمعة هي شمعة الوصول
    bars_seen: int = 0
    checks:    dict = field(default_factory=dict)
    details:   dict = field(default_factory=dict)


# ─── وقت وجلسة ────────────────────────────────────────────────────────────────

def to_et(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize(ET) if t.tzinfo is None else t.tz_convert(ET)


def bar_close_ts(ts, minutes: int = BAR_MINUTES) -> pd.Timestamp:
    """طابع Alpaca = بداية الشمعة؛ الإغلاق = البداية + المدة."""
    return pd.Timestamp(ts) + pd.Timedelta(minutes=minutes)


def is_session_bar(ts) -> bool:
    """شمعة تبدأ داخل الجلسة النظامية [09:30, 16:00) ET."""
    t = to_et(ts)
    return SESSION_OPEN_ET <= (t.hour, t.minute) < SESSION_CLOSE_ET


def after_cutoff(ts, minutes: int = BAR_MINUTES) -> bool:
    """هل تُغلق هذه الشمعة بعد 15:00 ET؟ (شمعة 14:55→15:00 مسموحة، 15:00→15:05 لا)."""
    c = to_et(bar_close_ts(ts, minutes))
    return (c.hour, c.minute, c.second) > (ENTRY_CUTOFF_ET[0], ENTRY_CUTOFF_ET[1], 0)


def closed_bars(df: pd.DataFrame, now, minutes: int = BAR_MINUTES,
                grace_sec: int = 0) -> pd.DataFrame:
    """يُبقي الشموع المغلقة فقط: بداية + مدة ≤ now − grace. يتجاهل الشمعة قيد التكوين."""
    if df is None or len(df) == 0:
        return df
    if df.index.tz is None:
        df = df.set_index(df.index.tz_localize(ET))
    cutoff = to_et(now) - pd.Timedelta(seconds=grace_sec)
    mask = (df.index + pd.Timedelta(minutes=minutes)) <= cutoff
    return df[mask]


def candidate_positions(df: pd.DataFrame, since_ts=None, last_bar_ts=None) -> List[int]:
    """
    مواضع الشموع الواجب تقييمها بالترتيب: داخل الجلسة، بدايتها ≥ since_ts (لا نظر للخلف
    قبل التسجيل)، وأحدث من last_bar_ts (idempotent عبر إعادة التشغيل).
    """
    out: List[int] = []
    since = to_et(since_ts) if since_ts is not None else None
    last  = to_et(last_bar_ts) if last_bar_ts is not None else None
    for pos, ts in enumerate(df.index):
        if not is_session_bar(ts):
            continue
        if since is not None and ts < since:
            continue
        if last is not None and ts <= last:
            continue
        out.append(pos)
    return out


def floor_5m(ts) -> pd.Timestamp:
    """يعيد بداية الشمعة 5m التي تحتوي هذا الوقت (لتحديد أول شمعة مرشّحة بعد التسجيل)."""
    t = to_et(ts)
    return t.floor(f"{BAR_MINUTES}min")


# ─── مؤشرات مساعدة ────────────────────────────────────────────────────────────

def atr_5m(df: pd.DataFrame, period: int = 14) -> float:
    """مطابق لـ analyzer._atr (EMA على True Range). fallback: متوسط TR إن قلّت الشموع."""
    if df is None or len(df) < 2:
        return 0.0
    prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low'] - prev).abs(),
    ], axis=1).max(axis=1)
    v = float(tr.ewm(com=period - 1, min_periods=period).mean().iloc[-1])
    if not np.isfinite(v):
        v = float(tr.dropna().mean()) if tr.notna().any() else 0.0
    return v


def overlaps(bar, zone: GateZone) -> bool:
    return float(bar['Low']) <= float(zone.high) and float(bar['High']) >= float(zone.low)


def is_break(bar, zone: GateZone, direction: str) -> bool:
    """إغلاق خلف المنطقة عكس الاتجاه = اختراق → إلغاء."""
    c = float(bar['Close'])
    return c < float(zone.low) if direction == 'call' else c > float(zone.high)


def close_location(close: float, zone: GateZone, direction: str) -> str:
    """inside داخل المنطقة | beyond خلفها في اتجاه الصفقة | behind خلفها عكس الاتجاه."""
    lo, hi = float(zone.low), float(zone.high)
    if lo <= close <= hi:
        return 'inside'
    if direction == 'call':
        return 'beyond' if close > hi else 'behind'
    return 'beyond' if close < lo else 'behind'


# ─── التأكيدات (واعية بشمعة الوصول) ───────────────────────────────────────────

def rejection(df: pd.DataFrame, pos: int, zone: GateZone, direction: str,
              lookback: int = REJECTION_LOOKBACK) -> dict:
    """
    شمعة رافضة (جديد): ذيلها في اتجاه المنطقة > جسمها و > أطول ذيل مماثل في آخر
    `lookback` شموع، يكسر حدّ المنطقة بالذيل، وتُغلق داخل المنطقة أو خلفها في
    اتجاه الصفقة. يُرجع dict فيه ok + التفاصيل (تُسجَّل في checks).
    """
    bar = df.iloc[pos]
    o, h, l, c = (float(bar['Open']), float(bar['High']),
                  float(bar['Low']),  float(bar['Close']))
    body = abs(c - o)
    prev = df.iloc[max(0, pos - lookback):pos]
    if direction == 'call':
        wick      = min(o, c) - l
        broke     = l < float(zone.low)
        closed_ok = c >= float(zone.low)
        prev_w    = (prev[['Open', 'Close']].min(axis=1) - prev['Low']) if len(prev) else None
    else:
        wick      = h - max(o, c)
        broke     = h > float(zone.high)
        closed_ok = c <= float(zone.high)
        prev_w    = (prev['High'] - prev[['Open', 'Close']].max(axis=1)) if len(prev) else None
    prev_max = float(prev_w.max()) if prev_w is not None and len(prev_w) else 0.0
    ok = bool(broke and closed_ok and wick > body and wick > prev_max and len(prev) >= 1)
    return {
        'ok': ok,
        'wick': round(wick, 4), 'body': round(body, 4), 'prev_max_wick': round(prev_max, 4),
        'broke_boundary': bool(broke), 'closed_ok': bool(closed_ok),
        'close_loc': close_location(c, zone, direction),
    }


def cisd_after(df: pd.DataFrame, pos: int, direction: str) -> bool:
    """cisd_5m على الشموع حتى الشمعة المُقيَّمة (إغلاق التأكيد بعد الوصول بالبناء)."""
    bull, bear = cisd_5m(df.iloc[:pos + 1])
    return bool(bull if direction == 'call' else bear)


def ifvg_after(df: pd.DataFrame, pos: int, arrival_pos: int,
               zone: GateZone, direction: str) -> bool:
    """فجوة منقلبة تتقاطع مع المنطقة، إغلاقها الكاسر عند شمعة الوصول أو بعدها."""
    return bool(inversion_fvg_confirms_zone(df.iloc[:pos + 1], zone, direction,
                                            since_idx=arrival_pos))


def fvg_after(df: pd.DataFrame, pos: int, arrival_pos: int,
              zone: GateZone, direction: str) -> bool:
    """
    FVG على 5m تكوّنت بعد الوصول (شمعتها الثالثة ≥ شمعة الوصول) وتتقاطع مع المنطقة
    ولم تُملأ بعد. نافذة الحساب تبدأ شمعتين قبل الوصول (لتكوين النمط الثلاثي).
    """
    window = df.iloc[max(0, arrival_pos - 2):pos + 1]
    if len(window) < 3:
        return False
    for lo, hi, t in _find_fvg(window, limit=10):
        if direction == 'call' and t == 'bullish' and lo <= zone.high and hi >= zone.low:
            return True
        if direction == 'put' and t == 'bearish' and lo <= zone.high and hi >= zone.low:
            return True
    return False


def run_checks(df: pd.DataFrame, pos: int, arrival_pos: int, zone: GateZone,
               direction: str, atr: float) -> dict:
    """كل الأعلام تُحسب وتُسجَّل (displacement علم فقط لا يُدخِل)."""
    return {
        'rejection':    rejection(df, pos, zone, direction),
        'cisd':         cisd_after(df, pos, direction),
        'ifvg':         ifvg_after(df, pos, arrival_pos, zone, direction),
        'fvg':          fvg_after(df, pos, arrival_pos, zone, direction),
        'displacement': bool(displacement_5m(df.iloc[:pos + 1], direction, atr)),
    }


def first_confirmation(checks: dict) -> Optional[str]:
    for name in CONFIRM_ORDER:
        v = checks.get(name)
        if isinstance(v, dict):
            v = v.get('ok')
        if v:
            return name
    return None


# ─── الستوب والأهداف ──────────────────────────────────────────────────────────

def compute_stop(direction: str, confirm_type: str, bar, zone: GateZone, atr: float,
                 margin_atr: float = STOP_MARGIN_ATR,
                 wide_mult: float = WIDE_ZONE_ATR) -> Tuple[float, str]:
    """
    rejection → خلف قاع/قمة الشمعة الرافضة (basis=candle)
    cisd/fvg/ifvg → خلف حدّ المنطقة (basis=zone)
    منطقة عريضة (> wide_mult×ATR) → خلف الشمعة المؤكِّدة أياً كان النوع (basis=wide_zone_candle)
    """
    wide = zone.width > wide_mult * atr
    if wide:
        basis = 'wide_zone_candle'
        ref   = float(bar['Low']) if direction == 'call' else float(bar['High'])
    elif confirm_type == 'rejection':
        basis = 'candle'
        ref   = float(bar['Low']) if direction == 'call' else float(bar['High'])
    else:
        basis = 'zone'
        ref   = float(zone.low) if direction == 'call' else float(zone.high)
    margin = margin_atr * atr
    stop = ref - margin if direction == 'call' else ref + margin
    return round(stop, 2), basis


def collect_levels(direction: str, price: float, df5: pd.DataFrame,
                   df1h: Optional[pd.DataFrame] = None,
                   df1d: Optional[pd.DataFrame] = None,
                   zones: Optional[list] = None) -> List[Tuple[float, str]]:
    """
    مرشّحات الهدف الهيكلي في اتجاه الصفقة، الأقرب أولاً: PDH/PDL، PWH/PWL، PMH/PML،
    قمم/قيعان swing 5m و 1h، strong/weak/trailing، EQH/EQL، حافة أقرب منطقة HTF معاكسة.
    """
    up = direction == 'call'
    out: List[Tuple[float, str]] = []

    def add(v, src: str):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if not np.isfinite(v) or v <= 0:
            return
        if (up and v > price) or ((not up) and v < price):
            out.append((round(v, 2), src))

    if df1d is not None and len(df1d) >= 2:
        add(df1d['High'].iloc[-2] if up else df1d['Low'].iloc[-2], 'pdh' if up else 'pdl')
        pp = prev_period_levels(df1d)
        add(pp['pwh'] if up else pp['pwl'], 'pwh' if up else 'pwl')
        add(pp['pmh'] if up else pp['pml'], 'pmh' if up else 'pml')

    if df5 is not None and len(df5) >= 11:
        hs, ls = _pivot_levels(df5.tail(120))
        for v in (hs if up else ls):
            add(v, 'swing_5m')
        try:
            strength = detect_structure_dual(df5, swing_size=15, internal_size=5)['swing'].get('strength', {}) or {}
            keys = ('strong_high', 'weak_high', 'trailing_top') if up else ('strong_low', 'weak_low', 'trailing_bottom')
            for k in keys:
                add(strength.get(k), k)
        except Exception:
            pass
        try:
            eq = detect_equal_levels(df5, pivot_size=3, threshold_atr_mult=0.1, atr_period=50)
            add(eq['eqh_level'] if up else eq['eql_level'], 'eqh' if up else 'eql')
        except Exception:
            pass

    if df1h is not None and len(df1h) >= 11:
        hs, ls = _pivot_levels(df1h.tail(120))
        for v in (hs if up else ls):
            add(v, 'swing_1h')

    for z in zones or []:
        if up and getattr(z, 'direction', '') == 'supply':
            add(z.low, f"htf_{getattr(z, 'timeframe', '')}_{getattr(z, 'zone_type', '')}")
        elif (not up) and getattr(z, 'direction', '') == 'demand':
            add(z.high, f"htf_{getattr(z, 'timeframe', '')}_{getattr(z, 'zone_type', '')}")

    dedup: Dict[float, str] = {}
    for v, s in out:
        dedup.setdefault(v, s)
    return sorted(dedup.items(), key=lambda kv: kv[0], reverse=not up)


def structural_targets(direction: str, entry: float, stop: float, atr: float,
                       levels: List[Tuple[float, str]]) -> Optional[dict]:
    """
    T1 = أقرب مستوى يبعد ≥ max(1R, 0.5×ATR) وضمن 4R، وإلا 2R موسوم fallback_2r.
    T2 = المستوى التالي بعد T1 بمسافة ≥ 0.5×ATR وضمن 6R، وإلا امتداد T1 + (T1−E).
    `levels` مرتّبة الأقرب أولاً (كما تعيدها collect_levels).
    """
    up = direction == 'call'
    risk = (entry - stop) if up else (stop - entry)
    if risk <= 0:
        return None
    dist = (lambda v: v - entry) if up else (lambda v: entry - v)
    min_d, max_d = max(MIN_T1_R * risk, MIN_T1_ATR * atr), MAX_T1_R * risk

    t1 = t1s = None
    for v, s in levels:
        if dist(v) < min_d:
            continue
        if dist(v) <= max_d:
            t1, t1s = v, s
        break
    if t1 is None:
        t1  = entry + FALLBACK_T1_R * risk if up else entry - FALLBACK_T1_R * risk
        t1s = 'fallback_2r'

    t2 = t2s = None
    for v, s in levels:
        d2 = (v - t1) if up else (t1 - v)
        if d2 >= MIN_T1_ATR * atr and dist(v) <= MAX_T2_R * risk:
            t2, t2s = v, s
            break
    if t2 is None:
        t2  = t1 + (t1 - entry) if up else t1 - (entry - t1)
        t2s = 'extension'

    return {
        'target1': round(t1, 2), 'target1_source': t1s,
        'target2': round(t2, 2), 'target2_source': t2s,
        'rr': round(dist(t1) / risk, 2),
        'risk': round(risk, 4),
        'risk_atr': round(risk / atr, 3) if atr > 0 else None,
    }


# ─── آلة الحالة (armed / touched) على شمعة مغلقة واحدة ────────────────────────

def step_bar(state: dict, df: pd.DataFrame, pos: int, atr: float) -> Tuple[dict, BarDecision]:
    """
    يُقيّم شمعة مغلقة واحدة (الموضع pos في df) ويُرجع (الحالة الجديدة, القرار).
    state: {state: armed|touched, direction, zone: GateZone, arrival_pos: int|None, bars_seen: int}
    ترتيب الفحص في touched: eod → break → التأكيدات → timeout.
    نقي: لا يكتب شيئاً؛ المُستدعي مسؤول عن idempotency (last_bar_ts).
    """
    st = dict(state)
    bar, ts = df.iloc[pos], df.index[pos]
    zone, direction = st['zone'], st['direction']
    dec = BarDecision(bar_ts=ts, bars_seen=int(st.get('bars_seen') or 0))

    if st['state'] == 'armed':
        if after_cutoff(ts):
            st['state'] = 'cancelled'
            dec.action, dec.reason = 'cancel', 'eod'
            return st, dec
        if not overlaps(bar, zone):
            return st, dec
        st.update(state='touched', arrival_pos=pos, arrival_ts=ts, bars_seen=0)
        dec.touched = True

    if st['state'] != 'touched':
        return st, dec

    st['bars_seen'] = int(st.get('bars_seen') or 0) + 1
    dec.bars_seen = st['bars_seen']

    if after_cutoff(ts):
        st['state'] = 'cancelled'
        dec.action, dec.reason = 'cancel', 'eod'
        return st, dec

    if is_break(bar, zone, direction):
        st['state'] = 'cancelled'
        dec.action, dec.reason = 'cancel', 'break'
        dec.checks = {'break': True, 'close': float(bar['Close'])}
        return st, dec

    arrival_pos = st.get('arrival_pos')
    if arrival_pos is None:
        arrival_pos = pos
    checks = run_checks(df, pos, int(arrival_pos), zone, direction, atr)
    checks['break'] = False
    dec.checks = checks

    ct = first_confirmation(checks)
    if ct:
        close = float(bar['Close'])
        stop, basis = compute_stop(direction, ct, bar, zone, atr)
        st['state'] = 'entered'
        dec.action, dec.reason = 'enter', ct
        dec.details = {
            'entry_price':    round(close, 2),
            'stop_price':     stop,
            'stop_basis':     basis,
            'close_loc':      close_location(close, zone, direction),
            'atr':            round(float(atr), 4),
            'zone_width_atr': round(zone.width / atr, 3) if atr > 0 else None,
            'bar_low':        float(bar['Low']),
            'bar_high':       float(bar['High']),
        }
        return st, dec

    if st['bars_seen'] >= MAX_CONFIRM_BARS:
        st['state'] = 'cancelled'
        dec.action, dec.reason = 'cancel', 'timeout'
    return st, dec


# ─── إدارة الصفقة بعد الدخول (مرآة price_monitor._check) ─────────────────────

def _reached(direction: str, price: float, level: float) -> bool:
    return price >= level if direction == 'call' else price <= level


def evaluate_exit(direction: str, price: float, entry: float, stop: float,
                  t1: float, t2: float, rr: float,
                  t1_hit: bool = False, peak: Optional[float] = None) -> dict:
    """
    نفس ترتيب price_monitor._check: T2 (يغلق) → Stop (يغلق: stopped أو stop_after_t1)
    → T1 (لا يغلق، يبدأ Trailing) → Trailing بنصف مسافة الدخول→T1.
    يُرجع {'action': none|t1|close, ...status/exit_reason/outcome_price/r_planned, t1_hit, peak}
    """
    up = direction == 'call'
    res = {'action': 'none', 't1_hit': bool(t1_hit), 'peak': peak}

    if _reached(direction, price, t2):
        return {**res, 'action': 'close', 'status': 'hit_t2', 'exit_reason': 'target2',
                'outcome_price': t2, 'r_planned': rr}

    stopped = price <= stop if up else price >= stop
    if stopped:
        if t1_hit:
            return {**res, 'action': 'close', 'status': 'hit_t1', 'exit_reason': 'stop_after_t1',
                    'outcome_price': t1, 'r_planned': rr * 0.5}
        return {**res, 'action': 'close', 'status': 'stopped', 'exit_reason': 'stop',
                'outcome_price': stop, 'r_planned': -1.0}

    if not t1_hit and _reached(direction, price, t1):
        res.update(action='t1', t1_hit=True, peak=price)
        t1_hit, peak = True, price

    if t1_hit:
        gap  = abs(t1 - entry) * 0.5
        peak = (max(peak if peak is not None else price, price) if up
                else min(peak if peak is not None else price, price))
        res['peak'] = peak
        if up and price <= peak - gap:
            return {**res, 'action': 'close', 'status': 'hit_t1', 'exit_reason': 'trailing_stop',
                    'outcome_price': round(peak - gap, 2), 'r_planned': rr * 0.5}
        if (not up) and price >= peak + gap:
            return {**res, 'action': 'close', 'status': 'hit_t1', 'exit_reason': 'trailing_stop',
                    'outcome_price': round(peak + gap, 2), 'r_planned': rr * 0.5}
    return res


def evaluate_exit_bars(direction: str, bars: pd.DataFrame, entry: float, stop: float,
                       t1: float, t2: float, rr: float,
                       t1_hit: bool = False, peak: Optional[float] = None) -> dict:
    """
    حكم من شموع مغلقة فائتة (بعد توقف/إعادة تشغيل). ترتيب الأحداث داخل الشمعة الواحدة
    مجهول، فيُؤخذ تحفّظياً (السلبي قبل الإيجابي):
      الستوب → Trailing مقابل قمة الشموع السابقة → T2 → T1 / تحديث القمة.
    يُعلَّم gap=True عند أي إغلاق.
    """
    up = direction == 'call'
    gap_dist = abs(t1 - entry) * 0.5
    for _, b in bars.iterrows():
        hi, lo = float(b['High']), float(b['Low'])
        fav, adv = (hi, lo) if up else (lo, hi)
        if (lo <= stop) if up else (hi >= stop):
            if t1_hit:
                return {'action': 'close', 'status': 'hit_t1', 'exit_reason': 'stop_after_t1',
                        'outcome_price': t1, 'r_planned': rr * 0.5, 't1_hit': True, 'peak': peak, 'gap': True}
            return {'action': 'close', 'status': 'stopped', 'exit_reason': 'stop',
                    'outcome_price': stop, 'r_planned': -1.0, 't1_hit': False, 'peak': peak, 'gap': True}
        if t1_hit and peak is not None:
            trail = peak - gap_dist if up else peak + gap_dist
            if (adv <= trail) if up else (adv >= trail):
                return {'action': 'close', 'status': 'hit_t1', 'exit_reason': 'trailing_stop',
                        'outcome_price': round(trail, 2), 'r_planned': rr * 0.5,
                        't1_hit': True, 'peak': peak, 'gap': True}
        if _reached(direction, fav, t2):
            return {'action': 'close', 'status': 'hit_t2', 'exit_reason': 'target2',
                    'outcome_price': t2, 'r_planned': rr, 't1_hit': t1_hit, 'peak': peak, 'gap': True}
        if not t1_hit and _reached(direction, fav, t1):
            t1_hit, peak = True, fav
        elif t1_hit:
            peak = max(peak, fav) if up else min(peak, fav)
    return {'action': 'none', 't1_hit': t1_hit, 'peak': peak, 'gap': False}


def r_actual(direction: str, entry: float, stop: float, outcome_price: float) -> float:
    """R الفعلي من سعر الخروج (لا R المخطّط)."""
    risk = (entry - stop) if direction == 'call' else (stop - entry)
    if risk <= 0:
        return 0.0
    move = (outcome_price - entry) if direction == 'call' else (entry - outcome_price)
    return round(move / risk, 3)


def excursions(direction: str, bars: pd.DataFrame, entry: float, stop: float,
               extra_price: Optional[float] = None) -> dict:
    """MFE ≥ 0 و MAE ≤ 0 بالـ R من High/Low الشموع بين الدخول والخروج (+ سعر إضافي اختياري)."""
    risk = (entry - stop) if direction == 'call' else (stop - entry)
    if risk <= 0 or bars is None or len(bars) == 0:
        return {'max_favorable': 0.0, 'max_adverse': 0.0, 'highest_price': None, 'lowest_price': None}
    hi, lo = float(bars['High'].max()), float(bars['Low'].min())
    if extra_price is not None:
        hi, lo = max(hi, extra_price), min(lo, extra_price)
    if direction == 'call':
        mfe, mae = (hi - entry) / risk, (lo - entry) / risk
    else:
        mfe, mae = (entry - lo) / risk, (entry - hi) / risk
    return {'max_favorable': round(max(0.0, mfe), 3), 'max_adverse': round(min(0.0, mae), 3),
            'highest_price': round(hi, 2), 'lowest_price': round(lo, 2)}
