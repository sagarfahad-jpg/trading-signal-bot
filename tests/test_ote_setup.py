import pandas as pd
import numpy as np
from market_structure import detect_ote_setup


def make_df_with_leg(n=80, leg_start=100.0, leg_end=120.0,
                     retrace_to=None, post_leg=None):
    """يبني df بـ leg دافعة + retracement اختياري + شموع لاحقة."""
    rng = np.random.default_rng(42)
    closes = []
    closes += list(leg_start + rng.normal(0, 0.3, 15))
    closes += list(np.linspace(leg_start, leg_end, 25))
    if retrace_to is not None:
        closes += list(np.linspace(leg_end, retrace_to, 20))
        if post_leg is not None:
            closes += list(np.linspace(retrace_to, post_leg, 20))
        else:
            closes += list(retrace_to + rng.normal(0, 0.3, 20))
    else:
        closes += list(leg_end + rng.normal(0, 0.3, 20))

    closes = closes[:n]
    while len(closes) < n:
        closes.append(closes[-1])

    arr = np.array(closes)
    return pd.DataFrame({
        'High':   arr + 0.5,
        'Low':    arr - 0.5,
        'Close':  arr,
        'Open':   arr,
        'Volume': [1000] * n,
    })


def fake_ms_dual(bias='bullish', event='BOS',
                 strong_low=100.0, strong_high=None,
                 trailing_top=120.0, trailing_bottom=None):
    """يبني ms_dual صناعي بكل المفاتيح المطلوبة."""
    if bias == 'bullish':
        strength = {
            'strong_low':      strong_low,
            'strong_high':     None,
            'weak_low':        None,
            'weak_high':       None,
            'trailing_top':    trailing_top,
            'trailing_bottom': strong_low,
        }
    else:
        strength = {
            'strong_low':      None,
            'strong_high':     strong_high or 120.0,
            'weak_low':        None,
            'weak_high':       None,
            'trailing_top':    strong_high or 120.0,
            'trailing_bottom': trailing_bottom or 100.0,
        }
    return {
        'swing': {
            'current_bias': bias,
            'last_event':   event,
            'strength':     strength,
        },
        'internal':   {},
        'confluence': False,
        'alignment':  'swing_only',
    }


def test_ote_setup_detected_bullish_leg():
    """leg صاعدة + BOS → OTE setup يُكتشف."""
    df = make_df_with_leg(leg_start=100, leg_end=120, retrace_to=114)
    ms = fake_ms_dual(bias='bullish', event='BOS', strong_low=100, trailing_top=120)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=0.5)

    assert result['has_setup'] is True
    assert result['direction'] == 'bullish'
    assert result['leg_start'] == 100.0
    assert result['leg_end'] == 120.0
    assert result['leg_length'] == 20.0
    assert abs(result['ote_zone_high'] - 107.64) < 0.1
    assert abs(result['ote_zone_low']  - 104.2)  < 0.1
    assert abs(result['golden_pocket'] - 105.9)  < 0.1


def test_in_ote_when_price_inside_zone():
    """السعر داخل OTE zone → in_ote=True."""
    df = make_df_with_leg(leg_start=100, leg_end=120, retrace_to=107)
    ms = fake_ms_dual(bias='bullish', event='BOS', strong_low=100, trailing_top=120)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=0.5)
    assert result['in_ote'] is True


def test_in_golden_when_price_at_golden_pocket():
    """السعر عند Golden Pocket → in_golden=True."""
    df = make_df_with_leg(leg_start=100, leg_end=120, retrace_to=105.9)
    ms = fake_ms_dual(bias='bullish', event='BOS', strong_low=100, trailing_top=120)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=0.5, golden_tolerance=0.01)
    assert result['in_golden'] is True


def test_inverse_active_when_price_breaks_79():
    """السعر اخترق 79% → inverse_active=True."""
    df = make_df_with_leg(leg_start=100, leg_end=120, retrace_to=102)
    ms = fake_ms_dual(bias='bullish', event='BOS', strong_low=100, trailing_top=120)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=0.5)
    assert result['inverse_active'] is True


def test_no_setup_when_event_is_choch_only():
    """CHoCH وحدها → لا OTE setup."""
    df = make_df_with_leg(leg_start=100, leg_end=120, retrace_to=107)
    ms = fake_ms_dual(bias='bullish', event='CHoCH', strong_low=100, trailing_top=120)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=0.5)
    assert result['has_setup'] is False


def test_no_setup_when_leg_too_short():
    """leg أقصر من min_leg_atr_mult × ATR → لا setup."""
    df = make_df_with_leg(leg_start=100, leg_end=101, retrace_to=100.5)
    ms = fake_ms_dual(bias='bullish', event='BOS', strong_low=100, trailing_top=101)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=5.0)
    assert result['has_setup'] is False


def test_no_setup_when_bias_neutral():
    """bias neutral → لا setup."""
    df = make_df_with_leg(leg_start=100, leg_end=120, retrace_to=107)
    ms_neutral = {
        'swing': {'current_bias': None, 'last_event': None, 'strength': {}},
        'internal': {}, 'confluence': False, 'alignment': 'none',
    }

    result = detect_ote_setup(df, ms_neutral, min_leg_atr_mult=0.5)
    assert result['has_setup'] is False


def test_bearish_ote_setup():
    """leg هابطة + BOS bearish → OTE setup هابط."""
    n = 80
    rng = np.random.default_rng(42)
    closes = []
    closes += list(120 + rng.normal(0, 0.3, 15))
    closes += list(np.linspace(120, 100, 25))
    closes += list(np.linspace(100, 113, 20))
    closes += list(113 + rng.normal(0, 0.3, 20))
    closes = closes[:n]

    arr = np.array(closes)
    df = pd.DataFrame({
        'High': arr + 0.5, 'Low': arr - 0.5,
        'Close': arr, 'Open': arr, 'Volume': [1000]*n
    })
    ms = fake_ms_dual(bias='bearish', event='BOS', strong_high=120, trailing_bottom=100)

    result = detect_ote_setup(df, ms, min_leg_atr_mult=0.5)
    assert result['has_setup'] is True
    assert result['direction'] == 'bearish'
    assert result['leg_start'] == 120.0
    assert result['leg_end']   == 100.0


def test_keys_always_present():
    """كل المفاتيح المطلوبة دائماً موجودة."""
    df = make_df_with_leg()
    ms = fake_ms_dual()
    result = detect_ote_setup(df, ms)

    required = ['has_setup', 'direction', 'leg_start', 'leg_end', 'leg_length',
                'leg_event', 'ote_zone_low', 'ote_zone_high', 'golden_pocket',
                'in_ote', 'in_golden', 'inverse_active', 'inverse_zone_low',
                'inverse_zone_high', 'in_inverse', 'atr_used']
    for key in required:
        assert key in result


def test_empty_df_returns_safe_default():
    """df فارغ → empty result بلا exceptions."""
    df = pd.DataFrame({'High': [], 'Low': [], 'Close': [], 'Open': [], 'Volume': []})
    result = detect_ote_setup(df, fake_ms_dual())
    assert result['has_setup'] is False
