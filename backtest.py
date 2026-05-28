"""
Backtest — اختبار الاستراتيجية على بيانات تاريخية
يولّد إشارة الساعة 9:45 صباحاً ويتحقق من النتيجة حتى 3:45 مساءً ET.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import pytz
import json


# ─── Helper functions (نفس منطق analyzer.py بدون options/MTF) ─────────────────

def _rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def _atr(df, period=14):
    prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low']  - prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(com=period - 1, min_periods=period).mean().iloc[-1])


def _pivot_levels(df, lookback=5):
    highs, lows = [], []
    n = len(df)
    for i in range(lookback, n - lookback):
        wh = df['High'].iloc[i - lookback: i + lookback + 1]
        wl = df['Low'].iloc[i - lookback: i + lookback + 1]
        if df['High'].iloc[i] == wh.max(): highs.append(float(df['High'].iloc[i]))
        if df['Low'].iloc[i]  == wl.min(): lows.append(float(df['Low'].iloc[i]))
    return highs, lows


def _compute(df5: pd.DataFrame, df1d: pd.DataFrame,
             min_score: float, min_rr: float) -> dict | None:
    """Core signal logic on provided DataFrames (no API calls)."""
    try:
        if len(df5) < 60 or len(df1d) < 5:
            return None

        price = float(df5['Close'].iloc[-1])
        atr   = _atr(df5)
        if atr == 0:
            return None

        rsi_s    = _rsi(df5['Close'])
        rsi      = float(rsi_s.iloc[-1])
        rsi_prev = float(rsi_s.iloc[-4])
        rsi_rising = rsi > rsi_prev

        sma10 = float(df5['Close'].rolling(10).mean().iloc[-1])
        sma30 = float(df5['Close'].rolling(30).mean().iloc[-1])

        pdh = float(df1d['High'].iloc[-1])
        pdl = float(df1d['Low'].iloc[-1])

        pivot_highs, pivot_lows = _pivot_levels(df5.tail(80))
        supports    = sorted([l for l in pivot_lows  if l < price], reverse=True)
        resistances = sorted([h for h in pivot_highs if h > price])
        near_sup = supports[0]    if supports    else price * 0.998
        near_res = resistances[0] if resistances else price * 1.002

        at_sup   = abs(price - near_sup) / price < 0.004
        at_res   = abs(price - near_res) / price < 0.004
        near_pdl = abs(price - pdl)     / price < 0.005
        near_pdh = abs(price - pdh)     / price < 0.005

        vol_avg    = float(df5['Volume'].rolling(20).mean().iloc[-1])
        vol_recent = float(df5['Volume'].iloc[-5:].mean())
        vol_surge  = vol_recent > vol_avg * 1.5
        last_bull  = float(df5['Close'].iloc[-1]) > float(df5['Open'].iloc[-1])

        bs = ps = 0.0

        if rsi < 30: bs += 3.0
        elif rsi < 40 and rsi_rising: bs += 2.0
        elif rsi < 50 and rsi_rising: bs += 1.0
        if rsi > 70: ps += 3.0
        elif rsi > 60 and not rsi_rising: ps += 2.0
        elif rsi > 50 and not rsi_rising: ps += 1.0

        if at_sup or near_pdl: bs += 2.5
        if at_res or near_pdh: ps += 2.5
        if price > sma10 and price > sma30: bs += 0.5
        if price < sma10 and price < sma30: ps += 0.5
        if vol_surge and last_bull:       bs += 1.0
        elif vol_surge and not last_bull: ps += 1.0

        if bs >= ps and bs >= min_score:
            direction, score = 'call', bs
        elif ps > bs and ps >= min_score:
            direction, score = 'put', ps
        else:
            return None

        if direction == 'call':
            base       = near_sup if at_sup else (price - atr * 0.2)
            entry_low  = round(base, 2)
            entry_high = round(base + atr * 0.35, 2)
            stop       = round(entry_low - atr * 0.5, 2)
            min_t1     = entry_high + atr * 0.5
            target1    = round(near_res if (resistances and near_res > min_t1) else min_t1, 2)
            target2    = round(target1 + atr * 0.6, 2)
        else:
            base       = near_res if at_res else (price + atr * 0.2)
            entry_high = round(base, 2)
            entry_low  = round(base - atr * 0.35, 2)
            stop       = round(entry_high + atr * 0.5, 2)
            max_t1     = entry_low - atr * 0.5
            target1    = round(near_sup if (supports and near_sup < max_t1) else max_t1, 2)
            target2    = round(target1 - atr * 0.6, 2)

        entry_mid = (entry_low + entry_high) / 2
        rr = ((target1 - entry_mid) / (entry_mid - stop)
              if direction == 'call' and entry_mid > stop
              else (entry_mid - target1) / (stop - entry_mid)
              if direction == 'put' and stop > entry_mid
              else 0.0)

        if rr < min_rr:
            return None

        return {
            'direction'       : direction,
            'score'           : round(score, 2),
            'rr'              : round(rr, 2),
            'suggested_entry' : round(entry_mid, 2),
            'entry_low'       : entry_low,
            'entry_high'      : entry_high,
            'stop'            : stop,
            'target1'         : target1,
            'target2'         : target2,
        }
    except Exception:
        return None


# ─── Per-symbol backtest ───────────────────────────────────────────────────────

def backtest_symbol(symbol: str, days: int = 55,
                    min_score: float = 5.5, min_rr: float = 1.5) -> list:
    et = pytz.timezone('America/New_York')

    try:
        ticker = yf.Ticker(symbol)
        df5  = ticker.history(period=f"{days + 5}d",  interval='5m', auto_adjust=True)
        df1d = ticker.history(period=f"{days + 30}d", interval='1d', auto_adjust=True)
        if df5.empty or len(df5) < 100:
            return []
    except Exception as e:
        print(f"  [backtest] {symbol}: {e}")
        return []

    # Normalize timezone
    for df in (df5, df1d):
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert(et)
        else:
            df.index = df.index.tz_convert(et)

    trading_days = df5.index.normalize().unique()
    results = []

    for day in trading_days[5:-1]:
        d = day.date()
        signal_ts = pd.Timestamp(d, tz=et).replace(hour=9, minute=45)
        eod_ts    = pd.Timestamp(d, tz=et).replace(hour=15, minute=45)

        df5_sl  = df5[df5.index <= signal_ts].tail(200)
        df1d_sl = df1d[df1d.index.normalize() < day].tail(30)

        sig = _compute(df5_sl, df1d_sl, min_score, min_rr)
        if sig is None:
            continue

        outcome_df = df5[(df5.index > signal_ts) & (df5.index <= eod_ts)]
        if outcome_df.empty:
            continue

        hi = float(outcome_df['High'].max())
        lo = float(outcome_df['Low'].min())

        if sig['direction'] == 'call':
            if hi >= sig['target2']:   outcome = 'WIN_T2'
            elif hi >= sig['target1']: outcome = 'WIN_T1'
            elif lo <= sig['stop']:    outcome = 'LOSS'
            else:                      outcome = 'OPEN'
        else:
            if lo <= sig['target2']:   outcome = 'WIN_T2'
            elif lo <= sig['target1']: outcome = 'WIN_T1'
            elif hi >= sig['stop']:    outcome = 'LOSS'
            else:                      outcome = 'OPEN'

        results.append({
            'date'            : d.strftime('%Y-%m-%d'),
            'symbol'          : symbol,
            'direction'       : sig['direction'],
            'score'           : sig['score'],
            'rr'              : sig['rr'],
            'suggested_entry' : sig['suggested_entry'],
            'stop'            : sig['stop'],
            'target1'         : sig['target1'],
            'target2'         : sig['target2'],
            'outcome'         : outcome,
        })

    return results


# ─── Full backtest ─────────────────────────────────────────────────────────────

def run_backtest(symbols: list, days: int = 55,
                 min_score: float = 5.5, min_rr: float = 1.5) -> dict:
    all_results = []
    symbol_stats = {}

    for symbol in symbols:
        print(f"  → {symbol}", end=" ", flush=True)
        res = backtest_symbol(symbol, days, min_score, min_rr)
        all_results.extend(res)

        wins   = sum(1 for r in res if 'WIN'  in r['outcome'])
        losses = sum(1 for r in res if 'LOSS' in r['outcome'])
        decided = wins + losses
        wr = round(wins / decided * 100) if decided > 0 else 0
        avg_rr = round(sum(r['rr'] for r in res) / len(res), 2) if res else 0

        symbol_stats[symbol] = {
            'signals': len(res), 'wins': wins,
            'losses': losses, 'win_rate': wr, 'avg_rr': avg_rr,
        }
        if res:
            print(f"{len(res)} إشارة | WR: {wr}%")
        else:
            print("لا إشارات")

    total_w = sum(1 for r in all_results if 'WIN'  in r['outcome'])
    total_l = sum(1 for r in all_results if 'LOSS' in r['outcome'])
    decided = total_w + total_l
    wr_all  = round(total_w / decided * 100) if decided > 0 else 0

    return {
        'all_results'  : all_results,
        'symbol_stats' : symbol_stats,
        'overall'      : {
            'total_signals': len(all_results),
            'wins': total_w, 'losses': total_l, 'win_rate': wr_all,
        },
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit('/', 1)[0])
    import config

    print(f"🧪 Backtest — آخر 55 يوم\n")
    data = run_backtest(config.WATCHLIST)

    with open("backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    ov = data['overall']
    print(f"\n{'─'*40}")
    print(f"الإجمالي : {ov['total_signals']} إشارة")
    print(f"فوز      : {ov['wins']}  |  خسارة: {ov['losses']}")
    print(f"معدل الفوز: {ov['win_rate']}%")
    print(f"{'─'*40}")
    print("النتائج محفوظة في backtest_results.json")
