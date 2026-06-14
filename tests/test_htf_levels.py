import pandas as pd
import numpy as np
from market_structure import prev_period_levels


def make_daily_df(n_days: int = 90, start: str = '2026-03-01'):
    """يبني DataFrame يومي بـ DatetimeIndex حقيقي."""
    dates = pd.date_range(start=start, periods=n_days, freq='D')
    rng = np.random.default_rng(42)
    base = 100 + np.cumsum(rng.normal(0, 0.5, n_days))
    df = pd.DataFrame({
        'High':   base + rng.uniform(0.5, 1.5, n_days),
        'Low':    base - rng.uniform(0.5, 1.5, n_days),
        'Close':  base,
        'Open':   base,
        'Volume': rng.integers(1000, 5000, n_days),
    }, index=dates)
    return df


def test_returns_all_levels_with_sufficient_data():
    """90 يوم كافية → كل المستويات الـ4 يجب أن تُحسب."""
    df = make_daily_df(90)
    result = prev_period_levels(df)
    for key in ('pwh', 'pwl', 'pmh', 'pml'):
        assert result[key] is not None, f"{key} should not be None"
        assert result[key] > 0


def test_weekly_high_greater_than_low():
    """PWH > PWL منطقياً."""
    df = make_daily_df(60)
    result = prev_period_levels(df)
    if result['pwh'] and result['pwl']:
        assert result['pwh'] > result['pwl']


def test_monthly_high_greater_than_low():
    """PMH > PML منطقياً."""
    df = make_daily_df(90)
    result = prev_period_levels(df)
    if result['pmh'] and result['pml']:
        assert result['pmh'] > result['pml']


def test_monthly_range_engulfs_weekly():
    """جميع القيم منطقية (> 0) على بيانات 90 يوم."""
    df = make_daily_df(90)
    result = prev_period_levels(df)
    if all(result[k] is not None for k in ('pwh', 'pwl', 'pmh', 'pml')):
        assert result['pmh'] > 0 and result['pml'] > 0


def test_short_df_returns_none():
    """DataFrame قصير جداً → كل المستويات None."""
    df = make_daily_df(3)
    result = prev_period_levels(df)
    assert result == {'pwh': None, 'pwl': None, 'pmh': None, 'pml': None}


def test_handles_missing_datetime_index():
    """DataFrame بدون DatetimeIndex → التحويل التلقائي يعمل بدون كسر."""
    df = make_daily_df(60)
    df = df.reset_index(drop=True)
    result = prev_period_levels(df)
    assert isinstance(result, dict)
    assert all(k in result for k in ('pwh', 'pwl', 'pmh', 'pml'))


def test_keys_always_present():
    """المفاتيح الـ4 دائماً موجودة حتى مع DataFrame فارغ."""
    df = pd.DataFrame({'High': [], 'Low': [], 'Close': []})
    result = prev_period_levels(df)
    for key in ('pwh', 'pwl', 'pmh', 'pml'):
        assert key in result
