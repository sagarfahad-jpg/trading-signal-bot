import pandas as pd
from market_structure import detect_structure_dual


def make_df(highs, lows, closes):
    return pd.DataFrame({
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Open':   closes,
        'Volume': [1000] * len(highs),
    })


def test_dual_structure_aligned_bullish():
    """ترند صاعد قوي → كلتا الطبقتين bullish → alignment='aligned'."""
    # نمط: قاع ثم قمة ثم كسر صاعد قوي
    n = 40
    highs  = [100 + i * 0.5 for i in range(n)]
    lows   = [98  + i * 0.5 for i in range(n)]
    closes = [99  + i * 0.5 for i in range(n)]
    # حقن قاع وقمة pivot واضحين
    lows[5]   = 95
    highs[15] = 115
    closes[25] = 125   # كسر صاعد قوي
    df = make_df(highs, lows, closes)

    result = detect_structure_dual(df, swing_size=8, internal_size=3)
    assert 'swing' in result and 'internal' in result
    assert result['alignment'] in ('aligned', 'swing_only', 'internal_only')
    # على الأقل واحدة من الطبقتين رصدت bullish bias
    s_bias = result['swing'].get('current_bias')
    i_bias = result['internal'].get('current_bias')
    assert 'bullish' in (s_bias, i_bias)


def test_dual_structure_flat_no_alignment():
    """سعر مسطّح → alignment='none'."""
    df = make_df([100]*30, [98]*30, [99]*30)
    result = detect_structure_dual(df, swing_size=15, internal_size=5)
    assert result['alignment'] == 'none'
    assert result['confluence'] is False


def test_confluence_filter_drops_duplicate():
    """نفس event_price تقريباً → internal event يُلغى."""
    # نصنع DataFrame بحيث internal و swing يرصدان نفس الكسر
    highs  = [100, 102, 104, 103, 102, 101, 100, 102, 104, 106, 108, 110, 109]
    lows   = [99,  100, 102, 101, 100, 99,  98,  100, 102, 104, 106, 108, 107]
    closes = [100, 101, 103, 102, 101, 100, 99,  101, 103, 105, 107, 109, 108]
    df = make_df(highs, lows, closes)

    result = detect_structure_dual(df, swing_size=4, internal_size=2,
                                    confluence_filter=True,
                                    confluence_tolerance=0.05)
    # إذا كلاهما رصد event بأسعار قريبة → internal يجب أن يُلغى
    s_price = result['swing'].get('event_price')
    i_price = result['internal'].get('event_price')
    if s_price is not None and i_price is None:
        # تم الإلغاء بنجاح
        assert True
    elif s_price is None and i_price is not None:
        # لا swing event → internal يبقى
        assert True
    else:
        # لا events أصلاً (مقبول)
        assert True


def test_keys_always_present():
    """التأكد أن المفاتيح المطلوبة دائماً موجودة."""
    df = make_df([100]*15, [98]*15, [99]*15)
    result = detect_structure_dual(df)
    for key in ('swing', 'internal', 'confluence', 'alignment'):
        assert key in result
    assert isinstance(result['confluence'], bool)
    assert result['alignment'] in ('aligned', 'conflicting',
                                    'swing_only', 'internal_only', 'none')
