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


def _find_fvg(df: pd.DataFrame,
              limit: int = 10,
              track_mitigation: bool = True,
              mitigation_source: str = 'highlow') -> List[Tuple[float, float, str]]:
    """
    فجوات القيمة العادلة (FVG) مع تتبّع الـ Mitigation (LuxAlgo SMC #7).

    track_mitigation=True (default):
      Bullish FVG تُملأ إذا نزل السعر تحت bottom (c0_high)
        → تُحوَّل إلى 'supply' (Inversion FVG → مقاومة)
      Bearish FVG تُملأ إذا صعد السعر فوق top (c0_low)
        → تُحوَّل إلى 'demand' (Inversion FVG → دعم)

    Returns: List of (low, high, type) tuples where type is one of:
      'bullish', 'bearish', 'demand', 'supply'
    """
    fvgs = []
    n = len(df)
    highs  = df['High'].values
    lows   = df['Low'].values
    closes = df['Close'].values

    for i in range(2, n):
        c0_h, c0_l = float(highs[i - 2]), float(lows[i - 2])
        c2_h, c2_l = float(highs[i]),     float(lows[i])

        if c2_l > c0_h:
            # Bullish FVG: bottom=c0_h, top=c2_l
            fvg_type = 'bullish'
            if track_mitigation and i + 1 < n:
                check_src = lows[i + 1:] if mitigation_source == 'highlow' else closes[i + 1:]
                if len(check_src) > 0 and check_src.min() < c0_h:
                    fvg_type = 'supply'  # ملئت → انقلبت إلى مقاومة
            fvgs.append((c0_h, c2_l, fvg_type))

        elif c2_h < c0_l:
            # Bearish FVG: bottom=c2_h, top=c0_l
            fvg_type = 'bearish'
            if track_mitigation and i + 1 < n:
                check_src = highs[i + 1:] if mitigation_source == 'highlow' else closes[i + 1:]
                if len(check_src) > 0 and check_src.max() > c0_l:
                    fvg_type = 'demand'  # ملئت → انقلبت إلى دعم
            fvgs.append((c2_h, c0_l, fvg_type))

    return fvgs[-limit:]


def _find_order_blocks(df: pd.DataFrame,
                      limit: int = 10,
                      track_mitigation: bool = True,
                      mitigation_source: str = 'highlow') -> List[Tuple[float, float, str]]:
    """
    مناطق Order Blocks مع تتبّع الـ Mitigation (LuxAlgo SMC #6 + ICT Breaker).

    track_mitigation=True (default):
      Bullish OB يُخترق إذا نزل السعر تحت ob_low بعد +5 شموع من التكوين
        → يُحوَّل إلى 'breaker_bear' (مقاومة)
      Bearish OB يُخترق إذا صعد السعر فوق ob_high بعد +5 شموع
        → يُحوَّل إلى 'breaker_bull' (دعم)

    Returns: List of (low, high, type) tuples where type is one of:
      'bullish', 'bearish', 'breaker_bull', 'breaker_bear'
    """
    obs = []
    n = len(df)
    highs  = df['High'].values
    lows   = df['Low'].values
    closes = df['Close'].values
    opens  = df['Open'].values

    for i in range(1, n - 4):
        ob_low, ob_high = float(lows[i]), float(highs[i])
        span = ob_high - ob_low
        if span == 0:
            continue
        following = df.iloc[i + 1: i + 5]

        if closes[i] < opens[i]:
            # مرشّح Bullish OB
            if (following['Close'].max() - ob_low) > span * 2.0:
                ob_type = 'bullish'
                if track_mitigation and i + 5 < n:
                    check_src = lows[i + 5:] if mitigation_source == 'highlow' else closes[i + 5:]
                    if len(check_src) > 0 and check_src.min() < ob_low:
                        ob_type = 'breaker_bear'  # كان دعم → كُسر → مقاومة
                obs.append((ob_low, ob_high, ob_type))

        elif closes[i] > opens[i]:
            # مرشّح Bearish OB
            if (ob_high - following['Close'].min()) > span * 2.0:
                ob_type = 'bearish'
                if track_mitigation and i + 5 < n:
                    check_src = highs[i + 5:] if mitigation_source == 'highlow' else closes[i + 5:]
                    if len(check_src) > 0 and check_src.max() > ob_high:
                        ob_type = 'breaker_bull'  # كان مقاومة → كُسر → دعم
                obs.append((ob_low, ob_high, ob_type))

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

    # ─── Trailing extremes + Strong/Weak classification (LuxAlgo SMC #4) ──
    # محاكاة LuxAlgo trailing.top/bottom: أعلى/أدنى قيمة منذ آخر BOS/CHoCH/MSS
    if events:
        last_event_bar = events[-1]['bar']
        window_after = df.iloc[last_event_bar:]
        if len(window_after) > 0:
            trailing_top    = float(window_after['High'].max())
            trailing_bottom = float(window_after['Low'].min())
        else:
            trailing_top    = float(df['High'].iloc[-1])
            trailing_bottom = float(df['Low'].iloc[-1])
    else:
        trailing_top    = float(df['High'].max())
        trailing_bottom = float(df['Low'].min())

    # تصنيف القوة بناءً على trend_bias
    strong_high_level = None
    weak_high_level   = None
    strong_low_level  = None
    weak_low_level    = None

    if trend_bias == 'bearish':
        # الترند هابط → القمة لم تُكسر (Strong) + القاع المخترَق ضعيف (Weak)
        strong_high_level = trailing_top
        weak_low_level    = trailing_bottom
    elif trend_bias == 'bullish':
        # الترند صاعد → القاع لم يُكسر (Strong) + القمة المخترَقة ضعيفة (Weak)
        strong_low_level  = trailing_bottom
        weak_high_level   = trailing_top
    # neutral → كل المستويات None

    strength_block = {
        'strong_high':     strong_high_level,
        'weak_high':       weak_high_level,
        'strong_low':      strong_low_level,
        'weak_low':        weak_low_level,
        'trailing_top':    trailing_top,
        'trailing_bottom': trailing_bottom,
    }

    if not events:
        return {
            **empty,
            'pivots': {
                'last_high':     last_pivot_high,
                'last_high_bar': last_pivot_high_bar,
                'last_low':      last_pivot_low,
                'last_low_bar':  last_pivot_low_bar,
            },
            'strength': strength_block,
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
        'strength': strength_block,
    }


# ─── Premium / Discount / Equilibrium (LuxAlgo SMC) ──────────────────────────

def detect_pd_zones(df: pd.DataFrame,
                    lookback: int = 50,
                    min_range_pct: float = 0.005) -> Dict:
    """
    Premium / Discount / Equilibrium Zones من LuxAlgo SMC.

    تصنيف صقر (نسبة من الـ range):
      Premium     : أعلى 5%   (95%-100%)
      Equilibrium : وسط 5%    (47.5%-52.5%)
      Discount    : أدنى 5%   (0%-5%)
      Neutral     : باقي المناطق

    شرط الحد الأدنى للـ range:
      لو range_pct < min_range_pct → سوق ميت → Neutral

    Returns:
        {
            'zone':         'Premium' | 'Discount' | 'Equilibrium' | 'Neutral',
            'swing_high':   float,
            'swing_low':    float,
            'range_pct':    float,    # range / current_price
            'position_pct': float,    # 0-100 موقع السعر داخل الـ range
        }
    """
    empty = {
        'zone': 'Neutral', 'swing_high': 0.0, 'swing_low': 0.0,
        'range_pct': 0.0, 'position_pct': 50.0,
    }
    if df is None or len(df) == 0:
        return empty

    window = df.tail(lookback)
    swing_high = float(window['High'].max())
    swing_low  = float(window['Low'].min())
    current    = float(df['Close'].iloc[-1])

    if current <= 0 or swing_high <= swing_low:
        return empty

    rng = swing_high - swing_low
    range_pct = rng / current

    if range_pct < min_range_pct:
        return {
            'zone': 'Neutral',
            'swing_high':   swing_high,
            'swing_low':    swing_low,
            'range_pct':    range_pct,
            'position_pct': 50.0,
        }

    position_pct = (current - swing_low) / rng * 100.0

    if position_pct >= 95.0:
        zone = 'Premium'
    elif position_pct <= 5.0:
        zone = 'Discount'
    elif 47.5 <= position_pct <= 52.5:
        zone = 'Equilibrium'
    else:
        zone = 'Neutral'

    return {
        'zone':         zone,
        'swing_high':   swing_high,
        'swing_low':    swing_low,
        'range_pct':    range_pct,
        'position_pct': position_pct,
    }


# قيم الـ scoring لكل (فريم، اتجاه، منطقة)
_PD_BULL_WEIGHTS = {
    'daily': {'Discount': +0.5,  'Premium': -0.75, 'Equilibrium': -0.25, 'Neutral': 0.0},
    '4h':    {'Discount': +0.3,  'Premium': -0.45, 'Equilibrium': -0.15, 'Neutral': 0.0},
    '1h':    {'Discount': +0.2,  'Premium': -0.30, 'Equilibrium': -0.10, 'Neutral': 0.0},
}
_PD_BEAR_WEIGHTS = {
    'daily': {'Premium': +0.5,  'Discount': -0.75, 'Equilibrium': -0.25, 'Neutral': 0.0},
    '4h':    {'Premium': +0.3,  'Discount': -0.45, 'Equilibrium': -0.15, 'Neutral': 0.0},
    '1h':    {'Premium': +0.2,  'Discount': -0.30, 'Equilibrium': -0.10, 'Neutral': 0.0},
}


def detect_pd_zones_mtf(df1h: pd.DataFrame,
                        df4h: pd.DataFrame,
                        df1d: pd.DataFrame) -> Dict:
    """
    Multi-Timeframe Premium/Discount Analysis.
    يحسب PD zones على Daily + 4H + 1H ويُرجع modifiers تراكميين + ملخص نصي.

    Returns:
        {
            'daily': dict, '4h': dict, '1h': dict,
            'bull_modifier': float,   # تعديل سكور الـ CALL
            'bear_modifier': float,   # تعديل سكور الـ PUT
            'summary':       str,
        }
    """
    daily = detect_pd_zones(df1d, lookback=50)
    h4    = detect_pd_zones(df4h, lookback=50)
    h1    = detect_pd_zones(df1h, lookback=50)

    bull_mod = (
        _PD_BULL_WEIGHTS['daily'].get(daily['zone'], 0.0) +
        _PD_BULL_WEIGHTS['4h'].get(h4['zone'], 0.0) +
        _PD_BULL_WEIGHTS['1h'].get(h1['zone'], 0.0)
    )
    bear_mod = (
        _PD_BEAR_WEIGHTS['daily'].get(daily['zone'], 0.0) +
        _PD_BEAR_WEIGHTS['4h'].get(h4['zone'], 0.0) +
        _PD_BEAR_WEIGHTS['1h'].get(h1['zone'], 0.0)
    )

    summary = (
        f"D: {daily['zone']}({daily['position_pct']:.0f}%) | "
        f"4H: {h4['zone']}({h4['position_pct']:.0f}%) | "
        f"1H: {h1['zone']}({h1['position_pct']:.0f}%)"
    )

    return {
        'daily':         daily,
        '4h':            h4,
        '1h':            h1,
        'bull_modifier': round(bull_mod, 3),
        'bear_modifier': round(bear_mod, 3),
        'summary':       summary,
    }


# ─── Dual Structure (Swing + Internal) — LuxAlgo SMC #5 ──────────────────────

def detect_structure_dual(df: pd.DataFrame,
                          swing_size: int = 15,
                          internal_size: int = 5,
                          confluence_filter: bool = True,
                          confluence_tolerance: float = 0.001) -> Dict:
    """
    يكشف البنية السوقية على طبقتين متوازيتين (محاكاة LuxAlgo SMC #5):
      • Swing    : pivot نافذة كبيرة (افتراضي 15) — أحداث نادرة وقوية
      • Internal : pivot نافذة صغيرة (افتراضي 5) — أحداث متكرّرة سريعة

    confluence_filter: عند تطابق internal مع swing ضمن tolerance،
                       نُلغي internal لتجنّب double counting في scoring.

    Returns:
        {
            'swing':    {... كامل مخرجات detect_structure_events بـ swing_size},
            'internal': {... كامل مخرجات detect_structure_events بـ internal_size},
            'confluence': bool,
            'alignment':  'aligned'|'conflicting'|'swing_only'|'internal_only'|'none',
        }
    """
    swing    = detect_structure_events(df, pivot_size=swing_size)
    internal = detect_structure_events(df, pivot_size=internal_size)

    s_bias = swing.get('current_bias')
    i_bias = internal.get('current_bias')

    # تحديد العلاقة بين الطبقتين
    if s_bias and i_bias:
        if s_bias == i_bias:
            alignment, confluence = 'aligned', True
        else:
            alignment, confluence = 'conflicting', False
    elif s_bias and not i_bias:
        alignment, confluence = 'swing_only', False
    elif i_bias and not s_bias:
        alignment, confluence = 'internal_only', False
    else:
        alignment, confluence = 'none', False

    # Confluence filter: إذا internal event يطابق swing event ضمن tolerance
    # → نُسقط internal event لتجنّب double counting في scoring
    if confluence_filter:
        s_price = swing.get('event_price')
        i_price = internal.get('event_price')
        if s_price and i_price:
            denom = max(abs(s_price), abs(i_price), 1e-9)
            if abs(s_price - i_price) / denom < confluence_tolerance:
                internal = {
                    **internal,
                    'last_event':     None,
                    'event_price':    None,
                    'event_bars_ago': None,
                }

    return {
        'swing':      swing,
        'internal':   internal,
        'confluence': confluence,
        'alignment':  alignment,
    }
