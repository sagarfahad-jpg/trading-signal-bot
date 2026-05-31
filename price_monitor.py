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

POLL_SECONDS        = 45
PCT_MILESTONES      = [25, 50, 100]      # محطات الربح
CONTRACT_LOSS_ALERTS = [-40, -60]        # تنبيه تآكل العقد (Theta)
PENDING_MAX_HOURS   = 24                 # إشارة لم تدخل خلال هذه المدة → تُلغى

# تتبّع المحطات المُرسلة لكل إشارة (في الذاكرة) — { signal_id: set(...) }
_milestones: dict = {}
_announced: set   = set()   # إشارات يدوية أُعلن عنها (لتفادي التكرار)
_peak: dict       = {}      # أعلى/أدنى سعر بعد T1 للـ Trailing Stop
_mfe: dict        = {}      # Max Favorable Excursion (أقصى ربح بالـ R)
_mae: dict        = {}      # Max Adverse Excursion (أقصى خسارة بالـ R)
_lo_px: dict      = {}      # أدنى سعر سهم بعد الدخول
_hi_px: dict      = {}      # أعلى سعر سهم بعد الدخول


def _current_r(direction, price, entry_px, stop_px) -> float:
    """R الحالي بناءً على المسافة دخول→وقف."""
    if direction == "call" and entry_px > stop_px:
        return (price - entry_px) / (entry_px - stop_px)
    if direction == "put" and stop_px > entry_px:
        return (entry_px - price) / (stop_px - entry_px)
    return 0.0


def _duration_min(sig) -> int:
    """مدة الصفقة بالدقائق من وقت الدخول (أو الإنشاء) حتى الآن."""
    try:
        import datetime as _dt
        t0s = sig.get("entry_time") or sig.get("created_at") or ""
        t0  = _dt.datetime.fromisoformat(str(t0s).replace("Z", "+00:00"))
        return int((_dt.datetime.now(_dt.timezone.utc) - t0).total_seconds() / 60)
    except Exception:
        return 0


def _finalize(sig, status, outcome_price, r, reason):
    """يُغلق الإشارة مع كل تفاصيل سلامة البيانات + ينظّف الذاكرة."""
    sid = sig["id"]
    db.update_outcome(
        sid, status, outcome_price, r,
        exit_reason=reason,
        duration_min=_duration_min(sig),
        max_favorable=_mfe.get(sid),
        max_adverse=_mae.get(sid),
        lowest_price=_lo_px.get(sid),
        highest_price=_hi_px.get(sid),
    )
    for d in (_milestones, _peak, _mfe, _mae, _lo_px, _hi_px):
        d.pop(sid, None)


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
    """يفحص كل الإشارات المفتوحة (pending + active) + طلبات الخروج اليدوي."""
    signals = db.get_open_signals()          # status = 'open'
    exits   = db.get_exit_requests()         # status = 'exit_requested'
    cancels = db.get_cancel_requests()       # status = 'cancel_requested'

    # الإلغاء اليدوي لا يحتاج سعر — نفّذه فوراً
    for c in cancels:
        try:
            _alert_cancelled(c)
            _finalize(c, "cancelled", 0.0, 0.0, "manual_cancel")
        except Exception as e:
            print(f"  [monitor] cancel {c.get('symbol')}: {e}")

    signals = signals + exits
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

    # ── خروج يدوي فوري (طلب من Dashboard) ────────────────────────────────────
    if sig.get("status") == "exit_requested":
        entry_px = float(sig.get("entry_price") or (e_low + e_high) / 2)
        stop_px  = float(sig.get("stop_price") or 0)
        if direction == "call" and entry_px > stop_px:
            r = (price - entry_px) / (entry_px - stop_px)
        elif direction == "put" and stop_px > entry_px:
            r = (entry_px - price) / (stop_px - entry_px)
        else:
            r = 0.0
        _alert_manual_exit(sig, price, r)
        _finalize(sig, "manual_exit", price, round(r, 3), "manual")
        return

    ms = _milestones.setdefault(sid, set())

    # ── إعلان الإشارة اليدوية أول مرة ────────────────────────────────────────
    if sig.get("is_manual") and sid not in _announced:
        _announced.add(sid)
        _alert_manual_new(sig)

    # ── المرحلة 1: pending → انتظار الدخول ──────────────────────────────────
    if not filled:
        # إلغاء تلقائي لو لم يتحقق الدخول خلال المدة المسموحة
        if _age_hours(sig) > PENDING_MAX_HOURS:
            _alert_pending_expired(sig)
            _finalize(sig, "cancelled", 0.0, 0.0, "expired_no_entry")
            return
        # ── دخول تكيّفي حسب عرض المنطقة ──────────────────────────────────────
        lo, hi  = min(e_low, e_high), max(e_low, e_high)
        width   = hi - lo
        width_pct = (width / price * 100) if price else 0
        if width_pct >= 0.5 and width > 0:
            # منطقة عريضة → ننتظر النصف الأعمق (دخول أدق, R:R أفضل)
            if direction == "call":
                trigger = lo <= price <= (lo + width * 0.5)
            else:
                trigger = (hi - width * 0.5) <= price <= hi
        else:
            # منطقة ضيقة → أي لمسة تكفي
            trigger = lo <= price <= hi

        if trigger:
            db.mark_entry_filled(sid, fill_price=price)   # نسجّل سعر اللمسة الفعلي
            _lo_px[sid] = price
            _hi_px[sid] = price
            _alert_entry(sig, price)
            if sig.get("is_manual"):
                _auto_log_trade(sig, price)
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

    # ── تتبّع MFE/MAE (بالـ R) + أعلى/أدنى سعر فعلي ──────────────────────────
    r_now = _current_r(direction, price, entry_px, stop_px)
    _mfe[sid] = max(_mfe.get(sid, r_now), r_now)
    _mae[sid] = min(_mae.get(sid, r_now), r_now)
    _hi_px[sid] = max(_hi_px.get(sid, price), price)
    _lo_px[sid] = min(_lo_px.get(sid, price), price)

    # ── T2 (يُغلق) ──────────────────────────────────────────────────────────
    t2 = (direction == "call" and price >= target2) or \
         (direction == "put"  and price <= target2)
    if t2 and "T2" not in ms:
        ms.add("T2")
        _alert_exit(sig, price, contract_now, pct, "T2")
        _finalize(sig, "hit_t2", target2, round(rr, 3), "target2")
        return

    # ── Stop (يُغلق) ────────────────────────────────────────────────────────
    stopped = (direction == "call" and price <= stop_px) or \
              (direction == "put"  and price >= stop_px)
    if stopped and "stop" not in ms:
        ms.add("stop")
        if "T1" in ms:
            # وصل T1 ثم رجع للوقف → نُسجّلها فوز جزئي (الوقف عند التعادل)
            _alert_exit(sig, price, contract_now, pct, "stop_after_t1")
            _finalize(sig, "hit_t1", target1, round(rr * 0.5, 3), "stop_after_t1")
        else:
            _alert_exit(sig, price, contract_now, pct, "stop")
            _finalize(sig, "stopped", stop_px, -1.0, "stop")
        return

    # ── T1 (لا يُغلق — تنبيه فقط + تفعيل Trailing Stop) ─────────────────────
    t1 = (direction == "call" and price >= target1) or \
         (direction == "put"  and price <= target1)
    if t1 and "T1" not in ms:
        ms.add("T1")
        _peak[sid] = price            # نبدأ تتبّع القمة للـ Trailing
        _alert_t1(sig, price, contract_now, pct)

    # ── Trailing Stop (بعد T1 فقط) ──────────────────────────────────────────
    if "T1" in ms:
        trail_gap = abs(target1 - entry_px) * 0.5   # نصف المسافة دخول→هدف١
        if direction == "call":
            _peak[sid] = max(_peak.get(sid, price), price)
            if price <= _peak[sid] - trail_gap and "trail" not in ms:
                ms.add("trail")
                _alert_exit(sig, price, contract_now, pct, "trail")
                _finalize(sig, "hit_t1", _peak[sid] - trail_gap, round(rr * 0.5, 3), "trailing_stop")
                return
        else:
            _peak[sid] = min(_peak.get(sid, price), price)
            if price >= _peak[sid] + trail_gap and "trail" not in ms:
                ms.add("trail")
                _alert_exit(sig, price, contract_now, pct, "trail")
                _finalize(sig, "hit_t1", _peak[sid] + trail_gap, round(rr * 0.5, 3), "trailing_stop")
                return

    # ── محطات الربح ──────────────────────────────────────────────────────────
    if opt_px:
        for thr in PCT_MILESTONES:
            key = f"pct_{thr}"
            if pct >= thr and key not in ms:
                ms.add(key)
                _alert_pct(sig, thr, contract_now, pct)

        # ── تنبيه تآكل العقد (Theta) — وقف مبني على قيمة العقد ──────────────
        for loss in CONTRACT_LOSS_ALERTS:
            key = f"loss_{loss}"
            if pct <= loss and key not in ms:
                ms.add(key)
                _alert_contract_decay(sig, contract_now, pct)


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


def _age_hours(sig: dict) -> float:
    try:
        import datetime as _dt
        created = _dt.datetime.fromisoformat(
            str(sig.get("created_at", "")).replace("Z", "+00:00"))
        return (_dt.datetime.now(_dt.timezone.utc) - created).total_seconds() / 3600
    except Exception:
        return 0.0


def _auto_log_trade(sig: dict, price: float) -> None:
    """يسجّل الإشارة اليدوية في دفتر 'صفقاتي' كصفقة مفتوحة عند تحقق الدخول."""
    try:
        import datetime as _dt
        opt = float(sig.get("option_price") or 0)
        # عدد العقود من رأس المال (1% مخاطرة) إن توفّر سعر العقد
        contracts = 1
        if opt > 0:
            try:
                acct = db.get_account_size(config.ACCOUNT_SIZE)
                contracts = max(1, int(acct * config.RISK_PCT / (opt * 100)))
            except Exception:
                contracts = 1
        note_bits = []
        if sig.get("strike"): note_bits.append(f"Strike {sig['strike']}")
        if sig.get("expiry"): note_bits.append(str(sig["expiry"]))
        note_bits.append(f"دخول السهم ~{price:.2f}")
        db.add_my_trade({
            "entry_date":   _dt.date.today().isoformat(),
            "symbol":       sig["symbol"],
            "side":         "CALL" if sig.get("direction") == "call" else "PUT",
            "entry_price":  round(opt, 2),          # سعر العقد (Premium)
            "contracts":    contracts,
            "stop_price":   float(sig.get("stop_price") or 0),
            "target_price": float(sig.get("target1") or 0),
            "from_signal":  False,                  # اختيارك الشخصي
            "status":       "OPEN",
            "notes":        "auto من إشارة يدوية — " + " | ".join(note_bits),
        })
    except Exception as e:
        print(f"  [monitor] auto_log_trade: {e}")


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
        + ("📒 سُجّلت في «صفقاتي» — أغلقها بسعرك الفعلي عند الخروج\n" if sig.get("is_manual") else "")
        + f"{'━'*26}\n"
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
    qty     = int(sig.get("contracts") or 0)
    # خروج جزئي (Scale-out): نصف العقود الآن
    if qty >= 2:
        half = qty // 2
        scale_line = f"💰 اخرج {half} عقد الآن (نصف المركز) — ثبّت ربح\n🎯 اترك {qty - half} عقد لـ T2\n"
    else:
        scale_line = "💰 فكّر بجني نصف الربح هنا\n"
    msg = (
        f"✅ {sig['symbol']} | {_dir_ar(sig)} — الهدف الأول!  {_tag(sig)}\n"
        f"السهم وصل: {price:.2f}\n"
        + (f"العقد: ${opt:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n" if opt else "")
        + f"\n{scale_line}"
        f"🔒 حرّك وقف الباقي لنقطة التعادل ({entry_p:.2f})\n"
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
    elif kind == "trail":
        header = f"🪤 {sig['symbol']} | {_dir_ar(sig)} — Trailing Stop (تأمين ربح بعد T1)"
    else:
        header = f"❌ {sig['symbol']} | {_dir_ar(sig)} — الوقف ضُرب"
    msg = (
        f"{header}  {_tag(sig)}\n"
        f"السهم: {price:.2f}\n"
        + (f"العقد: ${opt:.2f} → ~${contract_now:.2f} ({_pct_str(pct)})\n" if opt else "")
        + f"{'━'*24}\nللمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_contract_decay(sig, contract_now, pct):
    opt = float(sig.get("option_price") or 0)
    msg = (
        f"⚠️ تآكل العقد — {sig['symbol']} | {_dir_ar(sig)}  {_tag(sig)}\n"
        f"{'━'*26}\n"
        f"العقد فقد {_pct_str(pct)} من قيمته (السهم لم يضرب الوقف)\n"
        + (f"الدخول: ${opt:.2f} → الآن: ~${contract_now:.2f}\n" if opt else "")
        + f"💡 السبب غالباً Theta — فكّر بالخروج لتقليل الخسارة\n"
        f"{'━'*26}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_manual_exit(sig, price, r):
    r_sign = "+" if r >= 0 else ""
    r_emoji = "✅" if r >= 0 else "❌"
    msg = (
        f"🚪 خروج فوري — {sig['symbol']} | {_dir_ar(sig)}  {_tag(sig)}\n"
        f"{'━'*26}\n"
        f"أُغلقت يدوياً عند: {price:.2f}\n"
        f"{r_emoji} النتيجة: {r_sign}{r:.2f}R\n"
        f"{'━'*26}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_cancelled(sig):
    info = _opt_info(sig)
    msg = (
        f"✖ أُلغيت إشارة — {sig['symbol']} | {_dir_ar(sig)}  {_tag(sig)}\n"
        f"{'━'*26}\n"
        + (f"{info}\n" if info else "")
        + f"أُلغيت يدوياً قبل الدخول\n"
        f"{'━'*26}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _alert_pending_expired(sig):
    info = _opt_info(sig)
    msg = (
        f"⌛ أُلغيت إشارة معلّقة — {sig['symbol']} | {_dir_ar(sig)}  {_tag(sig)}\n"
        f"{'━'*26}\n"
        + (f"{info}\n" if info else "")
        + f"السعر لم يصل منطقة الدخول خلال {PENDING_MAX_HOURS} ساعة\n"
        f"{'━'*26}\n"
        f"للمراقبة فقط — ليست توصية"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
