import pandas as pd
from market_structure import _find_fvg, _find_order_blocks


def make_df(highs, lows, closes, opens=None):
    return pd.DataFrame({
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Open':   opens if opens is not None else closes,
        'Volume': [1000] * len(highs),
    })


# ── FVG Tests ────────────────────────────────────────────────────────────

def test_fvg_bullish_active_not_mitigated():
    """FVG صاعدة لم تُملأ → نوعها يبقى 'bullish'."""
    highs  = [100, 101, 102, 105, 106, 107, 108, 109, 110]
    lows   = [98,  99,  100, 103, 104, 105, 106, 107, 108]
    closes = [99,  100, 101, 104, 105, 106, 107, 108, 109]
    df = make_df(highs, lows, closes)

    result = _find_fvg(df, track_mitigation=True)
    bullish_count = sum(1 for _, _, t in result if t == 'bullish')
    assert bullish_count >= 1, f"Expected bullish FVG, got: {result}"


def test_fvg_bullish_becomes_supply_when_mitigated():
    """FVG صاعدة ثم السعر نزل تحتها → نوعها يتحول 'supply'."""
    highs  = [100, 101, 102, 105, 106, 107, 108, 105, 100, 95, 90]
    lows   = [98,  99,  100, 103, 104, 105, 106, 100, 95,  90, 85]
    closes = [99,  100, 101, 104, 105, 106, 107, 102, 97,  92, 87]
    df = make_df(highs, lows, closes)

    result = _find_fvg(df, track_mitigation=True)
    supply_count = sum(1 for _, _, t in result if t == 'supply')
    assert supply_count >= 1, f"Expected supply (inverted FVG), got: {result}"


def test_fvg_bearish_becomes_demand_when_mitigated():
    """FVG هابطة ثم السعر صعد فوقها → نوعها يتحول 'demand'."""
    highs  = [110, 109, 108, 105, 104, 103, 102, 105, 110, 115, 120]
    lows   = [108, 107, 106, 103, 102, 101, 100, 103, 108, 113, 118]
    closes = [109, 108, 107, 104, 103, 102, 101, 104, 109, 114, 119]
    df = make_df(highs, lows, closes)

    result = _find_fvg(df, track_mitigation=True)
    demand_count = sum(1 for _, _, t in result if t == 'demand')
    assert demand_count >= 1, f"Expected demand (inverted FVG), got: {result}"


def test_fvg_backward_compat_no_mitigation():
    """track_mitigation=False → سلوك قديم: كل الـ FVGs كـ 'bullish/bearish'."""
    highs  = [100, 101, 102, 105, 106, 107, 108, 105, 100, 95, 90]
    lows   = [98,  99,  100, 103, 104, 105, 106, 100, 95,  90, 85]
    closes = [99,  100, 101, 104, 105, 106, 107, 102, 97,  92, 87]
    df = make_df(highs, lows, closes)

    result = _find_fvg(df, track_mitigation=False)
    types = set(t for _, _, t in result)
    assert 'demand' not in types
    assert 'supply' not in types


# ── OB Tests ─────────────────────────────────────────────────────────────

def test_ob_bullish_active_not_mitigated():
    """OB صاعد لم يُخترق → نوعه يبقى 'bullish'."""
    n = 15
    opens  = [100] * n
    closes = [100] * n
    highs  = [101] * n
    lows   = [99]  * n

    opens[2]  = 100; closes[2] = 95; highs[2] = 100; lows[2] = 95
    for i in range(3, 8):
        opens[i] = 95 + (i - 2) * 3
        closes[i] = 95 + (i - 2) * 3 + 2
        highs[i] = closes[i] + 1
        lows[i] = opens[i] - 0.5
    for i in range(8, n):
        opens[i] = 115; closes[i] = 116; highs[i] = 117; lows[i] = 114

    df = make_df(highs, lows, closes, opens)
    result = _find_order_blocks(df, track_mitigation=True)
    bullish_count = sum(1 for _, _, t in result if t == 'bullish')
    assert bullish_count >= 1, f"Expected bullish OB, got: {result}"


def test_ob_bullish_becomes_breaker_bear_when_mitigated():
    """OB صاعد ثم السعر نزل تحته → 'breaker_bear'."""
    n = 20
    opens  = [100.0] * n
    closes = [100.0] * n
    highs  = [101.0] * n
    lows   = [99.0]  * n

    opens[2]  = 100; closes[2] = 95; highs[2] = 100; lows[2] = 95
    for i in range(3, 8):
        opens[i] = 95.0 + (i - 2) * 3
        closes[i] = opens[i] + 2
        highs[i] = closes[i] + 1
        lows[i] = opens[i] - 0.5
    for i in range(8, n):
        opens[i] = 90 - (i - 8); closes[i] = opens[i] - 1
        highs[i] = opens[i] + 0.5; lows[i] = closes[i] - 1

    df = make_df(highs, lows, closes, opens)
    result = _find_order_blocks(df, track_mitigation=True)
    breaker_count = sum(1 for _, _, t in result if t == 'breaker_bear')
    assert breaker_count >= 1, f"Expected breaker_bear, got: {result}"


def test_ob_backward_compat_no_mitigation():
    """track_mitigation=False → فقط 'bullish/bearish' في النتائج."""
    n = 15
    opens  = [100.0] * n; closes = [100.0] * n
    highs  = [101.0] * n; lows   = [99.0]  * n
    opens[2] = 100; closes[2] = 95; highs[2] = 100; lows[2] = 95
    for i in range(3, 8):
        opens[i] = 95.0 + (i - 2) * 3; closes[i] = opens[i] + 2
        highs[i] = closes[i] + 1;       lows[i] = opens[i] - 0.5
    for i in range(8, n):
        opens[i] = 85; closes[i] = 84; highs[i] = 86; lows[i] = 83

    df = make_df(highs, lows, closes, opens)
    result = _find_order_blocks(df, track_mitigation=False)
    types = set(t for _, _, t in result)
    assert 'breaker_bull' not in types
    assert 'breaker_bear' not in types


def test_no_breaker_during_confirmation_window():
    """OB لا يُلغى داخل نافذة الـ 5 شموع للتأكيد."""
    n = 15
    opens  = [100.0] * n; closes = [100.0] * n
    highs  = [101.0] * n; lows   = [99.0]  * n
    opens[2] = 100; closes[2] = 95; highs[2] = 100; lows[2] = 95
    lows[3] = 94.5
    for i in range(4, 8):
        opens[i] = 95.0 + (i - 2) * 3; closes[i] = opens[i] + 2
        highs[i] = closes[i] + 1
        lows[i]  = opens[i] - 0.5
    for i in range(8, n):
        opens[i] = 115; closes[i] = 116; highs[i] = 117; lows[i] = 114

    df = make_df(highs, lows, closes, opens)
    result = _find_order_blocks(df, track_mitigation=True)
    bullish_count = sum(1 for _, _, t in result if t == 'bullish')
    breaker_count = sum(1 for _, _, t in result if t == 'breaker_bear')
    if bullish_count > 0:
        assert breaker_count == 0, f"OB shouldn't break inside confirmation window"
