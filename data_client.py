"""
Data Client — يجلب البيانات من Alpaca (لحظي) مع fallback لـ yfinance.

Alpaca IEX feed: مجاني + شبه لحظي (ثوانٍ وليس 15 دقيقة)
Alpaca SIP feed: لحظي 100% (يتطلب حساب ممول أو اشتراك $29/شهر)
yfinance:        احتياطي فقط (VIX + Options chain)
"""

from __future__ import annotations
import pytz, pandas as pd
from datetime import datetime, timedelta
from typing import Optional

import config

# ─── Alpaca client (singleton) ────────────────────────────────────────────────

_alpaca_client = None

def _get_alpaca():
    global _alpaca_client
    if _alpaca_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        _alpaca_client = StockHistoricalDataClient(
            config.ALPACA_API_KEY,
            config.ALPACA_SECRET_KEY,
        )
    return _alpaca_client


# ─── Period → days ────────────────────────────────────────────────────────────

_PERIOD_DAYS = {
    "1d": 1,  "2d": 2,  "3d": 3,  "5d": 5,
    "7d": 7, "10d": 10, "15d": 15, "30d": 32,
    "60d": 63, "90d": 93,
}

# ─── Interval → Alpaca TimeFrame ──────────────────────────────────────────────

def _to_timeframe(interval: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    mapping = {
        "1m":  TimeFrame.Minute,
        "5m":  TimeFrame(5,  TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h":  TimeFrame.Hour,
        "1d":  TimeFrame.Day,
    }
    return mapping.get(interval, TimeFrame(5, TimeFrameUnit.Minute))


# ─── Main fetch function ───────────────────────────────────────────────────────

def get_bars(symbol: str, interval: str = "5m", period: str = "2d") -> pd.DataFrame:
    """
    يجلب شموع OHLCV من Alpaca.
    إذا فشل → يرجع لـ yfinance تلقائياً.
    الأعمدة تطابق yfinance: Open High Low Close Volume
    """
    if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
        df = _fetch_alpaca(symbol, interval, period)
        if df is not None and not df.empty:
            return df

    return _fetch_yfinance(symbol, interval, period)


def _fetch_alpaca(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    """يجلب من Alpaca — يُرجع None عند الفشل."""
    try:
        from alpaca.data.requests import StockBarsRequest

        days  = _PERIOD_DAYS.get(period, 30)
        tf    = _to_timeframe(interval)
        end   = datetime.now(pytz.UTC)
        start = end - timedelta(days=days + 3)   # +3 للعطل والفواصل

        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            feed="iex",     # مجاني وشبه لحظي — غيّره لـ "sip" مع الاشتراك المدفوع
        )
        bars = _get_alpaca().get_stock_bars(req)
        df   = bars.df

        if df.empty:
            return None

        # إذا كان MultiIndex (طلب متعدد الأصول) نأخذ الأصل المطلوب فقط
        if isinstance(df.index, pd.MultiIndex):
            if symbol in df.index.get_level_values("symbol"):
                df = df.xs(symbol, level="symbol")
            else:
                return None

        # تحويل Timezone إلى ET
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("America/New_York")

        # إعادة تسمية الأعمدة لتطابق yfinance
        df = df.rename(columns={
            "open": "Open", "high": "High",
            "low":  "Low",  "close": "Close",
            "volume": "Volume",
        })

        cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
        return df[cols].copy()

    except Exception as exc:
        print(f"  [alpaca] {symbol}/{interval}: {exc}")
        return None


def _fetch_yfinance(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """Fallback — yfinance (بيانات متأخرة 15 دقيقة)."""
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        return df
    except Exception:
        return pd.DataFrame()


# ─── Batch fetch (للـ Dashboard — أسرع) ──────────────────────────────────────

def get_bars_batch(symbols: list, interval: str = "5m", period: str = "2d") -> dict[str, pd.DataFrame]:
    """
    يجلب بيانات عدة أصول في طلب واحد — أسرع بكثير من طلب منفرد لكل أصل.
    يُرجع dict: {symbol: DataFrame}
    """
    results = {}

    if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
        try:
            from alpaca.data.requests import StockBarsRequest

            days  = _PERIOD_DAYS.get(period, 30)
            tf    = _to_timeframe(interval)
            end   = datetime.now(pytz.UTC)
            start = end - timedelta(days=days + 3)

            req  = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=tf,
                start=start,
                end=end,
                feed="iex",
            )
            bars = _get_alpaca().get_stock_bars(req)
            df_all = bars.df

            if not df_all.empty and isinstance(df_all.index, pd.MultiIndex):
                for sym in symbols:
                    try:
                        df = df_all.xs(sym, level="symbol").copy()
                        if df.index.tz is None:
                            df.index = df.index.tz_localize("UTC")
                        df.index = df.index.tz_convert("America/New_York")
                        df = df.rename(columns={
                            "open":"Open","high":"High",
                            "low":"Low","close":"Close","volume":"Volume",
                        })
                        cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
                        results[sym] = df[cols]
                    except Exception:
                        pass

        except Exception as exc:
            print(f"  [alpaca batch] {exc}")

    # Fallback للأصول التي فشل جلبها
    missing = [s for s in symbols if s not in results]
    for sym in missing:
        results[sym] = _fetch_yfinance(sym, interval, period)

    return results
