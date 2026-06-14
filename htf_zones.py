"""
HTF Zone Detection — مناطق الاهتمام على الفريمات العليا (1H / 4H / Daily)
+ تأكيدات الدخول على الـ 5m: CISD و Displacement و FVG Confluence

المنطق:
  HTF  يجيب على: أين؟    (المنطقة)
  5m   يجيب على: متى؟    (التأكيد)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# الدوال الأساسية للبنية السوقية تأتي من market_structure (مصدر واحد، لا تكرار)
from market_structure import (
    _pivot_levels, _find_fvg, _find_order_blocks,
    _find_inversion_fvgs, structure_bias,
)


# ─── Data Class ───────────────────────────────────────────────────────────────

@dataclass
class HTFZone:
    low:       float
    high:      float
    zone_type: str    # 'ob' | 'fvg'
    direction: str    # 'demand' | 'supply'
    timeframe: str    # '1h' | '4h' | 'daily'
    strength:  float  # 1.0 (1h) → 2.0 (4h) → 3.0 (daily)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def __str__(self) -> str:
        tf_ar = {'1h': '١ساعة', '4h': '٤ساعات', 'daily': 'يومي'}.get(self.timeframe, self.timeframe)
        return f"{tf_ar} {self.zone_type.upper()} ({self.direction})"


# ─── Simple cache ─────────────────────────────────────────────────────────────

_cache: Dict[str, dict] = {}
_CACHE_TTL = 900   # 15 دقيقة — HTF zones لا تتغير بسرعة


# ─── Zone Detection ───────────────────────────────────────────────────────────

def _zones_from_df(df: pd.DataFrame, timeframe: str, strength: float) -> List[HTFZone]:
    """يستخرج OBs و FVGs و Inversion FVGs من DataFrame لفريم معيّن."""
    zones: List[HTFZone] = []
    if df.empty or len(df) < 20:
        return zones

    recent = df.tail(80)

    # نمرّر track_mitigation=False لإبقاء HTF يستخدم النوع الأصلي فقط.
    # الـ inversions في HTF تُكتشف عبر _find_inversion_fvgs أدناه (منطق منفصل).
    for lo, hi, fvg_type in _find_fvg(recent, track_mitigation=False):
        direction = 'demand' if fvg_type == 'bullish' else 'supply'
        zones.append(HTFZone(
            low=round(lo, 4), high=round(hi, 4),
            zone_type='fvg', direction=direction,
            timeframe=timeframe, strength=strength,
        ))

    for lo, hi, ob_type in _find_order_blocks(recent, track_mitigation=False):
        direction = 'demand' if ob_type == 'bullish' else 'supply'
        zones.append(HTFZone(
            low=round(lo, 4), high=round(hi, 4),
            zone_type='ob', direction=direction,
            timeframe=timeframe, strength=strength,
        ))

    # Inversion FVG — أقوى من الفجوة العادية (تطلّبت كسراً) → +0.5 قوة
    for lo, hi, inv_dir in _find_inversion_fvgs(recent):
        zones.append(HTFZone(
            low=round(lo, 4), high=round(hi, 4),
            zone_type='inv_fvg', direction=inv_dir,
            timeframe=timeframe, strength=strength + 0.5,
        ))

    return zones


# ─── Main HTF Analysis ────────────────────────────────────────────────────────

def get_htf_analysis(
    symbol: str,
    df1h:   pd.DataFrame,
    df4h:   pd.DataFrame,
    df1d:   pd.DataFrame,
) -> dict:
    """
    يحلل الفريمات العليا ويُرجع:
    {
        'zones'    : List[HTFZone],
        'structure': {'1h': str, '4h': str, 'daily': str},
    }
    """
    now = time.time()
    if symbol in _cache and now - _cache[symbol].get('ts', 0) < _CACHE_TTL:
        return _cache[symbol]

    zones: List[HTFZone] = []
    structure: Dict[str, str] = {}

    for df, tf, strength in [
        (df1h, '1h',    1.0),
        (df4h, '4h',    2.0),
        (df1d, 'daily', 3.0),
    ]:
        if not df.empty:
            zones    += _zones_from_df(df, tf, strength)
            structure[tf] = structure_bias(df)

    result = {'zones': zones, 'structure': structure, 'ts': now}
    _cache[symbol] = result
    return result


# ─── Price vs Zone ────────────────────────────────────────────────────────────

def price_in_zone(price: float, zones: List[HTFZone]) -> Optional[HTFZone]:
    """يُرجع أقوى منطقة يقع السعر داخلها حالياً."""
    active = [z for z in zones if z.low <= price <= z.high]
    if not active:
        return None
    return max(active, key=lambda z: z.strength)


def nearest_zone(
    price:     float,
    zones:     List[HTFZone],
    direction: str,
    max_pct:   float = 0.006,
) -> Optional[HTFZone]:
    """
    يُرجع أقرب منطقة من السعر (حتى 0.6%) في الاتجاه الصحيح.
    يستخدم عندما لا يكون السعر داخل منطقة لكنه قريب منها.
    """
    exp_dir = 'demand' if direction == 'call' else 'supply'
    nearby = [
        z for z in zones
        if z.direction == exp_dir
        and abs(price - z.mid) / price <= max_pct
    ]
    if not nearby:
        return None
    return max(nearby, key=lambda z: z.strength)


# ─── LTF Confirmations (5m) ───────────────────────────────────────────────────

def cisd_5m(df: pd.DataFrame, lookback: int = 15) -> Tuple[bool, bool]:
    """
    CISD (Change in State of Delivery) على الـ 5m:

    Bullish CISD:
      - آخر 5 شموع تكسر قاعاً مرجعياً (مسح السيولة)
      - ثم تُغلق فوق قمة مرجعية (تحوّل هيكلي)

    Bearish CISD:
      - آخر 5 شموع تكسر قمة مرجعية
      - ثم تُغلق تحت قاع مرجعي
    """
    if len(df) < lookback + 5:
        return False, False

    # الفترة المرجعية (قبل آخر 5 شموع)
    anchor   = df.iloc[-(lookback):-5]
    ref_high = float(anchor['High'].max())
    ref_low  = float(anchor['Low'].min())

    # آخر 5 شموع (النشاط الأخير)
    recent  = df.tail(5)
    r_low   = float(recent['Low'].min())
    r_high  = float(recent['High'].max())
    r_close = float(recent['Close'].iloc[-1])

    # Bullish: مسح القاع + إغلاق فوق القمة
    bull = (r_low  < ref_low  * 0.9998) and (r_close > ref_high * 0.9990)
    # Bearish: مسح القمة + إغلاق تحت القاع
    bear = (r_high > ref_high * 1.0002) and (r_close < ref_low  * 1.0010)

    return bool(bull), bool(bear)


def displacement_5m(df: pd.DataFrame, direction: str, atr: float) -> bool:
    """
    Displacement Candle: شمعة مؤسسية كبيرة تُظهر تدخلاً حقيقياً.

    الشروط:
    - جسم الشمعة > 1.2 × ATR  (حجم استثنائي)
    - جسم الشمعة ≥ 60% من النطاق الكامل  (وليس مجرد ذيل)
    - الإغلاق في أعلى 30% للـ Call أو أسفل 30% للـ Put
    """
    if len(df) < 2 or atr <= 0:
        return False

    last = df.iloc[-1]
    o, h, l, c = float(last['Open']), float(last['High']), float(last['Low']), float(last['Close'])
    body = abs(c - o)
    rng  = h - l

    if rng == 0 or body < atr * 1.2:
        return False

    body_ratio = body / rng
    close_pos  = (c - l) / rng   # 0 = أسفل النطاق، 1 = أعله

    if direction == 'call':
        return c > o and close_pos >= 0.70 and body_ratio >= 0.60
    else:
        return c < o and close_pos <= 0.30 and body_ratio >= 0.60


def inversion_fvg_confirms_zone(df5m: pd.DataFrame, zone: HTFZone, direction: str) -> bool:
    """
    Inversion FVG داخل منطقة HTF — تأكيد قوي (مثل Breaker لكن على الفجوات):

    Call : فجوة هابطة (bearish FVG) اخترقها السعر للأعلى وأغلق فوقها
           → تحوّلت من مقاومة إلى دعم، والسعر يعيد اختبارها داخل المنطقة.
    Put  : فجوة صاعدة (bullish FVG) اخترقها السعر للأسفل وأغلق تحتها
           → تحوّلت من دعم إلى مقاومة.

    الشرط الإضافي: الفجوة المنقلبة تتقاطع مع منطقة الـ HTF (Confluence).
    """
    window = df5m.tail(40)
    n = len(window)
    if n < 6:
        return False

    closes = window['Close'].values
    highs  = window['High'].values
    lows   = window['Low'].values

    for i in range(2, n - 1):
        c0_h, c0_l = float(highs[i - 2]), float(lows[i - 2])
        c2_h, c2_l = float(highs[i]),     float(lows[i])

        # فجوة هابطة (bearish FVG): band = (c2_h, c0_l)
        if c2_h < c0_l:
            band_lo, band_hi = c2_h, c0_l
            # هل أغلق السعر لاحقاً فوق الفجوة؟ (انقلاب صعودي)
            broke_up = any(closes[j] > band_hi for j in range(i + 1, n))
            if direction == 'call' and broke_up:
                if band_lo <= zone.high and band_hi >= zone.low:
                    return True

        # فجوة صاعدة (bullish FVG): band = (c0_h, c2_l)
        elif c2_l > c0_h:
            band_lo, band_hi = c0_h, c2_l
            # هل أغلق السعر لاحقاً تحت الفجوة؟ (انقلاب هبوطي)
            broke_dn = any(closes[j] < band_lo for j in range(i + 1, n))
            if direction == 'put' and broke_dn:
                if band_lo <= zone.high and band_hi >= zone.low:
                    return True

    return False


def fvg_confirms_zone(df5m: pd.DataFrame, zone: HTFZone, direction: str) -> bool:
    """
    يتحقق من وجود FVG على الـ 5m داخل منطقة HTF يدعم الاتجاه.
    FVG صغير داخل منطقة كبيرة = تقاطع = تأكيد إضافي.
    """
    recent_fvgs = _find_fvg(df5m.tail(15))
    for lo, hi, fvg_type in recent_fvgs:
        if direction == 'call' and fvg_type == 'bullish':
            if lo <= zone.high and hi >= zone.low:
                return True
        elif direction == 'put' and fvg_type == 'bearish':
            if lo <= zone.high and hi >= zone.low:
                return True
    return False
