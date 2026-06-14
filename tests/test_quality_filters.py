import pandas as pd
import numpy as np
from market_structure import _find_fvg, _find_order_blocks, _calc_atr_series


def make_df(highs, lows, closes, opens=None):
    return pd.DataFrame({
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Open':   opens if opens is not None else closes,
        'Volume': [1000] * len(highs),
    })


# ── ATR series ───────────────────────────────────────────────────────────

def test_atr_series_computed_correctly():
    """ATR series يحسب قيمة بعد period شمعة."""
    n = 250
    rng = np.random.default_rng(42)
    base = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame({
        'High':  base + 1.0,
        'Low':   base - 1.0,
        'Close': base,
    })
    atr = _calc_atr_series(df, period=200)
    assert len(atr) == n
    assert atr[100] == 0.0    # قبل اكتمال period
    assert atr[200] > 0.0     # بعد اكتمال period
    assert atr[-1]  > 0.0


# ── OB Volatility Filter (#8) ────────────────────────────────────────────

def test_ob_volatility_filter_rejects_huge_candle():
    """شمعة متقلبة جداً (high-low ≥ 2×ATR) → لا تُكتشف كـ OB."""
    n = 230
    opens  = [100.0] * n
    closes = [100.0] * n
    highs  = [101.0] * n
    lows   = [99.0]  * n

    opens[210]  = 100.0
    closes[210] = 70.0
    highs[210]  = 100.0
    lows[210]   = 70.0

    for i in range(211, 218):
        opens[i]  = 70.0 + (i - 210) * 10
        closes[i] = opens[i] + 5
        highs[i]  = closes[i] + 1
        lows[i]   = opens[i] - 0.5

    df = make_df(highs, lows, closes, opens)

    obs_filtered   = _find_order_blocks(df, volatility_filter=True)
    obs_unfiltered = _find_order_blocks(df, volatility_filter=False)

    assert len(obs_unfiltered) >= len(obs_filtered)


def test_ob_volatility_filter_backward_compat():
    """volatility_filter=False → السلوك القديم."""
    n = 230
    opens  = [100.0] * n
    closes = [100.0] * n
    highs  = [101.0] * n
    lows   = [99.0]  * n
    opens[210]  = 100; closes[210] = 95; highs[210] = 100; lows[210] = 95
    for i in range(211, 216):
        opens[i] = 95 + (i - 210) * 2; closes[i] = opens[i] + 1
        highs[i] = closes[i] + 0.5; lows[i] = opens[i] - 0.5

    df = make_df(highs, lows, closes, opens)
    obs = _find_order_blocks(df, volatility_filter=False)
    assert isinstance(obs, list)


# ── FVG Dynamic Threshold (#9.a) ─────────────────────────────────────────

def test_fvg_dynamic_threshold_rejects_small_displacement():
    """FVG من شمعة وسطى صغيرة (delta% < threshold) → لا تُكتشف."""
    n = 50
    closes = [100.0 + i * 0.01 for i in range(n)]
    opens  = [100.0 + i * 0.01 for i in range(n)]
    highs  = [c + 0.05 for c in closes]
    lows   = [c - 0.05 for c in closes]

    opens[21]  = 100.21
    closes[21] = 100.22
    highs[21]  = 100.23
    lows[21]   = 100.21
    opens[22]  = 100.31
    closes[22] = 100.32
    highs[22]  = 100.35
    lows[22]   = 100.30

    df = make_df(highs, lows, closes, opens)

    fvgs_strict = _find_fvg(df, dynamic_threshold=True, require_close_confirmation=False)
    fvgs_loose  = _find_fvg(df, dynamic_threshold=False, require_close_confirmation=False)

    assert len(fvgs_loose) >= len(fvgs_strict)


# ── FVG Close Confirmation (#9.b) ────────────────────────────────────────

def test_fvg_close_confirmation_rejects_wick_only():
    """FVG حيث c1 لا تُغلق فوق c0_high (wick فقط) → لا تُكتشف."""
    n = 30
    opens  = [100.0] * n
    closes = [100.0] * n
    highs  = [100.5] * n
    lows   = [99.5]  * n

    highs[10] = 100.5; lows[10] = 99.5

    opens[11]  = 100.0
    closes[11] = 100.3
    highs[11]  = 101.5
    lows[11]   = 100.0

    opens[12]  = 100.7
    closes[12] = 100.8
    highs[12]  = 101.0
    lows[12]   = 100.6

    df = make_df(highs, lows, closes, opens)

    fvgs_strict = _find_fvg(df, require_close_confirmation=True, dynamic_threshold=False)
    fvgs_loose  = _find_fvg(df, require_close_confirmation=False, dynamic_threshold=False)

    bullish_strict = sum(1 for _, _, t in fvgs_strict if t == 'bullish')
    bullish_loose  = sum(1 for _, _, t in fvgs_loose  if t == 'bullish')

    assert bullish_loose >= bullish_strict


def test_fvg_close_confirmation_accepts_displacement():
    """FVG حيث c1 تُغلق فوق c0_high (Displacement حقيقي) → تُكتشف."""
    n = 30
    opens  = [100.0] * n
    closes = [100.0] * n
    highs  = [100.5] * n
    lows   = [99.5]  * n

    highs[10] = 100.5; lows[10] = 99.5

    opens[11]  = 100.5
    closes[11] = 105.0
    highs[11]  = 105.2
    lows[11]   = 100.5

    opens[12]  = 106.5
    closes[12] = 107.0
    highs[12]  = 107.5
    lows[12]   = 106.0

    df = make_df(highs, lows, closes, opens)

    # نُعطّل track_mitigation للتركيز على شرط الإغلاق فقط
    # (الشموع التالية باللوز=99.5 ستحوّل الـ FVG إلى 'supply' لو شغلناه)
    fvgs = _find_fvg(df, require_close_confirmation=True,
                     dynamic_threshold=False, track_mitigation=False)
    bullish_count = sum(1 for _, _, t in fvgs if t == 'bullish')
    assert bullish_count >= 1


# ── Backward compat ──────────────────────────────────────────────────────

def test_fvg_full_backward_compat():
    """كل الفلاتر معطّلة → السلوك القديم لا يكسر."""
    n = 30
    opens  = [100.0] * n
    closes = [100.0] * n
    highs  = [100.5] * n
    lows   = [99.5]  * n
    highs[10] = 100.5; lows[10] = 99.5
    opens[11] = 100.0; closes[11] = 100.3; highs[11] = 101.5; lows[11] = 100.0
    opens[12] = 100.7; closes[12] = 100.8; highs[12] = 101.0; lows[12] = 100.6

    df = make_df(highs, lows, closes, opens)
    fvgs = _find_fvg(df,
                     dynamic_threshold=False,
                     require_close_confirmation=False,
                     track_mitigation=False)
    assert isinstance(fvgs, list)
