from dataclasses import dataclass
from typing import Optional, List, Tuple
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import pytz


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


# ─── Indicators ───────────────────────────────────────────────────────────────

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

def _pivot_levels(df: pd.DataFrame, lookback: int = 5) -> Tuple[List[float], List[float]]:
    highs, lows = [], []
    n = len(df)
    for i in range(lookback, n - lookback):
        w_h = df['High'].iloc[i - lookback: i + lookback + 1]
        if df['High'].iloc[i] == w_h.max():
            highs.append(float(df['High'].iloc[i]))
        w_l = df['Low'].iloc[i - lookback: i + lookback + 1]
        if df['Low'].iloc[i] == w_l.min():
            lows.append(float(df['Low'].iloc[i]))
    return highs, lows


def _find_fvg(df: pd.DataFrame) -> List[Tuple[float, float, str]]:
    fvgs = []
    for i in range(2, len(df)):
        c0_h = df['High'].iloc[i - 2]
        c0_l = df['Low'].iloc[i - 2]
        c2_h = df['High'].iloc[i]
        c2_l = df['Low'].iloc[i]
        if c2_l > c0_h:
            fvgs.append((float(c0_h), float(c2_l), 'bullish'))
        elif c2_h < c0_l:
            fvgs.append((float(c2_h), float(c0_l), 'bearish'))
    return fvgs[-8:]


def _find_order_blocks(df: pd.DataFrame) -> List[Tuple[float, float, str]]:
    obs = []
    for i in range(1, len(df) - 4):
        c = df.iloc[i]
        span = float(c['High'] - c['Low'])
        if span == 0:
            continue
        following = df.iloc[i + 1: i + 5]
        if c['Close'] < c['Open']:
            if (following['Close'].max() - c['Low']) > span * 2.0:
                obs.append((float(c['Low']), float(c['High']), 'bullish'))
        elif c['Close'] > c['Open']:
            if (c['High'] - following['Close'].min()) > span * 2.0:
                obs.append((float(c['Low']), float(c['High']), 'bearish'))
    return obs[-8:]


# ─── Filters ──────────────────────────────────────────────────────────────────

def get_vix() -> float:
    """Returns current VIX value (defaults to 20 if unavailable)."""
    try:
        hist = yf.Ticker("^VIX").history(period="1d", interval="5m")
        return float(hist['Close'].iloc[-1]) if not hist.empty else 20.0
    except Exception:
        return 20.0


def has_earnings_soon(symbol: str, days: int = 2) -> bool:
    """True if the symbol has earnings within the next N days."""
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None:
            return False
        today = datetime.now().date()
        for col in (cal.columns if hasattr(cal, 'columns') else []):
            val = cal[col].get('Earnings Date') if hasattr(cal[col], 'get') else None
            if val is not None:
                try:
                    delta = (pd.Timestamp(val).date() - today).days
                    if -1 <= delta <= days:
                        return True
                except Exception:
                    pass
        return False
    except Exception:
        return False


def _quick_direction(symbol: str, interval: str, period: str) -> Optional[str]:
    """Fast direction check on a single timeframe. Returns 'call', 'put', or None."""
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df.empty or len(df) < 30:
            return None
        price  = float(df['Close'].iloc[-1])
        rsi    = float(_rsi(df['Close']).iloc[-1])
        rsi_p  = float(_rsi(df['Close']).iloc[-4])
        sma20  = float(df['Close'].rolling(20).mean().iloc[-1])
        if rsi < 50 and rsi > rsi_p and price > sma20:
            return 'call'
        if rsi > 50 and rsi < rsi_p and price < sma20:
            return 'put'
        return None
    except Exception:
        return None


# ─── Options contract ─────────────────────────────────────────────────────────

def _get_contract(symbol: str, direction: str, price: float) -> Tuple[str, float]:
    et = pytz.timezone('America/New_York')
    today_str = datetime.now(et).strftime('%Y-%m-%d')
    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return today_str.replace('-', ''), round(price)
        available = [e for e in expirations if e >= today_str]
        expiry = available[0] if available else expirations[0]
        chain = ticker.option_chain(expiry)
        opts = chain.calls if direction == 'call' else chain.puts
        if opts.empty:
            return expiry.replace('-', ''), round(price)
        opts = opts.copy()
        opts['dist'] = (opts['strike'] - price).abs()
        strike = float(opts.nsmallest(1, 'dist')['strike'].iloc[0])
        return expiry.replace('-', ''), strike
    except Exception:
        return today_str.replace('-', ''), float(round(price))


# ─── Quick scan (للـ Dashboard فقط — بدون options chain أو MTF) ───────────────

def quick_scan(symbol: str) -> Optional[dict]:
    """
    تحليل سريع للـ Dashboard: يُرجع التقييم والاتجاه بدون جلب Options أو MTF.
    """
    try:
        ticker = yf.Ticker(symbol)
        df5  = ticker.history(period='2d',  interval='5m',  auto_adjust=True)
        df1d = ticker.history(period='30d', interval='1d',  auto_adjust=True)
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

        recent = df5.tail(100)
        pivot_highs, pivot_lows = _pivot_levels(recent)
        fvgs = _find_fvg(recent)
        obs  = _find_order_blocks(recent)

        supports    = sorted([l for l in pivot_lows  if l < price], reverse=True)
        resistances = sorted([h for h in pivot_highs if h > price])
        near_sup = supports[0]    if supports    else price * 0.998
        near_res = resistances[0] if resistances else price * 1.002

        at_sup   = abs(price - near_sup) / price < 0.004
        at_res   = abs(price - near_res) / price < 0.004
        near_pdl = abs(price - pdl)     / price < 0.005
        near_pdh = abs(price - pdh)     / price < 0.005
        bull_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bullish')
        bear_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bearish')
        bull_ob  = any(lo <= price <= hi * 1.003 for lo, hi, t in obs if t == 'bullish')
        bear_ob  = any(lo * 0.997 <= price <= hi  for lo, hi, t in obs if t == 'bearish')

        bs = ps = 0.0
        if rsi < 30: bs += 3.0
        elif rsi < 40 and rsi_rising: bs += 2.0
        elif rsi < 50 and rsi_rising: bs += 1.0
        if rsi > 70: ps += 3.0
        elif rsi > 60 and not rsi_rising: ps += 2.0
        elif rsi > 50 and not rsi_rising: ps += 1.0
        if at_sup or near_pdl:   bs += 2.5
        if at_res or near_pdh:   ps += 2.5
        if bull_fvg: bs += 1.5
        if bear_fvg: ps += 1.5
        if bull_ob:  bs += 2.0
        if bear_ob:  ps += 2.0
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

        entry_mid = (entry_low + entry_high) / 2
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
        }
    except Exception as e:
        return None


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
        ticker = yf.Ticker(symbol)
        df5  = ticker.history(period='3d',  interval='5m',  auto_adjust=True)
        df1d = ticker.history(period='60d', interval='1d',  auto_adjust=True)

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

        # ── ICT Structures ────────────────────────────────────────────────────
        recent = df5.tail(120)
        pivot_highs, pivot_lows = _pivot_levels(recent)
        fvgs = _find_fvg(recent)
        obs  = _find_order_blocks(recent)

        supports    = sorted([l for l in pivot_lows  if l < price], reverse=True)
        resistances = sorted([h for h in pivot_highs if h > price])
        near_sup = supports[0]    if supports    else price * 0.998
        near_res = resistances[0] if resistances else price * 1.002

        at_sup   = abs(price - near_sup) / price < 0.004
        at_res   = abs(price - near_res) / price < 0.004
        near_pdl = abs(price - pdl)     / price < 0.005
        near_pdh = abs(price - pdh)     / price < 0.005

        bull_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bullish')
        bear_fvg = any(lo <= price <= hi for lo, hi, t in fvgs if t == 'bearish')
        bull_ob  = any(lo <= price <= hi * 1.003 for lo, hi, t in obs if t == 'bullish')
        bear_ob  = any(lo * 0.997 <= price <= hi  for lo, hi, t in obs if t == 'bearish')

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

        if bull_fvg: bs += 1.5
        if bear_fvg: ps += 1.5
        if bull_ob:  bs += 2.0
        if bear_ob:  ps += 2.0

        if price > sma10 and price > sma30: bs += 0.5
        if price < sma10 and price < sma30: ps += 0.5
        if not np.isnan(sma200d):
            if price > sma200d: bs += 0.5
            else:               ps += 0.5

        if vol_surge and last_bull:      bs += 1.0
        elif vol_surge and not last_bull: ps += 1.0

        # ── فلتر VIX ─────────────────────────────────────────────────────────
        vix = vix_value if vix_value is not None else 20.0
        effective_min = min_score + (1.0 if vix > 25 else 0.0) + (1.5 if vix > 32 else 0.0)

        if bs >= ps and bs >= effective_min:
            direction, score = 'call', bs
        elif ps > bs and ps >= effective_min:
            direction, score = 'put', ps
        else:
            return None

        confidence = 'high' if score >= high_confidence_threshold else 'medium'

        # ── تأكيد Multi-Timeframe ─────────────────────────────────────────────
        tf15 = _quick_direction(symbol, '15m', '5d')
        tf1h = _quick_direction(symbol, '1h',  '30d')
        mtf_score = sum(1 for tf in [tf15, tf1h] if tf == direction)
        if require_mtf and mtf_score == 0:
            return None   # لا يوجد تأكيد من أي إطار زمني أعلى

        # ── حساب مستويات الإشارة ──────────────────────────────────────────────
        if direction == 'call':
            base       = near_sup if at_sup else (price - atr * 0.2)
            entry_low  = round(base, 2)
            entry_high = round(base + atr * 0.35, 2)
            stop       = round(entry_low - atr * 0.5, 2)
            min_t1     = entry_high + atr * 0.5
            target1    = round(near_res if (resistances and near_res > min_t1) else min_t1, 2)
            target2    = round(target1 + atr * 0.6, 2)
            entry_type = 'إعادة اختبار' if (at_sup or near_pdl) else 'اختراق'
        else:
            base       = near_res if at_res else (price + atr * 0.2)
            entry_high = round(base, 2)
            entry_low  = round(base - atr * 0.35, 2)
            stop       = round(entry_high + atr * 0.5, 2)
            max_t1     = entry_low - atr * 0.5
            target1    = round(near_sup if (supports and near_sup < max_t1) else max_t1, 2)
            target2    = round(target1 - atr * 0.6, 2)
            entry_type = 'إعادة اختبار' if (at_res or near_pdh) else 'اختراق'

        # ── فلتر Risk/Reward ──────────────────────────────────────────────────
        entry_mid = (entry_low + entry_high) / 2
        if direction == 'call':
            rr = (target1 - entry_mid) / (entry_mid - stop) if entry_mid > stop else 0.0
        else:
            rr = (entry_mid - target1) / (stop - entry_mid) if stop > entry_mid else 0.0

        if rr < min_rr:
            return None

        is_scalp   = (atr / price) < 0.007
        expiry, strike = _get_contract(symbol, direction, price)

        return SignalResult(
            symbol=symbol, direction=direction, confidence=confidence,
            score=score, entry_low=entry_low, entry_high=entry_high,
            stop=stop, target1=target1, target2=target2,
            entry_type=entry_type, current_price=price,
            is_scalp=is_scalp, expiry=expiry, strike=strike,
            rr=round(rr, 2), vix=round(vix, 1), mtf_score=mtf_score,
        )

    except Exception as e:
        print(f"  [analyzer] {symbol}: {e}")
        return None
