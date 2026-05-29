"""
Real-Time Price Monitor — Alpaca WebSocket
يراقب الإشارات المفتوحة لحظياً ويرسل تنبيهات عند المحطات الرئيسية.

المحطات:
  +25%  → تنبيه ارتفاع أول
  +50%  → تنبيه ارتفاع ثاني
  T1    → ✅ الهدف الأول + تحريك الوقف
  +100% → تنبيه مضاعفة
  T2    → ✅✅ الهدف الثاني (خروج كلي)
  Stop  → ❌ الوقف ضُرب

تقدير سعر العقد:
  contract_now ≈ option_price + (stock_move × delta)
"""

from __future__ import annotations

import threading
from typing import Dict, Any

import config
from telegram_bot import send

# ── إشارات نشطة ──────────────────────────────────────────────────────────────
# { symbol: { "signal": dict, "milestones": set() } }
_active: Dict[str, Dict[str, Any]] = {}
_lock   = threading.Lock()

PCT_MILESTONES = [25, 50, 100]


# ─── Public API ───────────────────────────────────────────────────────────────

def add_signal(symbol: str, entry: dict) -> None:
    with _lock:
        _active[symbol] = {"signal": entry, "milestones": set()}
    print(f"  [monitor] ➕ {symbol} — تتبع لحظي بدأ")


def remove_signal(symbol: str) -> None:
    with _lock:
        _active.pop(symbol, None)


def start(watchlist: list) -> None:
    """يُشغَّل مرة واحدة عند بدء البوت."""
    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        print("  [monitor] Alpaca غير مضبوط — WebSocket معطّل")
        return
    threading.Thread(target=_run_ws, args=(watchlist,), daemon=True).start()
    print(f"  [monitor] WebSocket بدأ — يراقب {len(watchlist)} أصل")


# ─── Price handler ────────────────────────────────────────────────────────────

def _check_price(symbol: str, current_price: float) -> None:
    with _lock:
        data = _active.get(symbol)
    if not data:
        return

    sig        = data["signal"]
    milestones = data["milestones"]
    direction  = sig.get("direction", "")
    entry_px   = float(sig.get("suggested_entry") or
                       (sig["entry_low"] + sig["entry_high"]) / 2)
    opt_px     = float(sig.get("option_price") or 0)
    delta      = float(sig.get("delta") or 0)
    target1    = float(sig.get("target1") or 0)
    target2    = float(sig.get("target2") or 0)
    stop_px    = float(sig.get("stop") or 0)

    if opt_px <= 0 or entry_px <= 0:
        return

    # Delta افتراضي إذا كان صفراً
    if abs(delta) < 0.01:
        delta = 0.45 if direction == "call" else -0.45

    # تقدير سعر العقد الحالي
    stock_move   = current_price - entry_px
    contract_now = max(0.01, opt_px + stock_move * delta)
    pct          = (contract_now - opt_px) / opt_px * 100

    # ── T2 (أعلى أولوية — يُغلق التتبع) ─────────────────────────────────────
    t2 = (direction == "call" and current_price >= target2) or \
         (direction == "put"  and current_price <= target2)
    if t2 and "T2" not in milestones:
        milestones.add("T2")
        _alert_t2(symbol, sig, current_price, contract_now, pct)
        remove_signal(symbol)
        return

    # ── Stop (يُغلق التتبع) ───────────────────────────────────────────────────
    stopped = (direction == "call" and current_price <= stop_px) or \
              (direction == "put"  and current_price >= stop_px)
    if stopped and "stop" not in milestones:
        milestones.add("stop")
        _alert_stop(symbol, sig, current_price, contract_now, pct)
        remove_signal(symbol)
        return

    # ── T1 ────────────────────────────────────────────────────────────────────
    t1 = (direction == "call" and current_price >= target1) or \
         (direction == "put"  and current_price <= target1)
    if t1 and "T1" not in milestones:
        milestones.add("T1")
        _alert_t1(symbol, sig, current_price, contract_now, pct)

    # ── % محطات الربح ─────────────────────────────────────────────────────────
    for threshold in PCT_MILESTONES:
        key = f"pct_{threshold}"
        if pct >= threshold and key not in milestones:
            milestones.add(key)
            _alert_pct(symbol, sig, threshold, contract_now, pct)


# ─── Alerts ───────────────────────────────────────────────────────────────────

def _pct_str(pct: float) -> str:
    return f"+{pct:.0f}%" if pct >= 0 else f"{pct:.0f}%"


def _alert_pct(symbol, sig, threshold, contract_now, pct):
    d_ar = "كول 🟢" if sig["direction"] == "call" else "بوت 🔴"
    msg  = (
        f"📈 {symbol} | {d_ar}\n"
        f"العقد ارتفع {_pct_str(pct)} ({'مضاعفة! 🎯' if threshold == 100 else ''})\n"
        f"الدخول: ${sig['option_price']:.2f} → الآن: ~${contract_now:.2f}\n"
        f"{'━'*24}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_t1(symbol, sig, price, contract_now, pct):
    d_ar    = "كول 🟢" if sig["direction"] == "call" else "بوت 🔴"
    entry_p = sig.get("suggested_entry") or (sig["entry_low"] + sig["entry_high"]) / 2
    msg = (
        f"✅ {symbol} | {d_ar} — الهدف الأول!\n"
        f"السهم: {entry_p:.2f} → {price:.2f}\n"
        f"العقد: ${sig['option_price']:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n\n"
        f"🔒 حرّك الوقف إلى نقطة التعادل ({entry_p:.2f})\n"
        f"🎯 الهدف الثاني: {sig['target2']:.2f}\n"
        f"{'━'*24}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_t2(symbol, sig, price, contract_now, pct):
    d_ar    = "كول 🟢" if sig["direction"] == "call" else "بوت 🔴"
    entry_p = sig.get("suggested_entry") or (sig["entry_low"] + sig["entry_high"]) / 2
    msg = (
        f"✅✅ {symbol} | {d_ar} — الهدف الثاني!\n"
        f"السهم: {entry_p:.2f} → {price:.2f}\n"
        f"العقد: ${sig['option_price']:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n\n"
        f"🎯 اخرج من الصفقة كلياً\n"
        f"{'━'*24}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_stop(symbol, sig, price, contract_now, pct):
    d_ar    = "كول 🟢" if sig["direction"] == "call" else "بوت 🔴"
    entry_p = sig.get("suggested_entry") or (sig["entry_low"] + sig["entry_high"]) / 2
    msg = (
        f"❌ {symbol} | {d_ar} — الوقف ضُرب\n"
        f"السهم: {entry_p:.2f} → {price:.2f}\n"
        f"العقد: ${sig['option_price']:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n"
        f"{'━'*24}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


# ─── WebSocket ────────────────────────────────────────────────────────────────

def _run_ws(watchlist: list) -> None:
    try:
        from alpaca.data.live import StockDataStream

        wss = StockDataStream(
            config.ALPACA_API_KEY,
            config.ALPACA_SECRET_KEY,
            feed="iex",
        )

        async def on_quote(q):
            try:
                bid = float(q.bid_price or 0)
                ask = float(q.ask_price or 0)
                if bid > 0 and ask > 0:
                    _check_price(q.symbol, (bid + ask) / 2)
            except Exception:
                pass

        wss.subscribe_quotes(on_quote, *watchlist)
        wss.run()

    except Exception as e:
        print(f"  [monitor] WebSocket خطأ: {e}")
