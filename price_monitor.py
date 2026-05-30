"""
Unified Signal Monitor — محرك موحّد لمراقبة كل الإشارات (بوت + يدوية).
يقرأ من Supabase ويدير دورة الحياة كاملة:

  ⏳ pending  → تنتظر وصول السعر لمنطقة الدخول
  🟢 دخول     → السعر لمس المنطقة → تنبيه دخول (entry_filled = True)
  📈 active   → مراقبة: +25% / +50% / T1 / +100% / T2
  ✅/❌ خروج   → T2 أو الوقف → تحديث Supabase + تنبيه

يعمل بالـ Polling كل 45 ثانية — يدعم أي سهم (داخل القائمة أو خارجها).
تقدير سعر العقد:  contract_now ≈ option_price + (stock_move × delta)
"""

from __future__ import annotations

import threading
import time

import config
import db
import data_client as dc
from telegram_bot import send

POLL_SECONDS   = 45
PCT_MILESTONES = [25, 50, 100]

# تتبّع المحطات المُرسلة لكل إشارة (في الذاكرة) — { signal_id: set(...) }
_milestones: dict = {}
_announced: set   = set()   # إشارات يدوية أُعلن عنها (لتفادي التكرار)


# ─── Public API ───────────────────────────────────────────────────────────────

def start() -> None:
    """يُشغَّل مرة واحدة عند بدء البوت."""
    if not db.is_configured():
        print("  [monitor] Supabase غير مضبوط — المراقبة معطّلة")
        return
    threading.Thread(target=_loop, daemon=True).start()
    print("  [monitor] محرك المراقبة الموحّد بدأ (كل 45 ثانية)")


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as e:
            print(f"  [monitor] tick error: {e}")
        time.sleep(POLL_SECONDS)


def _tick() -> None:
    """يفحص كل الإشارات المفتوحة (pending + active)."""
    signals = db.get_open_signals()        # status = 'open'
    if not signals:
        return

    # جمّع الأسعار مرة واحدة لكل سهم (تقليل الطلبات)
    symbols = list({s["symbol"] for s in signals})
    prices  = {}
    for sym in symbols:
        try:
            df = dc.get_bars(sym, "1m", "1d")
            if not df.empty:
                prices[sym] = float(df["Close"].iloc[-1])
        except Exception:
            pass

    for sig in signals:
        px = prices.get(sig["symbol"])
        if px:
            try:
                _check(sig, px)
            except Exception as e:
                print(f"  [monitor] {sig.get('symbol')}: {e}")


# ─── Core lifecycle ─────────────────────────────────────────────────────────

def _check(sig: dict, price: float) -> None:
    sid       = sig["id"]
    symbol    = sig["symbol"]
    direction = sig.get("direction", "")
    filled    = bool(sig.get("entry_filled"))
    e_low     = float(sig.get("entry_low")  or sig.get("entry_price") or 0)
    e_high    = float(sig.get("entry_high") or sig.get("entry_price") or 0)

    ms = _milestones.setdefault(sid, set())

    # ── إعلان الإشارة اليدوية أول مرة ────────────────────────────────────────
    if sig.get("is_manual") and sid not in _announced:
        _announced.add(sid)
        _alert_manual_new(sig)

    # ── المرحلة 1: pending → انتظار الدخول ──────────────────────────────────
    if not filled:
        # تحقّق أن السعر لمس منطقة الدخول
        lo, hi = min(e_low, e_high), max(e_low, e_high)
        if lo <= price <= hi:
            db.mark_entry_filled(sid)
            _alert_entry(sig, price)
        return   # لا نراقب الأهداف قبل تحقق الدخول

    # ── المرحلة 2: active → مراقبة الأهداف ──────────────────────────────────
    entry_px = float(sig.get("entry_price") or (e_low + e_high) / 2)
    opt_px   = float(sig.get("option_price") or 0)
    delta    = float(sig.get("delta") or 0)
    target1  = float(sig.get("target1") or 0)
    target2  = float(sig.get("target2") or 0)
    stop_px  = float(sig.get("stop_price") or 0)
    rr       = float(sig.get("rr") or 1.5)

    if abs(delta) < 0.01:
        delta = 0.45 if direction == "call" else -0.45

    contract_now = max(0.01, opt_px + (price - entry_px) * delta) if opt_px else 0
    pct          = (contract_now - opt_px) / opt_px * 100 if opt_px else 0

    # ── T2 (يُغلق) ──────────────────────────────────────────────────────────
    t2 = (direction == "call" and price >= target2) or \
         (direction == "put"  and price <= target2)
    if t2 and "T2" not in ms:
        ms.add("T2")
        _alert_exit(sig, price, contract_now, pct, "T2")
        db.update_outcome(sid, "hit_t2", target2, round(rr, 3))
        _milestones.pop(sid, None)
        return

    # ── Stop (يُغلق) ────────────────────────────────────────────────────────
    stopped = (direction == "call" and price <= stop_px) or \
              (direction == "put"  and price >= stop_px)
    if stopped and "stop" not in ms:
        ms.add("stop")
        if "T1" in ms:
            # وصل T1 ثم رجع للوقف → نُسجّلها فوز جزئي (الوقف عند التعادل)
            _alert_exit(sig, price, contract_now, pct, "stop_after_t1")
            db.update_outcome(sid, "hit_t1", target1, round(rr * 0.5, 3))
        else:
            _alert_exit(sig, price, contract_now, pct, "stop")
            db.update_outcome(sid, "stopped", stop_px, -1.0)
        _milestones.pop(sid, None)
        return

    # ── T1 (لا يُغلق — تنبيه فقط + تحريك الوقف، نكمل مراقبة T2) ──────────────
    t1 = (direction == "call" and price >= target1) or \
         (direction == "put"  and price <= target1)
    if t1 and "T1" not in ms:
        ms.add("T1")
        _alert_t1(sig, price, contract_now, pct)

    # ── محطات النسبة ─────────────────────────────────────────────────────────
    if opt_px:
        for thr in PCT_MILESTONES:
            key = f"pct_{thr}"
            if pct >= thr and key not in ms:
                ms.add(key)
                _alert_pct(sig, thr, contract_now, pct)


# ─── Alerts ───────────────────────────────────────────────────────────────────

def _dir_ar(sig: dict) -> str:
    return "كول 🟢" if sig.get("direction") == "call" else "بوت 🔴"

def _tag(sig: dict) -> str:
    return "📌 يدوية" if sig.get("is_manual") else "🤖 بوت"

def _pct_str(p: float) -> str:
    return f"+{p:.0f}%" if p >= 0 else f"{p:.0f}%"

def _opt_info(sig: dict) -> str:
    parts = []
    if sig.get("strike"):
        parts.append(f"Strike {sig['strike']}")
    if sig.get("expiry"):
        parts.append(str(sig["expiry"]))
    return " | ".join(parts)


def _alert_manual_new(sig: dict) -> None:
    info = _opt_info(sig)
    e_low  = float(sig.get("entry_low")  or 0)
    e_high = float(sig.get("entry_high") or 0)
    msg = (
        f"📌 إشارة يدوية — {sig['symbol']} | {_dir_ar(sig)}\n"
        f"{'━'*26}\n"
        + (f"{info}\n" if info else "")
        + f"💠 منطقة الدخول: {e_low:.2f} – {e_high:.2f}\n"
        f"🛑 الوقف: {float(sig.get('stop_price') or 0):.2f}\n"
        f"🎯 هدف ١: {float(sig.get('target1') or 0):.2f}  |  هدف ٢: {float(sig.get('target2') or 0):.2f}\n"
        f"{'━'*26}\n"
        f"🤖 البوت يراقبها — تنبيه عند الدخول والأهداف\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_entry(sig: dict, price: float) -> None:
    info = _opt_info(sig)
    msg = (
        f"🟢 تنبيه دخول — {sig['symbol']} | {_dir_ar(sig)}  {_tag(sig)}\n"
        f"{'━'*26}\n"
        + (f"{info}\n" if info else "")
        + f"السعر وصل منطقة الدخول: {price:.2f}\n"
        f"🛑 الوقف: {float(sig.get('stop_price') or 0):.2f}\n"
        f"🎯 هدف ١: {float(sig.get('target1') or 0):.2f}  |  هدف ٢: {float(sig.get('target2') or 0):.2f}\n"
        f"{'━'*26}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_pct(sig, threshold, contract_now, pct):
    opt = float(sig.get("option_price") or 0)
    msg = (
        f"📈 {sig['symbol']} | {_dir_ar(sig)}  {_tag(sig)}\n"
        f"العقد ارتفع {_pct_str(pct)}{'  مضاعفة! 🎯' if threshold == 100 else ''}\n"
        + (f"الدخول: ${opt:.2f} → الآن: ~${contract_now:.2f}\n" if opt else "")
        + f"{'━'*24}\nللمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_t1(sig, price, contract_now, pct):
    opt = float(sig.get("option_price") or 0)
    entry_p = float(sig.get("entry_price") or 0)
    msg = (
        f"✅ {sig['symbol']} | {_dir_ar(sig)} — الهدف الأول!  {_tag(sig)}\n"
        f"السهم وصل: {price:.2f}\n"
        + (f"العقد: ${opt:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n" if opt else "")
        + f"\n🔒 حرّك الوقف لنقطة التعادل ({entry_p:.2f})\n"
        f"🎯 الهدف الثاني: {float(sig.get('target2') or 0):.2f}\n"
        f"{'━'*24}\nللمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_exit(sig, price, contract_now, pct, kind):
    opt = float(sig.get("option_price") or 0)
    if kind == "T2":
        header = f"✅✅ {sig['symbol']} | {_dir_ar(sig)} — الهدف الثاني! اخرج كلياً"
    elif kind == "stop_after_t1":
        header = f"🔒 {sig['symbol']} | {_dir_ar(sig)} — رجع للوقف بعد الهدف الأول (تعادل)"
    else:
        header = f"❌ {sig['symbol']} | {_dir_ar(sig)} — الوقف ضُرب"
    msg = (
        f"{header}  {_tag(sig)}\n"
        f"السهم: {price:.2f}\n"
        + (f"العقد: ${opt:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n" if opt else "")
        + f"{'━'*24}\nللمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
