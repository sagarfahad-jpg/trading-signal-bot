from dataclasses import dataclass
from typing import Optional, List, Tuple
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import json
import os
import data_client


# ─── Scalp classification threshold (ATR كنسبة من السعر) ─────────────────────
# تحت العتبة = تقلّب منخفض → scalp (0DTE + بوّابة رفض صلبة تتطلّب تأكيد MTF).
# SCALP_ATR_PCT_OLD = العتبة التاريخية، تُستخدم في shadow logging للمقارنة فقط.
SCALP_ATR_PCT     = 0.007   # 0.7%
SCALP_ATR_PCT_OLD = 0.007   # 0.7% — baseline تاريخي (shadow only)


@dataclass
class SignalResult:
    symbol: str
    direction: str      # 'call' or 'put'
    confidence: str     # 'high' or 'medium'
    score: float
    entry_low: float
    entry_high: float
    stop: float
    target1: float
    target2: float
    entry_type: str
    current_price: float
    is_scalp: bool
    expiry: str
    strike: float
    rr: float           # Risk/Reward ratio
    vix: float          # VIX at signal time
    mtf_score: int      # عدد الأطر الزمنية المؤكِّدة (0-2)
    option_price:  float = 0.0   # سعر العقد (Premium) — bid/ask midpoint
    delta:         float = 0.0   # Delta (0-1)
    iv:            float = 0.0   # Implied Volatility
    theta:         float = 0.0   # Theta (daily decay)
    contracts:     int   = 1     # عدد العقود المقترح (Position Sizing)
    vwap:          float = 0.0   # VWAP لحظة الإشارة
    regime:        str   = ""    # "bull" / "bear" / "neutral"
    # ── HTF Analysis ──────────────────────────────────────────────────────────
    htf_zone_tf:   str   = ""    # '1h' | '4h' | 'daily'
    htf_zone_type: str   = ""    # 'OB' | 'FVG'
    htf_direction: str   = ""    # 'demand' | 'supply'
    cisd:          bool  = False # CISD مؤكَّد على 5m
    displacement:  bool  = False # Displacement Candle على 5m
    # ── SMT ───────────────────────────────────────────────────────────────────
    smt_divergence: bool = False  # SMT divergence detected (^NDX vs ^GSPC)
    smt_direction:  str  = ""     # 'call' | 'put' | ''
    # ── Market Structure Events (BOS / CHoCH / MSS) ──────────────────────────
    structure_event: str = ""     # 'BOS' | 'CHoCH' | 'MSS' | ''
    structure_bias:  str = ""     # 'bullish' | 'bearish' | ''
    event_bars_ago:  int = 0      # كم شمعة مرّت منذ آخر حدث هيكلي
    # ── Premium/Discount Zones (MTF — Daily + 4H + 1H) ───────────────────────
    pd_summary:       str   = ""   # "D: Discount(8%) | 4H: ... | 1H: ..."
    pd_bull_modifier: float = 0.0  # +/- على bs
    pd_bear_modifier: float = 0.0  # +/- على ps
    pd_zone_daily:    str   = ""   # 'Premium' | 'Discount' | 'Equilibrium' | 'Neutral'
    pd_zone_4h:       str   = ""
    pd_zone_1h:       str   = ""
    # ── Strong/Weak Highs & Lows (LuxAlgo SMC #4) ────────────────────────
    strong_high:      float = 0.0
    strong_low:       float = 0.0
    near_strong_high: bool  = False
    near_strong_low:  bool  = False
    # ── HTF Liquidity Levels (LuxAlgo SMC #10+#11) ───────────────────────
    pwh:       float = 0.0
    pwl:       float = 0.0
    pmh:       float = 0.0
    pml:       float = 0.0
    near_pwh:  bool  = False
    near_pwl:  bool  = False
    near_pmh:  bool  = False
    near_pml:  bool  = False
    # ── Equal Highs/Lows (LuxAlgo SMC #3) ───────────────────────────────
    eqh_level: float = 0.0
    eql_level: float = 0.0
    near_eqh:  bool  = False
    near_eql:  bool  = False
    eqh_swept: bool  = False
    eql_swept: bool  = False
    # ── OTE Setup (Optimal Trade Entry — ICT) ────────────────────────────
    ote_active:        bool  = False
    ote_direction:     str   = ""        # 'bullish' | 'bearish' | ''
    ote_leg_start:     float = 0.0
    ote_leg_end:       float = 0.0
    ote_golden_pocket: float = 0.0
    in_ote:            bool  = False
    in_golden_pocket:  bool  = False
    inverse_ote:       bool  = False
    # ── Internal vs Swing Structure (LuxAlgo SMC #5) ─────────────────────
    swing_event:   str = ""   # 'BOS' | 'CHoCH' | 'MSS' | ''
    swing_bias:    str = ""   # 'bullish' | 'bearish' | ''
    alignment:     str = ""   # 'aligned'|'conflicting'|'swing_only'|'internal_only'|'none'
    # ── Options Flow (سياق — للتقييم لاحقاً) ───────────────────────────────────
    max_pain:  float = 0.0
    call_wall: float = 0.0
    put_wall:  float = 0.0
    pcr:       float = 0.0


# ─── Indicators ───────────────────────────────────────────────────────────────

def _vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP — Cumulative(Price × Volume) / Cumulative(Volume)"""
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    return (typical * df['Volume']).cumsum() / df['Volume'].cumsum().replace(0, np.nan)


def _market_regime(df1d: pd.DataFrame) -> str:
    """
    تحديد اتجاه السوق:
    bull   : السعر > SMA200 والـ SMA200 صاعد
    bear   : السعر < SMA200 والـ SMA200 هابط
    neutral: غير محدد
    """
    if len(df1d) < 25:
        return "neutral"
    sma200 = df1d['Close'].rolling(20).mean()
    last   = float(df1d['Close'].iloc[-1])
    s_now  = float(sma200.iloc[-1])
    s_prev = float(sma200.iloc[-5])
    if last > s_now and s_now > s_prev:
        return "bull"
    if last < s_now and s_now < s_prev:
        return "bear"
    return "neutral"


def _rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low']  - prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(com=period - 1, min_periods=period).mean().iloc[-1])


# ─── ICT Concepts ─────────────────────────────────────────────────────────────

# ─── البنية السوقية الموحَّدة (re-export للحفاظ على واردات dashboard.py) ────
from market_structure import (
    _pivot_levels, _find_fvg, _find_order_blocks,
    detect_structure_events, detect_pd_zones_mtf,
    detect_structure_dual, prev_period_levels,
    detect_equal_levels, detect_ote_setup,
)


def _rsi_divergence(df: pd.DataFrame, lookback: int = 30) -> Tuple[bool, bool]:
    """
    RSI Divergence — انعكاس قوي:
    bull_div : السعر قاع أدنى + RSI قاع أعلى  → فرصة CALL
    bear_div : السعر قمة أعلى + RSI قمة أدنى  → فرصة PUT
    """
    if len(df) < lookback:
        return False, False
    recent   = df.tail(lookback)
    prices   = recent['Close']
    rsi_vals = _rsi(prices).dropna()
    if len(rsi_vals) < lookback // 2:
        return False, False

    half = lookback // 2
    p1, p2 = prices.iloc[:half],      prices.iloc[half:]
    r1, r2 = rsi_vals.iloc[:half],    rsi_vals.iloc[half:]

    # Bullish: price → lower low  |  RSI → higher low
    bull = (p2.min() < p1.min() * 0.9995) and (r2.min() > r1.min() * 1.01)
    # Bearish: price → higher high  |  RSI → lower high
    bear = (p2.max() > p1.max() * 1.0005) and (r2.max() < r1.max() * 0.99)
    return bool(bull), bool(bear)


def _liquidity_sweep(df: pd.DataFrame, lookback: int = 40) -> Tuple[bool, bool]:
    """
    Liquidity Sweep (ICT Stop Hunt) — السعر يمسح الـ Stops ثم ينعكس:
    bull_sweep : ذيل الشمعة مسح تحت القيعان ثم أغلق فوقها → CALL
    bear_sweep : ذيل الشمعة مسح فوق القمم ثم أغلق دونها  → PUT
    """
    if len(df) < lookback:
        return False, False

    recent      = df.tail(lookback)
    anchor_end  = int(lookback * 0.70)
    anchor      = recent.iloc[:anchor_end]
    tail        = recent.iloc[anchor_end:]

    anchor_low  = float(anchor['Low'].min())
    anchor_high = float(anchor['High'].max())
    tail_low    = float(tail['Low'].min())
    tail_high   = float(tail['High'].max())
    tail_close  = float(tail['Close'].iloc[-1])

    # ذيل نزل تحت القاع ثم أغلق فوقه
    bull_sweep = (tail_low < anchor_low * 0.9995) and (tail_close > anchor_low)
    # ذيل صعد فوق القمة ثم أغلق دونها
    bear_sweep = (tail_high > anchor_high * 1.0005) and (tail_close < anchor_high)
    return bool(bull_sweep), bool(bear_sweep)


# ─── Filters ──────────────────────────────────────────────────────────────────

def get_vix() -> float:
    """Returns current VIX value (defaults to 20 if unavailable)."""
    try:
        hist = yf.Ticker("^VIX").history(period="1d", interval="5m")
        return float(hist['Close'].iloc[-1]) if not hist.empty else 20.0
    except Exception:
        return 20.0


_earnings_cache: dict = {}

def has_earnings_soon(symbol: str, days: int = 2) -> bool:
    """True if the symbol has earnings within the next N days. Cached 6 hours."""
    import time as _t
    key = (symbol, days)
    entry = _earnings_cache.get(key)
    if entry and _t.time() - entry[1] < 21600:
        return entry[0]

    result = False
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is not None:
            today = datetime.now().date()
            for col in (cal.columns if hasattr(cal, 'columns') else []):
                val = cal[col].get('Earnings Date') if hasattr(cal[col], 'get') else None
                if val is not None:
                    try:
                        delta = (pd.Timestamp(val).date() - today).days
                        if -1 <= delta <= days:
                            result = True
                            break
                    except Exception:
                        pass
    except Exception:
        pass

    _earnings_cache[key] = (result, _t.time())
    return result


def _direction_signals(rsi: float, rsi_p: float, price: float,
                       sma20: float) -> Tuple[Optional[str], Optional[str]]:
    """
    يُرجع (old, new) لاتجاه فريم واحد — للمقارنة في shadow logging.

    old : منطق mean-reversion القديم (يشترط RSI على الجهة المعاكسة من 50).
          يُحتفظ به للتسجيل فقط — لا يؤثّر على القرار.
    new : منطق الاستمرار الجديد (B) — القرار الفعلي:
          توافق الترند (price مقابل sma20) + زخم RSI صاعد/هابط،
          مع تجنّب التطرّف (CALL: rsi < 70 | PUT: rsi > 30).
    """
    old = ('call' if (rsi < 50 and rsi > rsi_p and price > sma20)
           else 'put' if (rsi > 50 and rsi < rsi_p and price < sma20)
           else None)
    new = ('call' if (price > sma20 and rsi > rsi_p and rsi < 70)
           else 'put' if (price < sma20 and rsi < rsi_p and rsi > 30)
           else None)
    return old, new


def _quick_direction(symbol: str, interval: str, period: str,
                     shadow: Optional[dict] = None) -> Optional[str]:
    """
    Fast direction check on a single timeframe. Returns 'call', 'put', or None.

    القرار الفعلي = المنطق الجديد (B). لو مُرِّر shadow، يُخزَّن اتجاه المنطق
    القديم في shadow[interval] للمقارنة فقط — دون أي أثر على القيمة المُعادة.
    """
    try:
        df = data_client.get_bars(symbol, interval, period)
        if df.empty or len(df) < 30:
            return None
        price      = float(df['Close'].iloc[-1])
        rsi_series = _rsi(df['Close'])
        rsi        = float(rsi_series.iloc[-1])
        rsi_p      = float(rsi_series.iloc[-4])
        sma20      = float(df['Close'].rolling(20).mean().iloc[-1])
        old, new   = _direction_signals(rsi, rsi_p, price, sma20)
        if shadow is not None:
            shadow[interval] = {'old': old, 'new': new}
        return new
    except Exception:
        return None


# ─── Options contract ─────────────────────────────────────────────────────────

def _get_contract(symbol: str, direction: str, price: float, is_scalp: bool = False, score: float = 0.0) -> Tuple[str, float, float, float, float, float]:
    """
    Returns (expiry, strike, option_price, delta, iv, theta).
    - Scalp  → 0DTE
    - عادي   → أقرب جمعة قادمة
    """
    et        = pytz.timezone('America/New_York')
    today     = datetime.now(et).date()
    today_str = today.strftime('%Y-%m-%d')

    try:
        ticker      = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return today_str.replace('-', ''), float(round(price)), 0.0, 0.0, 0.0, 0.0

        available = [e for e in expirations if e >= today_str]
        if not available:
            available = list(expirations)

        if is_scalp:
            expiry = available[0]   # 0DTE ⚡
        else:
            # انتهاء أقصر = Premium أرخص (Theta أقل) — مناسب للمضاربة
            min_days = 10 if score >= 7.5 else 5
            target_str = (today + timedelta(days=min_days)).strftime('%Y-%m-%d')
            candidates = [e for e in available if e >= target_str]
            expiry     = candidates[0] if candidates else available[0]

        # قيم افتراضية (yfinance) — قد تُستبدل بأسعار Alpaca الأدق
        strike       = float(round(price))
        option_price = 0.0
        delta = iv = theta = 0.0

        try:
            chain = ticker.option_chain(expiry)
            opts  = chain.calls if direction == 'call' else chain.puts
            if not opts.empty:
                opts         = opts.copy()
                opts['dist'] = (opts['strike'] - price).abs()
                row          = opts.nsmallest(1, 'dist').iloc[0]
                strike       = float(row['strike'])
                bid  = float(row.get('bid',       0) or 0)
                ask  = float(row.get('ask',       0) or 0)
                last = float(row.get('lastPrice', 0) or 0)
                if bid > 0 and ask > 0:
                    option_price = round((bid + ask) / 2, 2)
                elif last > 0:
                    option_price = round(last, 2)
                delta = round(float(row.get('delta', 0) or 0), 3)
                iv    = round(float(row.get('impliedVolatility', 0) or 0) * 100, 1)
                theta = round(float(row.get('theta', 0) or 0), 3)
        except Exception:
            pass

        # ── أولوية Alpaca للسعر والـ strike (أدق وأحدث من yfinance) ──────────
        try:
            alp = data_client.get_option_alpaca(symbol, direction, price, expiry)
            if alp and alp.get("option_price", 0) > 0:
                strike       = alp["strike"]
                option_price = alp["option_price"]
        except Exception:
            pass

        return expiry.replace('-', ''), strike, option_price, delta, iv, theta

    except Exception:
        return today_str.replace('-', ''), float(round(price)), 0.0, 0.0, 0.0, 0.0


# ─── SMT (Smart Money Technique) ─────────────────────────────────────────────

_smt_cache: dict  = {"ts": 0.0, "direction": ""}
_perf_cache: dict = {"ts": 0.0, "adj": {}}


def _get_perf_adj(entry_type: str) -> float:
    """
    تعديل السكور بناءً على أداء نوع الدخول التاريخي (من Supabase).
    يُحدَّث كل 24 ساعة.
    WR ≥ 65% → +0.8   |  50-65% → +0.3
    40-50%   → 0.0    |  < 40%  → -1.0
    يُطبَّق فقط لو فيه ≥ 5 إشارات محسومة.
    """
    import time as _t
    now = _t.time()
    if now - float(_perf_cache["ts"]) < 86400:
        return float(_perf_cache["adj"].get(entry_type, 0.0))

    try:
        import db
        signals = db.get_all_signals(limit=500)
        stats: dict = {}
        for s in signals:
            et     = s.get("entry_type", "")
            status = s.get("status", "")
            if not et or status in ("open", "expired"):
                continue
            if et not in stats:
                stats[et] = {"w": 0, "n": 0}
            stats[et]["n"] += 1
            if status in ("hit_t1", "hit_t2"):
                stats[et]["w"] += 1

        adj: dict = {}
        for et, d in stats.items():
            if d["n"] < 5:
                continue
            wr = d["w"] / d["n"]
            if   wr >= 0.65: adj[et] =  0.8
            elif wr >= 0.50: adj[et] =  0.3
            elif wr >= 0.40: adj[et] =  0.0
            else:            adj[et] = -1.0

        _perf_cache["ts"]  = now
        _perf_cache["adj"] = adj
    except Exception:
        pass

    return float(_perf_cache["adj"].get(entry_type, 0.0))


def _compute_smt(df_nas: pd.DataFrame, df_spx: pd.DataFrame) -> str:
    """
    Compares the last 2 pivot highs/lows on 1H for NAS100 (^NDX) vs SPX500 (^GSPC).
    Bullish SMT : one makes a new lower low while the other does not  → 'call'
    Bearish SMT : one makes a new higher high while the other does not → 'put'
    Minimum divergence: 0.3%.
    """
    MIN_PCT = 0.003
    LB      = 5

    nas_highs, nas_lows = _pivot_levels(df_nas, LB)
    spx_highs, spx_lows = _pivot_levels(df_spx, LB)

    # Bullish SMT — new-low divergence
    if len(nas_lows) >= 2 and len(spx_lows) >= 2:
        n1, n0 = nas_lows[-1], nas_lows[-2]
        s1, s0 = spx_lows[-1], spx_lows[-2]
        if n0 > 0 and s0 > 0 and ((n1 < n0) != (s1 < s0)):
            move = max(abs(n1 - n0) / n0, abs(s1 - s0) / s0)
            if move >= MIN_PCT:
                return 'call'

    # Bearish SMT — new-high divergence
    if len(nas_highs) >= 2 and len(spx_highs) >= 2:
        n1, n0 = nas_highs[-1], nas_highs[-2]
        s1, s0 = spx_highs[-1], spx_highs[-2]
        if n0 > 0 and s0 > 0 and ((n1 > n0) != (s1 > s0)):
            move = max(abs(n1 - n0) / n0, abs(s1 - s0) / s0)
            if move >= MIN_PCT:
                return 'put'

    return ''


def _get_smt_direction() -> str:
    """Returns 'call', 'put', or '' — result cached for 15 minutes."""
    import time as _t
    now = _t.time()
    if now - float(_smt_cache["ts"]) < 900:
        return str(_smt_cache["direction"])

    result = ""
    try:
        nas = yf.Ticker("^NDX").history(period="14d", interval="1h")
        spx = yf.Ticker("^GSPC").history(period="14d", interval="1h")
        if not nas.empty and not spx.empty and len(nas) >= 15 and len(spx) >= 15:
            result = _compute_smt(nas, spx)
    except Exception:
        pass

    _smt_cache["ts"]        = now
    _smt_cache["direction"] = result
    return result


# ─── Quick scan (للـ Dashboard فقط — بدون options chain أو MTF) ───────────────

def quick_scan(symbol: str) -> Optional[dict]:
    """
    تحليل سريع للـ Dashboard: يُرجع التقييم والاتجاه بدون جلب Options أو MTF.
    """
    try:
        df5  = data_client.get_bars(symbol, '5m', '2d')
        df1d = data_client.get_bars(symbol, '1d', '90d')
        if df5.empty or len(df5) < 40 or df1d.empty or len(df1d) < 15:
            return None

        price = float(df5['Close'].iloc[-1])
        atr   = _atr(df5)

        rsi_s    = _rsi(df5['Close'])
        rsi      = float(rsi_s.iloc[-1])
        rsi_prev = float(rsi_s.iloc[-4])
        rsi_rising = rsi > rsi_prev

        sma10   = float(df5['Close'].rolling(10).mean().iloc[-1])
        sma30   = float(df5['Close'].rolling(30).mean().iloc[-1])
        sma200d = float(df1d['Close'].rolling(20).mean().iloc[-1])

        vol_avg    = float(df5['Volume'].rolling(20).mean().iloc[-1])
        vol_recent = float(df5['Volume'].iloc[-5:].mean())
        vol_surge  = vol_recent > vol_avg * 1.5
        last_bull  = float(df5['Close'].iloc[-1]) > float(df5['Open'].iloc[-1])

        pdh = float(df1d['High'].iloc[-2])
        pdl = float(df1d['Low'].iloc[-2])

        # ── HTF Liquidity Levels (PWH/PWL/PMH/PML) — LuxAlgo SMC #10+#11 ──
        htf_lvls = prev_period_levels(df1d)
        pwh = htf_lvls['pwh']
        pwl = htf_lvls['pwl']
        pmh = htf_lvls['pmh']
        pml = htf_lvls['pml']

        recent = df5.tail(100)
        pivot_highs, pivot_lows = _pivot_levels(recent)
        fvgs = _find_fvg(recent)
        obs  = _find_order_blocks(recent, atr_period=50)

        supports    = sorted([l for l in pivot_lows  if l < price], reverse=True)
        resistances = sorted([h for h in pivot_highs if h > price])
        near_sup = supports[0]    if supports    else price * 0.998
        near_res = resistances[0] if resistances else price * 1.002

        at_sup   = abs(price - near_sup) / price < 0.004
        at_res   = abs(price - near_res) / price < 0.004
        near_pdl = abs(price - pdl)     / price < 0.005
        near_pdh = abs(price - pdh)     / price < 0.005

        HTF_TOL = 0.006   # 0.6% — أوسع قليلاً من PDH لطبيعة HTF
        near_pwh = pwh is not None and abs(price - pwh) / price < HTF_TOL
        near_pwl = pwl is not None and abs(price - pwl) / price < HTF_TOL
        near_pmh = pmh is not None and abs(price - pmh) / price < HTF_TOL
        near_pml = pml is not None and abs(price - pml) / price < HTF_TOL

        # OB نشطة (لم تُخترق)
        bull_ob = any(lo <= price <= hi * 1.003 for lo, hi, t in obs if t == 'bullish')
        bear_ob = any(lo * 0.997 <= price <= hi  for lo, hi, t in obs if t == 'bearish')

        # Breakers (OBs مخترقة + السعر قرب المستوى المعكوس)
        PROX_BREAK = 0.007
        bull_break = any(
            abs(price - hi) / price < PROX_BREAK and price > hi
            for lo, hi, t in obs if t == 'breaker_bull'
        )
        bear_break = any(
            abs(price - lo) / price < PROX_BREAK and price < lo
            for lo, hi, t in obs if t == 'breaker_bear'
        )

        # FVG نشطة (لم تُملأ)
        bull_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bullish')
        bear_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bearish')

        # Inversion FVGs (مملوءة + معكوسة)
        inv_demand = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'demand')
        inv_supply = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'supply')

        bull_div,   bear_div   = _rsi_divergence(df5)
        bull_sweep, bear_sweep = _liquidity_sweep(df5)

        bs = ps = 0.0
        if rsi < 30: bs += 3.0
        elif rsi < 40 and rsi_rising: bs += 2.0
        elif rsi < 50 and rsi_rising: bs += 1.0
        if rsi > 70: ps += 3.0
        elif rsi > 60 and not rsi_rising: ps += 2.0
        elif rsi > 50 and not rsi_rising: ps += 1.0
        if at_sup or near_pdl:   bs += 2.5
        if at_res or near_pdh:   ps += 2.5
        # ── HTF Liquidity scoring (sensitivity hierarchy: PDH=2.5 → PWH=3.0 → PMH=3.5) ──
        if near_pwl: bs += 3.0
        if near_pwh: ps += 3.0
        if near_pml: bs += 3.5
        if near_pmh: ps += 3.5

        # ── Equal Highs/Lows (EQH/EQL) — LuxAlgo SMC #3 + Sweep State ────
        eq_levels = detect_equal_levels(df5, pivot_size=3,
                                        threshold_atr_mult=0.1,
                                        atr_period=50,
                                        track_sweep=True)
        eqh_level = eq_levels['eqh_level']
        eql_level = eq_levels['eql_level']
        eqh_swept = eq_levels['eqh_swept']
        eql_swept = eq_levels['eql_swept']

        EQ_NEAR_TOL = 0.003   # 0.3%
        near_eqh = eqh_level is not None and abs(price - eqh_level) / price < EQ_NEAR_TOL
        near_eql = eql_level is not None and abs(price - eql_level) / price < EQ_NEAR_TOL

        # Scoring — يميّز بين pending (سيولة كامنة) و swept (مستوى منقلب)
        if near_eqh:
            if eqh_swept:
                bs += 1.5   # EQH مكسورة للأعلى → دعم الآن على إعادة الاختبار
            else:
                ps += 2.0   # EQH كامنة → هدف sweep هابط

        if near_eql:
            if eql_swept:
                ps += 1.5   # EQL مكسورة للأسفل → مقاومة الآن على إعادة الاختبار
            else:
                bs += 2.0   # EQL كامنة → هدف sweep صاعد
        if bull_fvg:   bs += 1.5
        if bear_fvg:   ps += 1.5
        if bull_ob:    bs += 2.0
        if bear_ob:    ps += 2.0
        if bull_div:   bs += 2.5
        if bear_div:   ps += 2.5
        if bull_break: bs += 1.5
        if bear_break: ps += 1.5
        # Inversion FVG bonus (أقل من OB العادي +2.0 لأنها مستوى ثانوي)
        if inv_demand: bs += 1.0
        if inv_supply: ps += 1.0
        if bull_sweep: bs += 1.5
        if bear_sweep: ps += 1.5
        if price > sma10 and price > sma30: bs += 0.5
        if price < sma10 and price < sma30: ps += 0.5
        if not np.isnan(sma200d):
            if price > sma200d: bs += 0.5
            else:               ps += 0.5
        if vol_surge and last_bull:       bs += 1.0
        elif vol_surge and not last_bull: ps += 1.0

        direction = 'call' if bs >= ps else 'put'
        score     = bs if direction == 'call' else ps

        if direction == 'call':
            base       = near_sup if at_sup else (price - atr * 0.2)
            entry_low  = round(base, 2)
            entry_high = round(base + atr * 0.35, 2)
            stop       = round(entry_low - atr * 0.5, 2)
            min_t1     = entry_high + atr * 0.5
            target1    = round(near_res if (resistances and near_res > min_t1) else min_t1, 2)
            target2    = round(target1 + atr * 0.6, 2)
        else:
            base       = near_res if at_res else (price + atr * 0.2)
            entry_high = round(base, 2)
            entry_low  = round(base - atr * 0.35, 2)
            stop       = round(entry_high + atr * 0.5, 2)
            max_t1     = entry_low - atr * 0.5
            target1    = round(near_sup if (supports and near_sup < max_t1) else max_t1, 2)
            target2    = round(target1 - atr * 0.6, 2)

        # حد أدنى لمسافة الوقف (مطابق لمنطق analyze الفعلي)
        MIN_STOP_DIST = max(atr * 0.5, 0.50)
        if direction == 'call':
            stop_cap = round(entry_low - MIN_STOP_DIST, 2)
            if stop > stop_cap:
                stop = stop_cap
        else:
            stop_cap = round(entry_high + MIN_STOP_DIST, 2)
            if stop < stop_cap:
                stop = stop_cap

        # سقف الهدف (R:R ≤ 4.0) — مطابق لمنطق البوت الفعلي analyze()
        MAX_RR = 4.0
        entry_mid = (entry_low + entry_high) / 2
        if direction == 'call' and entry_mid > stop:
            cap_t1 = round(entry_mid + (entry_mid - stop) * MAX_RR, 2)
            if target1 > cap_t1:
                target1 = cap_t1
                target2 = round(target1 + atr * 0.6, 2)
        elif direction == 'put' and stop > entry_mid:
            cap_t1 = round(entry_mid - (stop - entry_mid) * MAX_RR, 2)
            if target1 < cap_t1:
                target1 = cap_t1
                target2 = round(target1 - atr * 0.6, 2)

        if direction == 'call':
            rr = (target1 - entry_mid) / (entry_mid - stop) if entry_mid > stop else 0.0
        else:
            rr = (entry_mid - target1) / (stop - entry_mid) if stop > entry_mid else 0.0

        return {
            "symbol"    : symbol,
            "direction" : direction,
            "score"     : round(score, 1),
            "rsi"       : round(rsi, 1),
            "price"     : price,
            "entry_low" : entry_low,
            "entry_high": entry_high,
            "stop"      : stop,
            "target1"   : target1,
            "target2"   : target2,
            "rr"        : round(rr, 2),
            "at_sup"    : at_sup,
            "at_res"    : at_res,
            "bull_ob"   : bull_ob,
            "bear_ob"   : bear_ob,
            "bull_fvg"  : bull_fvg,
            "bear_fvg"  : bear_fvg,
            "bull_div"  : bull_div,
            "bear_div"  : bear_div,
            "bull_break": bull_break,
            "bear_break": bear_break,
            "bull_sweep": bull_sweep,
            "bear_sweep": bear_sweep,
            "regime"    : _market_regime(df1d),
        }
    except Exception as e:
        return None


# ─── Shadow logging (مقارنة منطق القرار القديم مقابل الجديد) ─────────────────
_SHADOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shadow_log.jsonl")


def _shadow_record(symbol, direction, shadow_tf, mtf_new, mtf_old,
                   scalp_new, scalp_old, score_new, score_old, eff_min,
                   gate_old, gate_new, pass_old, pass_new, rsi, atr_pct):
    """
    يسجّل مقارنة «القديم مقابل الجديد» — يُستدعى عند اختلاف القرار فقط
    (gate أو pass). جانبي بحت: لا يؤثّر على القرار الفعلي إطلاقاً.
    """
    print(f"  [shadow] {symbol} {direction} | mtf {mtf_old}->{mtf_new} | "
          f"scalp {scalp_old}->{scalp_new} | score {score_old:.1f}->{score_new:.1f} "
          f"(min {eff_min:.1f}) | pass {pass_old}->{pass_new}")
    rec = {
        "ts": datetime.now(pytz.timezone('America/New_York')).isoformat(),
        "symbol": symbol, "direction": direction, "tf": shadow_tf,
        "mtf_old": mtf_old, "mtf_new": mtf_new,
        "scalp_old": scalp_old, "scalp_new": scalp_new,
        "score_old": round(score_old, 2), "score_new": round(score_new, 2),
        "eff_min": round(eff_min, 2),
        "gate_old": gate_old, "gate_new": gate_new,
        "pass_old": pass_old, "pass_new": pass_new,
        "rsi": round(rsi, 1), "atr_pct": round(atr_pct, 3),
    }
    try:
        with open(_SHADOW_PATH, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [shadow] ⚠️ تعذّر كتابة shadow_log.jsonl: {e}")


# ─── Main analysis ────────────────────────────────────────────────────────────

def analyze(
    symbol: str,
    min_score: float = 5.5,
    high_confidence_threshold: float = 7.5,
    min_rr: float = 1.5,
    vix_value: Optional[float] = None,
    require_mtf: bool = True,
) -> Optional[SignalResult]:

    try:
        df5  = data_client.get_bars(symbol, '5m', '3d')
        df1d = data_client.get_bars(symbol, '1d', '90d')
        df1h = data_client.get_bars(symbol, '1h', '14d')
        df4h = data_client.get_bars(symbol, '4h', '30d')

        if df5.empty or len(df5) < 60 or df1d.empty or len(df1d) < 20:
            return None

        price = float(df5['Close'].iloc[-1])
        atr   = _atr(df5)

        # ── فلتر Earnings ─────────────────────────────────────────────────────
        if has_earnings_soon(symbol):
            return None

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi_s    = _rsi(df5['Close'])
        rsi      = float(rsi_s.iloc[-1])
        rsi_prev = float(rsi_s.iloc[-4])
        rsi_rising = rsi > rsi_prev

        # ── Moving Averages ───────────────────────────────────────────────────
        sma10   = float(df5['Close'].rolling(10).mean().iloc[-1])
        sma30   = float(df5['Close'].rolling(30).mean().iloc[-1])
        sma200d = float(df1d['Close'].rolling(20).mean().iloc[-1])

        # ── Volume ────────────────────────────────────────────────────────────
        vol_avg    = float(df5['Volume'].rolling(20).mean().iloc[-1])
        vol_recent = float(df5['Volume'].iloc[-5:].mean())
        vol_surge  = vol_recent > vol_avg * 1.5
        last_bull  = float(df5['Close'].iloc[-1]) > float(df5['Open'].iloc[-1])

        # ── Previous Day High/Low (ICT) ───────────────────────────────────────
        pdh = float(df1d['High'].iloc[-2])
        pdl = float(df1d['Low'].iloc[-2])

        # ── HTF Liquidity Levels (PWH/PWL/PMH/PML) — LuxAlgo SMC #10+#11 ──
        htf_lvls = prev_period_levels(df1d)
        pwh = htf_lvls['pwh']
        pwl = htf_lvls['pwl']
        pmh = htf_lvls['pmh']
        pml = htf_lvls['pml']

        # ── ICT Structures ────────────────────────────────────────────────────
        recent = df5.tail(120)
        pivot_highs, pivot_lows = _pivot_levels(recent)
        fvgs = _find_fvg(recent)
        obs  = _find_order_blocks(recent, atr_period=50)

        supports    = sorted([l for l in pivot_lows  if l < price], reverse=True)
        resistances = sorted([h for h in pivot_highs if h > price])
        near_sup = supports[0]    if supports    else price * 0.998
        near_res = resistances[0] if resistances else price * 1.002

        at_sup   = abs(price - near_sup) / price < 0.004
        at_res   = abs(price - near_res) / price < 0.004
        near_pdl = abs(price - pdl)     / price < 0.005
        near_pdh = abs(price - pdh)     / price < 0.005

        HTF_TOL = 0.006   # 0.6% — أوسع قليلاً من PDH لطبيعة HTF
        near_pwh = pwh is not None and abs(price - pwh) / price < HTF_TOL
        near_pwl = pwl is not None and abs(price - pwl) / price < HTF_TOL
        near_pmh = pmh is not None and abs(price - pmh) / price < HTF_TOL
        near_pml = pml is not None and abs(price - pml) / price < HTF_TOL

        # OB نشطة (لم تُخترق)
        bull_ob = any(lo <= price <= hi * 1.003 for lo, hi, t in obs if t == 'bullish')
        bear_ob = any(lo * 0.997 <= price <= hi  for lo, hi, t in obs if t == 'bearish')

        # Breakers (OBs مخترقة + السعر قرب المستوى المعكوس)
        PROX_BREAK = 0.007
        bull_break = any(
            abs(price - hi) / price < PROX_BREAK and price > hi
            for lo, hi, t in obs if t == 'breaker_bull'
        )
        bear_break = any(
            abs(price - lo) / price < PROX_BREAK and price < lo
            for lo, hi, t in obs if t == 'breaker_bear'
        )

        # FVG نشطة (لم تُملأ)
        bull_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bullish')
        bear_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bearish')

        # Inversion FVGs (مملوءة + معكوسة)
        inv_demand = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'demand')
        inv_supply = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'supply')

        # ── مفاهيم ICT المتقدمة ───────────────────────────────────────────────
        bull_div,   bear_div   = _rsi_divergence(df5)
        bull_sweep, bear_sweep = _liquidity_sweep(df5)

        # ── VWAP ──────────────────────────────────────────────────────────────
        vwap_val    = float(_vwap(df5).iloc[-1])
        above_vwap  = price > vwap_val

        # ── Market Regime ─────────────────────────────────────────────────────
        regime = _market_regime(df1d)

        # ── Scoring ───────────────────────────────────────────────────────────
        bs = ps = 0.0

        if rsi < 30:              bs += 3.0
        elif rsi < 40 and rsi_rising: bs += 2.0
        elif rsi < 50 and rsi_rising: bs += 1.0

        if rsi > 70:              ps += 3.0
        elif rsi > 60 and not rsi_rising: ps += 2.0
        elif rsi > 50 and not rsi_rising: ps += 1.0

        if at_sup or near_pdl:   bs += 2.5
        if at_res or near_pdh:   ps += 2.5
        # ── HTF Liquidity scoring (sensitivity hierarchy: PDH=2.5 → PWH=3.0 → PMH=3.5) ──
        if near_pwl: bs += 3.0
        if near_pwh: ps += 3.0
        if near_pml: bs += 3.5
        if near_pmh: ps += 3.5

        # ── Equal Highs/Lows (EQH/EQL) — LuxAlgo SMC #3 + Sweep State ────
        eq_levels = detect_equal_levels(df5, pivot_size=3,
                                        threshold_atr_mult=0.1,
                                        atr_period=50,
                                        track_sweep=True)
        eqh_level = eq_levels['eqh_level']
        eql_level = eq_levels['eql_level']
        eqh_swept = eq_levels['eqh_swept']
        eql_swept = eq_levels['eql_swept']

        EQ_NEAR_TOL = 0.003   # 0.3%
        near_eqh = eqh_level is not None and abs(price - eqh_level) / price < EQ_NEAR_TOL
        near_eql = eql_level is not None and abs(price - eql_level) / price < EQ_NEAR_TOL

        # Scoring — يميّز بين pending (سيولة كامنة) و swept (مستوى منقلب)
        if near_eqh:
            if eqh_swept:
                bs += 1.5   # EQH مكسورة للأعلى → دعم الآن على إعادة الاختبار
            else:
                ps += 2.0   # EQH كامنة → هدف sweep هابط

        if near_eql:
            if eql_swept:
                ps += 1.5   # EQL مكسورة للأسفل → مقاومة الآن على إعادة الاختبار
            else:
                bs += 2.0   # EQL كامنة → هدف sweep صاعد

        if bull_fvg:   bs += 1.5
        if bear_fvg:   ps += 1.5
        if bull_ob:    bs += 2.0
        if bear_ob:    ps += 2.0

        # RSI Divergence — إشارة انعكاس قوية
        if bull_div:   bs += 2.5
        if bear_div:   ps += 2.5

        # Breaker Block — مستوى ICT مقلوب
        if bull_break: bs += 1.5
        if bear_break: ps += 1.5

        # Inversion FVG bonus (أقل من OB العادي +2.0 لأنها مستوى ثانوي)
        if inv_demand: bs += 1.0
        if inv_supply: ps += 1.0

        # Liquidity Sweep — مسح الـ Stops ثم انعكاس
        if bull_sweep: bs += 1.5
        if bear_sweep: ps += 1.5

        if price > sma10 and price > sma30: bs += 0.5
        if price < sma10 and price < sma30: ps += 0.5
        if not np.isnan(sma200d):
            if price > sma200d: bs += 0.5
            else:               ps += 0.5

        if vol_surge and last_bull:      bs += 1.0
        elif vol_surge and not last_bull: ps += 1.0

        # ── Premium/Discount Zones (MTF: Daily + 4H + 1H) ────────────────────
        pd_zones = detect_pd_zones_mtf(df1h, df4h, df1d)
        bs += pd_zones['bull_modifier']
        ps += pd_zones['bear_modifier']

        # ── Structure Events (Internal + Swing) — LuxAlgo SMC #5 ────────────
        ms_dual = detect_structure_dual(
            df5,
            swing_size=15,
            internal_size=5,
            confluence_filter=True,
            confluence_tolerance=0.001,
        )
        ms        = ms_dual['internal']         # توافق مع الكود السفلي
        swing     = ms_dual['swing']
        alignment = ms_dual['alignment']

        _ms_event    = ms.get('last_event')
        _ms_bias     = ms.get('current_bias')
        _swing_event = swing.get('last_event')
        _swing_bias  = swing.get('current_bias')

        # ── Internal scoring (نفس الأوزان السابقة — لا تغيير) ─────────────
        if _ms_event == 'MSS':
            if _ms_bias == 'bullish':   bs += 1.5
            elif _ms_bias == 'bearish': ps += 1.5
        elif _ms_event == 'BOS':
            if _ms_bias == 'bullish':   bs += 1.0
            elif _ms_bias == 'bearish': ps += 1.0
        elif _ms_event == 'CHoCH':
            if _ms_bias == 'bullish':   bs += 0.5
            elif _ms_bias == 'bearish': ps += 0.5

        # عقوبة الإشارة ضد البنية الداخلية (نفس -0.8 السابقة)
        if _ms_bias == 'bearish':   bs -= 0.8
        elif _ms_bias == 'bullish': ps -= 0.8

        # ✨ Swing scoring (1.5× internal weights — أحداث أنادر وأقوى)
        if _swing_event == 'MSS':
            if _swing_bias == 'bullish':   bs += 2.5
            elif _swing_bias == 'bearish': ps += 2.5
        elif _swing_event == 'BOS':
            if _swing_bias == 'bullish':   bs += 2.0
            elif _swing_bias == 'bearish': ps += 2.0
        elif _swing_event == 'CHoCH':
            if _swing_bias == 'bullish':   bs += 1.0
            elif _swing_bias == 'bearish': ps += 1.0

        # ✨ Confluence bonus عند توافق الطبقتين
        if alignment == 'aligned':
            if _swing_bias == 'bullish':   bs += 1.0
            elif _swing_bias == 'bearish': ps += 1.0
        # conflicting/swing_only/internal_only/none → لا bonus ولا عقوبة إضافية

        # ✨ عقوبة swing معاكسة (أشد من internal لأن swing أقوى)
        if _swing_bias == 'bearish':   bs -= 1.2
        elif _swing_bias == 'bullish': ps -= 1.2

        # ── OTE Setup (Optimal Trade Entry — ICT) ──────────────────────────
        ote = detect_ote_setup(df5, ms_dual,
                               min_leg_atr_mult=2.0,
                               atr_period=50,
                               golden_tolerance=0.005)

        ote_active    = ote['has_setup']
        ote_direction = ote['direction'] or ''
        in_ote        = ote['in_ote']
        in_golden     = ote['in_golden']
        in_inverse    = ote['in_inverse']

        # Scoring — Active OTE
        if ote_active:
            if ote_direction == 'bullish':
                if in_golden:
                    bs += 2.5   # Golden Pocket (sweet spot)
                elif in_ote:
                    bs += 1.5   # OTE zone
                elif in_inverse:
                    ps += 2.0   # Inverse OTE → reversal setup للـ PUT
            elif ote_direction == 'bearish':
                if in_golden:
                    ps += 2.5
                elif in_ote:
                    ps += 1.5
                elif in_inverse:
                    bs += 2.0   # Inverse OTE → reversal setup للـ CALL

        # ── Strong/Weak Highs & Lows (LuxAlgo SMC #4) ────────────────────
        strength = ms.get('strength', {})
        strong_h = strength.get('strong_high')
        weak_h   = strength.get('weak_high')
        strong_l = strength.get('strong_low')
        weak_l   = strength.get('weak_low')

        NEAR_SW_TOL = 0.004   # 0.4%

        near_strong_high = bool(strong_h) and abs(price - strong_h) / price < NEAR_SW_TOL
        near_strong_low  = bool(strong_l) and abs(price - strong_l) / price < NEAR_SW_TOL
        near_weak_high   = bool(weak_h)   and abs(price - weak_h)   / price < NEAR_SW_TOL
        near_weak_low    = bool(weak_l)   and abs(price - weak_l)   / price < NEAR_SW_TOL

        # Scoring قياسي
        if near_strong_high:   ps += 2.0
        elif near_weak_high:   ps += 0.5
        if near_strong_low:    bs += 2.0
        elif near_weak_low:    bs += 0.5

        # عقوبة الإشارة المعاكسة (Counter-trend warning)
        # CALL أمام Strong High = خطر | PUT أمام Strong Low = خطر
        if near_strong_high: bs -= 1.0
        if near_strong_low:  ps -= 1.0

        # ── VWAP ──────────────────────────────────────────────────────────────
        if above_vwap:  bs += 1.0
        else:           ps += 1.0

        # ── Market Regime ─────────────────────────────────────────────────────
        if regime == "bull":    bs += 0.5
        elif regime == "bear":  ps += 0.5

        # ── HTF Zone Analysis ─────────────────────────────────────────────────
        from htf_zones import (get_htf_analysis, price_in_zone, nearest_zone,
                                cisd_5m, displacement_5m, fvg_confirms_zone,
                                inversion_fvg_confirms_zone)

        htf          = get_htf_analysis(symbol, df1h, df4h, df1d)
        _direction_p = 'call' if bs >= ps else 'put'  # الاتجاه المؤقت للبحث عن المنطقة

        active_zone = price_in_zone(price, htf['zones'])
        if active_zone is None:
            active_zone = nearest_zone(price, htf['zones'], _direction_p)

        htf_zone_tf   = ""
        htf_zone_type = ""
        htf_direction = ""
        is_cisd       = False
        is_displace   = False

        if active_zone:
            htf_zone_tf   = active_zone.timeframe
            htf_zone_type = active_zone.zone_type.upper()
            htf_direction = active_zone.direction
            zone_aligns   = (
                (_direction_p == 'call' and active_zone.direction == 'demand') or
                (_direction_p == 'put'  and active_zone.direction == 'supply')
            )

            if zone_aligns:
                # مكافأة الفريم: 1h=+1.0، 4h=+2.0، daily=+3.0
                zone_bonus = active_zone.strength
                # تأكيدات الـ 5m
                bull_c, bear_c = cisd_5m(df5)
                is_cisd     = (_direction_p == 'call' and bull_c) or (_direction_p == 'put' and bear_c)
                is_inv_fvg  = inversion_fvg_confirms_zone(df5, active_zone, _direction_p)
                is_displace = displacement_5m(df5, _direction_p, atr)
                fvg_conf    = fvg_confirms_zone(df5, active_zone, _direction_p)

                if   is_cisd:     confirm_bonus = 4.0   # الأقوى — تحوّل هيكلي
                elif is_inv_fvg:  confirm_bonus = 3.5   # فجوة منقلبة داخل المنطقة
                elif is_displace: confirm_bonus = 3.0
                elif fvg_conf:    confirm_bonus = 2.5
                else:             confirm_bonus = 1.0   # في المنطقة، انتظار تأكيد

                if _direction_p == 'call':
                    bs += zone_bonus + confirm_bonus
                else:
                    ps += zone_bonus + confirm_bonus
            else:
                # الإشارة تعارض اتجاه المنطقة → عقوبة
                if _direction_p == 'call': bs -= 2.0
                else:                      ps -= 2.0

        # مكافأة توافق البنية HTF
        for tf_bias in htf.get('structure', {}).values():
            if _direction_p == 'call' and tf_bias == 'bullish':   bs += 0.3
            elif _direction_p == 'put' and tf_bias == 'bearish':  ps += 0.3

        # ── SMT Divergence (NAS100 vs SPX500 — 1H) ───────────────────────────
        _smt_dir = _get_smt_direction()
        if _smt_dir:
            if _smt_dir == _direction_p:    # يؤكد الاتجاه
                if _direction_p == 'call':  bs += 2.0
                else:                       ps += 2.0
            else:                           # يعارض الاتجاه
                if _direction_p == 'call':  bs -= 2.0
                else:                       ps -= 2.0

        # ── فلتر VIX ─────────────────────────────────────────────────────────
        vix = vix_value if vix_value is not None else 20.0
        effective_min = min_score + (1.0 if vix > 25 else 0.0) + (1.5 if vix > 32 else 0.0)

        # ── فلتر الجلسة + اليوم (توقيت ET) ────────────────────────────────────
        # ذروة:  10:00 ص – 2:00 م  → بدون عقوبة
        # افتتاح: 9:35 – 10:00 ص  → +1.5 (تقلبات عالية)
        # إغلاق: 2:00 – 3:45 م   → +0.5
        # افتتاح الاثنين 9:35–10:30 → +1.0 إضافية (اتجاه غير واضح بعد العطلة)
        try:
            _et_now  = datetime.now(pytz.timezone('America/New_York'))
            _et_mins = _et_now.hour * 60 + _et_now.minute
            if _et_mins < 10 * 60:          # قبل 10:00 ص
                effective_min += 1.5
            elif _et_mins > 14 * 60:        # بعد 2:00 م
                effective_min += 0.5
            # تشديد إضافي لافتتاح الاثنين (weekday 0)
            if _et_now.weekday() == 0 and _et_mins < (10 * 60 + 30):
                effective_min += 1.0
            # تشديد الجمعة بعد الظهر (weekday 4) — انتهاء أوبشن + خطر العطلة
            if _et_now.weekday() == 4 and _et_mins >= (13 * 60):
                effective_min += 1.0
        except Exception:
            pass

        if bs >= ps and bs >= effective_min:
            direction, score = 'call', bs
        elif ps > bs and ps >= effective_min:
            direction, score = 'put', ps
        else:
            return None

        confidence = 'high' if score >= high_confidence_threshold else 'medium'

        # ── تأكيد Multi-Timeframe (مؤشر جودة لا بوابة رفض) ──────────────────
        _shadow_tf: dict = {}
        tf15 = _quick_direction(symbol, '15m', '5d', shadow=_shadow_tf)
        tf1h = _quick_direction(symbol, '1h',  '30d', shadow=_shadow_tf)
        tf4h = _quick_direction(symbol, '4h',  '60d', shadow=_shadow_tf)
        mtf_score = sum(1 for tf in [tf15, tf1h, tf4h] if tf == direction)

        # SHADOW: mtf_score بمنطق _quick_direction القديم (للمقارنة فقط)
        mtf_score_old = sum(1 for v in _shadow_tf.values() if v['old'] == direction)

        # مكافأة / عقوبة حسب عدد الفريمات المؤكِّدة (3→+0.9, 2→+0.3, 1→-0.5, 0→-3.0)
        def _mtf_adj(m: int) -> float:
            return 0.9 if m == 3 else 0.3 if m == 2 else -0.5 if m == 1 else -3.0

        score_pre_mtf = score
        score += _mtf_adj(mtf_score)                          # القرار الفعلي (جديد)
        score_old = score_pre_mtf + _mtf_adj(mtf_score_old)   # SHADOW (قديم)

        # ── فلتر صارم: 0DTE + لا تأكيد من أي فريم = مقامرة → رفض ──────────────
        is_scalp     = (atr / price) < SCALP_ATR_PCT          # القرار الفعلي
        is_scalp_old = (atr / price) < SCALP_ATR_PCT_OLD      # SHADOW

        # SHADOW: قرار كل منطق عند مرحلة MTF/scalp/min-score (المرحلة التي غُيِّرت).
        # الفلاتر اللاحقة (R:R/perf/تكلفة) متطابقة للقديم والجديد فلا تسبّب تباعداً.
        gate_new = is_scalp     and mtf_score     == 0
        gate_old = is_scalp_old and mtf_score_old == 0
        pass_new = (not gate_new) and (score     >= effective_min)
        pass_old = (not gate_old) and (score_old >= effective_min)
        if gate_old != gate_new or pass_old != pass_new:
            _shadow_record(symbol, direction, _shadow_tf, mtf_score, mtf_score_old,
                           is_scalp, is_scalp_old, score, score_old, effective_min,
                           gate_old, gate_new, pass_old, pass_new, rsi, atr / price * 100)

        if gate_new:
            # Diagnostic verbose — لتحليل الفرص الضائعة لاحقاً
            print(f"  [analyzer] {symbol}: رُفضت — 0DTE+MTF=0 | "
                  f"dir={direction} score={score:.1f} RSI={rsi:.1f} "
                  f"atr/p={atr/price*100:.2f}% | "
                  f"tf15={tf15 or '—'} tf1h={tf1h or '—'} tf4h={tf4h or '—'}")
            return None

        # أعد فحص الحد الأدنى بعد تعديل السكور
        if score < effective_min:
            return None

        # ── حساب مستويات الإشارة ──────────────────────────────────────────────
        if direction == 'call':
            base       = near_sup if at_sup else (price - atr * 0.2)
            entry_low  = round(base, 2)
            entry_high = round(base + atr * 0.35, 2)
            stop       = round(entry_low - atr * 0.5, 2)
            min_t1     = entry_high + atr * 0.5
            target1    = round(near_res if (resistances and near_res > min_t1) else min_t1, 2)
            target2    = round(target1 + atr * 0.6, 2)
            if bull_div:     entry_type = 'RSI Divergence 📐'
            elif bull_sweep: entry_type = 'Liquidity Sweep 🌊'
            elif bull_break: entry_type = 'Breaker Block 🔄'
            elif bull_ob:    entry_type = 'Order Block 🏛️'
            elif bull_fvg:   entry_type = 'FVG ⚡'
            elif near_pml:           entry_type = 'PML 🌙'           # شهري — الأعمق
            elif near_pwl:           entry_type = 'PWL 📅'           # أسبوعي
            elif at_sup or near_pdl: entry_type = 'إعادة اختبار'   # يومي
            else:            entry_type = 'اختراق'
        else:
            base       = near_res if at_res else (price + atr * 0.2)
            entry_high = round(base, 2)
            entry_low  = round(base - atr * 0.35, 2)
            stop       = round(entry_high + atr * 0.5, 2)
            max_t1     = entry_low - atr * 0.5
            target1    = round(near_sup if (supports and near_sup < max_t1) else max_t1, 2)
            target2    = round(target1 - atr * 0.6, 2)
            if bear_div:     entry_type = 'RSI Divergence 📐'
            elif bear_sweep: entry_type = 'Liquidity Sweep 🌊'
            elif bear_break: entry_type = 'Breaker Block 🔄'
            elif bear_ob:    entry_type = 'Order Block 🏛️'
            elif bear_fvg:   entry_type = 'FVG ⚡'
            elif near_pmh:           entry_type = 'PMH 🌙'
            elif near_pwh:           entry_type = 'PWH 📅'
            elif at_res or near_pdh: entry_type = 'إعادة اختبار'
            else:            entry_type = 'اختراق'

        # ── رفع الحد لـ RSI Divergence (+1.5) ────────────────────────────────
        if 'RSI Divergence' in entry_type and score < effective_min + 1.5:
            return None

        # ── Auto-Weight: تعديل السكور بناءً على الأداء التاريخي ──────────────
        score += _get_perf_adj(entry_type)
        if score < effective_min:
            return None

        # ── سقف الهدف: يمنع R:R الوهمي من مقاومة/دعم بعيد جداً ────────────────
        MAX_RR = 4.0
        entry_mid = (entry_low + entry_high) / 2
        if direction == 'call' and entry_mid > stop:
            risk      = entry_mid - stop
            cap_t1    = round(entry_mid + risk * MAX_RR, 2)
            if target1 > cap_t1:
                target1 = cap_t1
                target2 = round(target1 + atr * 0.6, 2)
        elif direction == 'put' and stop > entry_mid:
            risk      = stop - entry_mid
            cap_t1    = round(entry_mid - risk * MAX_RR, 2)
            if target1 < cap_t1:
                target1 = cap_t1
                target2 = round(target1 - atr * 0.6, 2)

        # ── فلتر Risk/Reward ──────────────────────────────────────────────────
        if direction == 'call':
            rr = (target1 - entry_mid) / (entry_mid - stop) if entry_mid > stop else 0.0
        else:
            rr = (entry_mid - target1) / (stop - entry_mid) if stop > entry_mid else 0.0

        if rr < min_rr:
            return None

        # ── HTF Stop Override (أضيق = R:R أفضل) ──────────────────────────────
        if active_zone and htf_direction == ('demand' if direction == 'call' else 'supply'):
            if direction == 'call':
                htf_stop = round(active_zone.low - atr * 0.15, 2)
                if htf_stop > stop:
                    stop = htf_stop
            else:
                htf_stop = round(active_zone.high + atr * 0.15, 2)
                if htf_stop < stop:
                    stop = htf_stop

        # ── حد أدنى لمسافة الوقف من حافة الدخول (يمنع stop داخل ضوضاء الشمعة)
        MIN_STOP_DIST = max(atr * 0.5, 0.50)
        if direction == 'call':
            stop_cap = round(entry_low - MIN_STOP_DIST, 2)
            if stop > stop_cap:
                stop = stop_cap
        else:
            stop_cap = round(entry_high + MIN_STOP_DIST, 2)
            if stop < stop_cap:
                stop = stop_cap

        # إعادة حساب R:R الحقيقي بعد ضبط الـ Stop
        if direction == 'call':
            rr = (target1 - entry_mid) / (entry_mid - stop) if entry_mid > stop else rr
        else:
            rr = (entry_mid - target1) / (stop - entry_mid) if stop > entry_mid else rr

        # رفض الإشارة لو R:R نزل تحت الحد بعد توسيع الـ Stop
        if rr < min_rr:
            print(f"  [analyzer] {symbol}: رُفضت — R:R={rr:.2f} بعد تطبيق حد الوقف الأدنى")
            return None

        # إعادة تقييم التأكيدات بالاتجاه النهائي (يُصحّح حالة انقلاب الاتجاه)
        if active_zone:
            _bc, _brc = cisd_5m(df5)
            is_cisd     = (direction == 'call' and _bc) or (direction == 'put' and _brc)
            is_displace = displacement_5m(df5, direction, atr)

        is_scalp   = (atr / price) < SCALP_ATR_PCT
        expiry, strike, option_price, delta, iv, theta = _get_contract(
            symbol, direction, price, is_scalp=is_scalp, score=score)

        # ── سقف تكلفة العقد (تكلفة العقد = Premium × 100) ──────────────────────
        try:
            import db as _db
            _max_cost = float(_db.get_config("max_contract_cost", "0") or 0)
        except Exception:
            _max_cost = 0.0
        if _max_cost > 0 and option_price > 0 and (option_price * 100) > _max_cost:
            print(f"  [analyzer] {symbol}: رُفضت — العقد ${option_price*100:.0f} > سقف ${_max_cost:.0f}")
            return None

        # ── Position Sizing ───────────────────────────────────────────────────
        import config as _cfg
        try:
            import db as _db
            _acct = _db.get_account_size(_cfg.ACCOUNT_SIZE)
        except Exception:
            _acct = _cfg.ACCOUNT_SIZE
        risk_amount = _acct * _cfg.RISK_PCT
        contracts   = max(1, int(risk_amount / (option_price * 100))) if option_price > 0 else 1

        # ── Options Flow (سياق مؤسسي — يُسجَّل للتقييم لاحقاً) ────────────────
        of_mp = of_cw = of_pw = of_pcr = 0.0
        try:
            import options_flow as _of
            flow = _of.get_options_flow(symbol)
            if flow:
                of_mp, of_cw, of_pw, of_pcr = (
                    flow["max_pain"], flow["call_wall"], flow["put_wall"], flow["pcr"])
        except Exception:
            pass

        return SignalResult(
            symbol=symbol, direction=direction, confidence=confidence,
            score=score, entry_low=entry_low, entry_high=entry_high,
            stop=stop, target1=target1, target2=target2,
            entry_type=entry_type, current_price=price,
            is_scalp=is_scalp, expiry=expiry, strike=strike,
            rr=round(rr, 2), vix=round(vix, 1), mtf_score=mtf_score,
            option_price=option_price, delta=delta, iv=iv, theta=theta,
            contracts=contracts, vwap=round(vwap_val, 2), regime=regime,
            htf_zone_tf=htf_zone_tf, htf_zone_type=htf_zone_type,
            htf_direction=htf_direction, cisd=is_cisd, displacement=is_displace,
            smt_divergence=bool(_smt_dir), smt_direction=_smt_dir,
            structure_event=_ms_event or "",
            structure_bias=_ms_bias or "",
            event_bars_ago=int(ms.get('event_bars_ago') or 0),
            pd_summary=pd_zones['summary'],
            pd_bull_modifier=pd_zones['bull_modifier'],
            pd_bear_modifier=pd_zones['bear_modifier'],
            pd_zone_daily=pd_zones['daily']['zone'],
            pd_zone_4h=pd_zones['4h']['zone'],
            pd_zone_1h=pd_zones['1h']['zone'],
            strong_high=float(strong_h) if strong_h else 0.0,
            strong_low=float(strong_l) if strong_l else 0.0,
            near_strong_high=bool(near_strong_high),
            near_strong_low=bool(near_strong_low),
            pwh=float(pwh) if pwh else 0.0,
            pwl=float(pwl) if pwl else 0.0,
            pmh=float(pmh) if pmh else 0.0,
            pml=float(pml) if pml else 0.0,
            near_pwh=bool(near_pwh),
            near_pwl=bool(near_pwl),
            near_pmh=bool(near_pmh),
            near_pml=bool(near_pml),
            eqh_level=float(eqh_level) if eqh_level else 0.0,
            eql_level=float(eql_level) if eql_level else 0.0,
            near_eqh=bool(near_eqh),
            near_eql=bool(near_eql),
            eqh_swept=bool(eqh_swept),
            eql_swept=bool(eql_swept),
            ote_active=bool(ote_active),
            ote_direction=ote_direction,
            ote_leg_start=float(ote['leg_start']) if ote['leg_start'] else 0.0,
            ote_leg_end=float(ote['leg_end']) if ote['leg_end'] else 0.0,
            ote_golden_pocket=float(ote['golden_pocket']) if ote['golden_pocket'] else 0.0,
            in_ote=bool(in_ote),
            in_golden_pocket=bool(in_golden),
            inverse_ote=bool(in_inverse),
            swing_event=_swing_event or "",
            swing_bias=_swing_bias or "",
            alignment=alignment or "",
            max_pain=of_mp, call_wall=of_cw, put_wall=of_pw, pcr=of_pcr,
        )

    except Exception as e:
        print(f"  [analyzer] {symbol}: {e}")
        return None
