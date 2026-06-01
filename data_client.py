"""
Data Client — يجلب البيانات من Alpaca (لحظي) مع fallback لـ yfinance.

Alpaca IEX feed: مجاني + شبه لحظي (ثوانٍ وليس 15 دقيقة)
Alpaca SIP feed: لحظي 100% (يتطلب حساب ممول أو اشتراك $29/شهر)
yfinance:        احتياطي فقط (VIX + Options chain)
"""

from __future__ import annotations
import pytz, pandas as pd, time as _time
from datetime import datetime, timedelta
from typing import Optional

import config

# ─── Cache للبيانات بطيئة التغيير ────────────────────────────────────────────
_bars_cache: dict = {}
_CACHE_TTL = {"1h": 600, "4h": 900, "1d": 1800}  # ثواني

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
    "7d": 7, "10d": 10, "14d": 14, "15d": 15,
    "30d": 32, "60d": 63, "90d": 93,
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
        "4h":  TimeFrame(4, TimeFrameUnit.Hour),
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
    ttl = _CACHE_TTL.get(interval, 0)
    if ttl:
        key = (symbol, interval, period)
        entry = _bars_cache.get(key)
        if entry and _time.time() - entry[1] < ttl:
            return entry[0].copy()

    if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
        df = _fetch_alpaca(symbol, interval, period)
        if df is not None and not df.empty:
            if ttl:
                _bars_cache[(symbol, interval, period)] = (df, _time.time())
            return df

    df = _fetch_yfinance(symbol, interval, period)
    if ttl and not df.empty:
        _bars_cache[(symbol, interval, period)] = (df, _time.time())
    return df


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


# ─── Options (Alpaca) ──────────────────────────────────────────────────────────

_OPT_HEADERS = {
    "APCA-API-KEY-ID":     config.ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
}


def get_option_alpaca(symbol: str, direction: str, price: float, expiry_str: str):
    """
    يجلب أقرب عقد للسعر من Alpaca + سعره الحقيقي (bid/ask mid).
    expiry_str: 'YYYY-MM-DD'
    يُرجع dict: {expiry, strike, option_price} أو None عند الفشل.
    """
    if not (config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY):
        return None
    import requests
    opt_type = "call" if direction == "call" else "put"
    try:
        # 1) قائمة العقود لذلك الانتهاء والنوع
        r = requests.get(
            "https://paper-api.alpaca.markets/v2/options/contracts",
            headers=_OPT_HEADERS,
            params={
                "underlying_symbols": symbol,
                "expiration_date":    expiry_str,
                "type":               opt_type,
                "limit":              200,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        contracts = r.json().get("option_contracts", [])
        if not contracts:
            return None

        # 2) أقرب strike للسعر
        best = min(contracts, key=lambda c: abs(float(c["strike_price"]) - price))
        opt_sym = best["symbol"]
        strike  = float(best["strike_price"])

        # 3) آخر سعر (bid/ask)
        rq = requests.get(
            "https://data.alpaca.markets/v1beta1/options/quotes/latest",
            headers=_OPT_HEADERS,
            params={"symbols": opt_sym, "feed": "indicative"},
            timeout=10,
        )
        option_price = 0.0
        if rq.status_code == 200:
            q = rq.json().get("quotes", {}).get(opt_sym, {})
            bid = float(q.get("bp", 0) or 0)
            ask = float(q.get("ap", 0) or 0)
            if bid > 0 and ask > 0:
                option_price = round((bid + ask) / 2, 2)

        return {
            "expiry":       expiry_str.replace("-", ""),
            "strike":       strike,
            "option_price": option_price,
        }
    except Exception as exc:
        print(f"  [alpaca opt] {symbol}: {exc}")
        return None
