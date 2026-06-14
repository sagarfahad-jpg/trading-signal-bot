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
