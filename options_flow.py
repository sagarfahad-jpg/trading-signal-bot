"""
Options Flow — مستويات المؤسسات من Open Interest (عبر yfinance, مجاني):

  Max Pain   : السعر الذي يخسر عنده أكبر عدد من حاملي العقود (مغناطيس).
  Call Wall  : أعلى Strike في Call OI (مقاومة/سقف).
  Put Wall   : أعلى Strike في Put OI (دعم/أرضية).
  P/C Ratio  : إجمالي Put OI ÷ Call OI (تحيّز السوق).

تُحسب من أقرب انتهاء، وتُخزَّن كـ سياق مع كل إشارة (للتقييم لاحقاً).
"""

from __future__ import annotations
import time as _time
from typing import Optional, Dict

_cache: Dict[str, dict] = {}
_TTL = 3600   # ساعة — OI يتحدّث يومياً، الساعة كافية


def get_options_flow(symbol: str) -> Optional[dict]:
    """يُرجع {max_pain, call_wall, put_wall, pcr} أو None."""
    now = _time.time()
    c = _cache.get(symbol)
    if c and now - c["ts"] < _TTL:
        return c["data"]

    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        exps = t.options
        if not exps:
            return None
        chain = t.option_chain(exps[0])     # أقرب انتهاء
        calls = chain.calls[["strike", "openInterest"]].copy()
        puts  = chain.puts[["strike", "openInterest"]].copy()
        calls["openInterest"] = calls["openInterest"].fillna(0)
        puts["openInterest"]  = puts["openInterest"].fillna(0)

        call_oi = float(calls["openInterest"].sum())
        put_oi  = float(puts["openInterest"].sum())
        if call_oi <= 0:
            return None

        # Walls
        call_wall = float(calls.loc[calls["openInterest"].idxmax(), "strike"])
        put_wall  = float(puts.loc[puts["openInterest"].idxmax(),  "strike"]) if put_oi > 0 else 0.0

        # P/C Ratio
        pcr = round(put_oi / call_oi, 2)

        # Max Pain — السعر الذي يقلّل قيمة العقود داخل المال
        strikes = sorted(set(calls["strike"]).union(set(puts["strike"])))
        call_map = dict(zip(calls["strike"], calls["openInterest"]))
        put_map  = dict(zip(puts["strike"],  puts["openInterest"]))
        best_p, min_pain = 0.0, float("inf")
        for P in strikes:
            pain = 0.0
            for k, oi in call_map.items():
                if P > k:
                    pain += (P - k) * oi
            for k, oi in put_map.items():
                if P < k:
                    pain += (k - P) * oi
            if pain < min_pain:
                min_pain, best_p = pain, float(P)

        data = {
            "max_pain":  round(best_p, 2),
            "call_wall": round(call_wall, 2),
            "put_wall":  round(put_wall, 2),
            "pcr":       pcr,
        }
        _cache[symbol] = {"ts": now, "data": data}
        return data
    except Exception as e:
        print(f"  [options_flow] {symbol}: {e}")
        return None
