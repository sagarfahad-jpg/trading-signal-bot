import pandas as pd
import numpy as np
from market_structure import detect_equal_levels


def make_df(highs, lows, closes, opens=None):
    return pd.DataFrame({
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Open':   opens if opens is not None else closes,
        'Volume': [1000] * len(highs),
    })


def test_eqh_detected_when_two_highs_match():
    """قمتان متطابقتان (فارق < 0.1×ATR) → EQH يُكشف."""
    # ramp بسيط — يضمن عدم تكوّن pivots إلا عند الحقن
    n = 80
    highs  = [100.0 + i * 0.001 for i in range(n)]
    lows   = [99.0  + i * 0.001 for i in range(n)]
    closes = [99.5  + i * 0.001 for i in range(n)]

    # قمَّتَان محقونتان: i=40 و i=60 — pivot شعاع واضح
    highs[40] = 110.0
    highs[60] = 110.05

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1, atr_period=50)

    assert result['eqh_level'] is not None, f"Expected EQH, got: {result}"
    assert 109.5 < result['eqh_level'] < 110.5


def test_no_eqh_when_highs_differ_significantly():
    """قمتان متباعدتان (فارق كبير) → لا EQH مزيّف لتلك القمم."""
    n = 80
    base = [100.0] * n
    highs  = list(base)
    lows   = [99.0] * n
    closes = list(base)

    highs[20] = 105.0
    highs[50] = 110.0

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1)

    if result['eqh_level'] is not None:
        assert abs(result['eqh_level'] - 107.5) > 1.0


def test_eql_detected_when_two_lows_match():
    """قاعان متطابقان → EQL يُكشف."""
    n = 80
    highs  = [101.0 + i * 0.001 for i in range(n)]
    lows   = [99.0  + i * 0.001 for i in range(n)]
    closes = [100.0 + i * 0.001 for i in range(n)]

    # قاعَيْن عميقَيْن: i=40 و i=60
    lows[40] = 90.0
    lows[60] = 90.05

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1, atr_period=50)

    assert result['eql_level'] is not None, f"Expected EQL, got: {result}"
    assert 89.5 < result['eql_level'] < 90.5


def test_short_df_returns_empty():
    """DataFrame قصير → كل القيم None."""
    df = make_df([100]*5, [98]*5, [99]*5)
    result = detect_equal_levels(df, pivot_size=3)

    assert result['eqh_level'] is None
    assert result['eql_level'] is None
    assert result['eqh_bars_ago'] is None
    assert result['eql_bars_ago'] is None


def test_keys_always_present():
    """المفاتيح الـ5 دائماً موجودة."""
    df = make_df([100]*50, [98]*50, [99]*50)
    result = detect_equal_levels(df)

    for key in ('eqh_level', 'eqh_bars_ago', 'eql_level', 'eql_bars_ago', 'atr_used'):
        assert key in result


# ── Sweep state tracking (architectural consistency with #6+#7) ────────────

def _eqh_setup(n: int = 100):
    """Helper: قمَّتَان متطابقتان عند 110 على ramp ثابت."""
    highs  = [100.0 + i * 0.001 for i in range(n)]
    lows   = [99.0  + i * 0.001 for i in range(n)]
    closes = [99.5  + i * 0.001 for i in range(n)]
    highs[40] = 110.0
    highs[60] = 110.05
    return highs, lows, closes


def _eql_setup(n: int = 100):
    """Helper: قاعَيْن متطابقَيْن عند 90 على ramp ثابت."""
    highs  = [101.0 + i * 0.001 for i in range(n)]
    lows   = [99.0  + i * 0.001 for i in range(n)]
    closes = [100.0 + i * 0.001 for i in range(n)]
    lows[40] = 90.0
    lows[60] = 90.05
    return highs, lows, closes


def test_eqh_marked_swept_when_price_exceeds():
    """EQH مكتشفة + شموع لاحقة تخترق المستوى → eqh_swept=True."""
    n = 100
    highs, lows, closes = _eqh_setup(n)
    # شموع لاحقة تخترق فوق 110 (sweep) — ramp تصاعدي يمنع تكوّن pivots جديدة
    for j in range(70, 80):
        highs[j] = 111.0 + (j - 70) * 0.1

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1, atr_period=50)

    assert result['eqh_level'] is not None
    assert result['eqh_swept'] is True, f"Expected swept=True, got: {result}"


def test_eqh_not_swept_when_price_stays_below():
    """EQH مكتشفة + السعر لم يتجاوز → eqh_swept=False."""
    n = 100
    highs, lows, closes = _eqh_setup(n)
    # شموع لاحقة تبقى تحت 110 (لا sweep)
    for j in range(70, 80):
        highs[j] = 105.0   # أقل من eqh_level

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1, atr_period=50)

    assert result['eqh_level'] is not None
    assert result['eqh_swept'] is False, f"Expected swept=False, got: {result}"


def test_eql_marked_swept_when_price_drops_below():
    """EQL مكتشفة + شموع لاحقة تنزل تحت المستوى → eql_swept=True."""
    n = 100
    highs, lows, closes = _eql_setup(n)
    # ramp تنازلي لمنع تكوّن pivots جديدة
    for j in range(70, 80):
        lows[j] = 89.0 - (j - 70) * 0.1

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1, atr_period=50)

    assert result['eql_level'] is not None
    assert result['eql_swept'] is True


def test_track_sweep_disabled():
    """track_sweep=False → swept دائماً False (backward compat)."""
    n = 100
    highs, lows, closes = _eqh_setup(n)
    for j in range(70, 80):
        highs[j] = 115.0 + (j - 70) * 0.1  # sweep واضح لكن track_sweep=False

    df = make_df(highs, lows, closes)
    result = detect_equal_levels(df, pivot_size=3, threshold_atr_mult=0.1,
                                 atr_period=50, track_sweep=False)

    assert result['eqh_swept'] is False
    assert result['eql_swept'] is False
