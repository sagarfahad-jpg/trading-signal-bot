import pandas as pd
from market_structure import detect_structure_events


def make_df(highs, lows, closes):
    return pd.DataFrame({
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Open':   closes,
        'Volume': [1000] * len(highs),
    })


def test_strong_high_in_bearish_trend():
    """
    pivot_low(bar 3) ثم pivot_high(bar 8) ثم close يكسر pivot_low → bearish.
    النتيجة المتوقّعة: strong_high (القمة المرجعية التي لم تُكسر) موجود.
    """
    highs  = [105, 103, 101,  99,  101, 103, 105, 107, 109, 105, 100, 95,  90,  85]
    lows   = [103, 101,  99,  97,   99, 101, 103, 105, 107, 103,  98, 93,  88,  83]
    closes = [104, 102, 100,  98,  100, 102, 104, 106, 108, 104,  99, 94,  89,  84]
    df = make_df(highs, lows, closes)

    result = detect_structure_events(df, pivot_size=2)

    assert 'strength' in result
    assert result['current_bias'] == 'bearish'
    assert result['strength']['strong_high'] is not None
    assert result['strength']['strong_low']  is None
    assert result['strength']['weak_low']    is not None
    assert result['strength']['weak_high']   is None


def test_strong_low_in_bullish_trend():
    """
    pivot_high(bar 3) ثم pivot_low(bar 8) ثم close يكسر pivot_high → bullish.
    النتيجة المتوقّعة: strong_low (القاع المرجعي الذي لم يُكسر) موجود.
    """
    highs  = [95,  97,  99,  101, 99,  97,  95,  93,  91,  95,  100, 105, 110, 115]
    lows   = [93,  95,  97,   99, 97,  95,  93,  91,  89,  93,   98, 103, 108, 113]
    closes = [94,  96,  98,  100, 98,  96,  94,  92,  90,  94,   99, 104, 109, 114]
    df = make_df(highs, lows, closes)

    result = detect_structure_events(df, pivot_size=2)

    assert result['current_bias'] == 'bullish'
    assert result['strength']['strong_low']  is not None
    assert result['strength']['strong_high'] is None
    assert result['strength']['weak_high']   is not None
    assert result['strength']['weak_low']    is None


def test_neutral_no_events():
    """سعر مسطّح → no events → كل المستويات None."""
    highs  = [100] * 20
    lows   = [98]  * 20
    closes = [99]  * 20
    df = make_df(highs, lows, closes)

    result = detect_structure_events(df, pivot_size=5)

    assert result['current_bias'] is None
    assert result['strength']['strong_high'] is None
    assert result['strength']['strong_low']  is None
    assert result['strength']['weak_high']   is None
    assert result['strength']['weak_low']    is None
    # لكن trailing_top/bottom يجب أن يكونا موجودَين
    assert result['strength']['trailing_top']    > 0
    assert result['strength']['trailing_bottom'] > 0


def test_strength_block_always_present():
    """التأكد أن مفتاح 'strength' موجود في جميع الحالات."""
    df = make_df([100]*15, [98]*15, [99]*15)
    result = detect_structure_events(df, pivot_size=5)
    assert 'strength' in result
    assert isinstance(result['strength'], dict)
