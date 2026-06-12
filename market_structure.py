"""
Market Structure — البنية السوقية الموحّدة

يحتوي على:
  • الدوال الأساسية (موحَّدة من htf_zones + analyzer):
      _pivot_levels, _find_fvg, _find_order_blocks,
      _find_inversion_fvgs, structure_bias
  • اكتشاف الأحداث الهيكلية:
      detect_structure_events → BOS / CHoCH / MSS

التعريفات (مطابقة لرؤية صقر):
  CHoCH : إغلاق يكسر آخر pivot عكس الترند الحالي  (تحوّل محتمل)
  BOS   : أول كسر في الاتجاه الجديد بعد CHoCH       (تأكيد التحوّل)
  MSS   : أي كسر إضافي بعد BOS في نفس الاتجاه       (ترند راسخ متسارع)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd


# ─── Pivots / FVG / Order Blocks ─────────────────────────────────────────────

def _pivot_levels(df: pd.DataFrame, lookback: int = 5) -> Tuple[List[float], List[float]]:
    """نقاط الـ pivot المؤكَّدة (مع نافذة lookback من كل جانب)."""
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


def _find_fvg(df: pd.DataFrame, limit: int = 10) -> List[Tuple[float, float, str]]:
    """فجوات القيمة العادلة (FVG) — يُرجع آخر `limit` فجوة."""
    fvgs = []
    for i in range(2, len(df)):
        c0_h, c0_l = df['High'].iloc[i - 2], df['Low'].iloc[i - 2]
        c2_h, c2_l = df['High'].iloc[i],     df['Low'].iloc[i]
        if c2_l > c0_h:
            fvgs.append((float(c0_h), float(c2_l), 'bullish'))
        elif c2_h < c0_l:
            fvgs.append((float(c2_h), float(c0_l), 'bearish'))
    return fvgs[-limit:]


def _find_order_blocks(df: pd.DataFrame, limit: int = 10) -> List[Tuple[float, float, str]]:
    """مناطق Order Blocks — يُرجع آخر `limit` كتلة."""
    obs = []
    for i in range(1, len(df) - 4):
        c    = df.iloc[i]
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
    return obs[-limit:]


def _find_inversion_fvgs(df: pd.DataFrame, limit: int = 6) -> List[Tuple[float, float, str]]:
    """
    فجوات منقلبة (Inversion FVG) — فجوة اخترقها السعر وأغلق خلفها فانقلب دورها:
      bearish FVG كُسرت للأعلى → تصبح منطقة طلب (demand / دعم)
      bullish FVG كُسرت للأسفل → تصبح منطقة عرض (supply / مقاومة)
    تُرجع (low, high, new_direction).
    """
    out: List[Tuple[float, float, str]] = []
    closes = df['Close'].values
    highs  = df['High'].values
    lows   = df['Low'].values
    n = len(df)
    for i in range(2, n - 1):
        c0_h, c0_l = float(highs[i - 2]), float(lows[i - 2])
        c2_h, c2_l = float(highs[i]),     float(lows[i])
        if c2_h < c0_l:                          # bearish FVG
            band_lo, band_hi = c2_h, c0_l
            if any(closes[j] > band_hi for j in range(i + 1, n)):
                out.append((band_lo, band_hi, 'demand'))
        elif c2_l > c0_h:                        # bullish FVG
            band_lo, band_hi = c0_h, c2_l
            if any(closes[j] < band_lo for j in range(i + 1, n)):
                out.append((band_lo, band_hi, 'supply'))
    return out[-limit:]


# ─── Structure Bias (HH/HL vs LH/LL) ─────────────────────────────────────────

def structure_bias(df: pd.DataFrame, lookback: int = 60) -> str:
    """يحدّد اتجاه البنية من HH/HL أو LH/LL. Returns: bullish | bearish | neutral."""
    if len(df) < lookback:
        return 'neutral'

    recent = df.tail(lookback)
    pivot_highs, pivot_lows = _pivot_levels(recent, lookback=4)

    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return 'neutral'

    h_prev, h_last = pivot_highs[-2], pivot_highs[-1]
    l_prev, l_last = pivot_lows[-2],  pivot_lows[-1]

    if h_last > h_prev and l_last > l_prev:
        return 'bullish'   # HH + HL
    if h_last < h_prev and l_last < l_prev:
        return 'bearish'   # LH + LL
    return 'neutral'


# ─── Structure Events: BOS / CHoCH / MSS ─────────────────────────────────────

def detect_structure_events(df: pd.DataFrame, pivot_size: int = 5) -> Dict:
    """
    يكتشف أحداث البنية السوقية: BOS, CHoCH, MSS.

    التعريفات:
      CHoCH : close يكسر آخر pivot عكس الترند الحالي  (تحوّل محتمل)
      BOS   : أول كسر في الاتجاه الجديد بعد CHoCH      (تأكيد التحوّل)
      MSS   : أي كسر إضافي بعد BOS في نفس الاتجاه      (ترند راسخ + متسارع)

    Returns:
        {
            'last_event':     'BOS' | 'CHoCH' | 'MSS' | None,
            'event_price':    float | None,
            'event_bars_ago': int   | None,
            'current_bias':   'bullish' | 'bearish' | None,
            'pivots': {
                'last_high':     float | None,
                'last_high_bar': int   | None,
                'last_low':      float | None,
                'last_low_bar':  int   | None,
            },
            'events_history': [ {type, price, bar, bias} … آخر 10 ]
        }
    """
    empty = {
        'last_event':     None,
        'event_price':    None,
        'event_bars_ago': None,
        'current_bias':   None,
        'pivots': {
            'last_high': None, 'last_high_bar': None,
            'last_low':  None, 'last_low_bar':  None,
        },
        'events_history': [],
    }

    n = len(df)
    if n < pivot_size * 2 + 2:
        return empty

    highs  = df['High'].values
    lows   = df['Low'].values
    closes = df['Close'].values

    def _is_pivot_high(i: int) -> bool:
        return all(highs[i] >= highs[j] for j in range(i - pivot_size, i + pivot_size + 1) if j != i)

    def _is_pivot_low(i: int) -> bool:
        return all(lows[i] <= lows[j] for j in range(i - pivot_size, i + pivot_size + 1) if j != i)

    trend_bias: Optional[str] = None
    awaiting_bos = False
    last_pivot_high: Optional[float] = None
    last_pivot_high_bar: Optional[int] = None
    last_pivot_low: Optional[float] = None
    last_pivot_low_bar: Optional[int] = None
    events: List[Dict] = []

    # نمشي على كل bar — تأكيد الـ pivot يتطلب نافذة pivot_size على اليمين
    # فنتمشى حتى n - pivot_size لضمان أن أي pivot نراه فعلياً مؤكَّد
    for i in range(pivot_size, n):
        close_i = float(closes[i])

        # تحديث الـ pivots المؤكَّدة (فقط للنوافذ المكتملة)
        pivot_idx = i - pivot_size
        if pivot_idx >= pivot_size and pivot_idx + pivot_size < n:
            if _is_pivot_high(pivot_idx):
                last_pivot_high = float(highs[pivot_idx])
                last_pivot_high_bar = pivot_idx
            if _is_pivot_low(pivot_idx):
                last_pivot_low = float(lows[pivot_idx])
                last_pivot_low_bar = pivot_idx

        event_type: Optional[str] = None

        # كسر صعودي على إغلاق الشمعة
        if last_pivot_high is not None and close_i > last_pivot_high:
            if trend_bias is None:
                event_type = 'BOS'
                awaiting_bos = False
            elif trend_bias == 'bearish':
                event_type = 'CHoCH'
                awaiting_bos = True
            elif trend_bias == 'bullish':
                if awaiting_bos:
                    event_type = 'BOS'
                    awaiting_bos = False
                else:
                    event_type = 'MSS'

            if event_type:
                trend_bias = 'bullish'
                events.append({
                    'type':  event_type,
                    'price': float(close_i),
                    'bar':   i,
                    'bias':  trend_bias,
                })
                # بعد الكسر، نمسح آخر pivot high (لن يُستخدم مجدداً للكسر نفسه)
                last_pivot_high = None
                last_pivot_high_bar = None

        # كسر هبوطي على إغلاق الشمعة
        elif last_pivot_low is not None and close_i < last_pivot_low:
            if trend_bias is None:
                event_type = 'BOS'
                awaiting_bos = False
            elif trend_bias == 'bullish':
                event_type = 'CHoCH'
                awaiting_bos = True
            elif trend_bias == 'bearish':
                if awaiting_bos:
                    event_type = 'BOS'
                    awaiting_bos = False
                else:
                    event_type = 'MSS'

            if event_type:
                trend_bias = 'bearish'
                events.append({
                    'type':  event_type,
                    'price': float(close_i),
                    'bar':   i,
                    'bias':  trend_bias,
                })
                last_pivot_low = None
                last_pivot_low_bar = None

    if not events:
        return {
            **empty,
            'pivots': {
                'last_high':     last_pivot_high,
                'last_high_bar': last_pivot_high_bar,
                'last_low':      last_pivot_low,
                'last_low_bar':  last_pivot_low_bar,
            },
        }

    last = events[-1]
    return {
        'last_event':     last['type'],
        'event_price':    last['price'],
        'event_bars_ago': n - 1 - last['bar'],
        'current_bias':   trend_bias,
        'pivots': {
            'last_high':     last_pivot_high,
            'last_high_bar': last_pivot_high_bar,
            'last_low':      last_pivot_low,
            'last_low_bar':  last_pivot_low_bar,
        },
        'events_history': events[-10:],
    }
