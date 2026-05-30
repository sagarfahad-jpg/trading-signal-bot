#!/usr/bin/env python3
"""
Trading Signal Bot v3  — VIX / Earnings / MTF / R:R / Outcome Notifications / Auto-Optimize
"""

import time, json, os, threading, subprocess, platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pytz, yfinance as yf

import config
from analyzer import analyze, get_vix, quick_scan, SignalResult
from telegram_bot import format_message, send, send_photo
from chart_generator import generate_signal_chart
import data_client as _dc
from weekly_report import send_weekly_report
from telegram_commands import start_command_listener
import db
import outcome_tracker
import price_monitor

LOG_FILE       = os.path.join(os.path.dirname(__file__), "signals_log.json")
WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")
THRESHOLD_FILE = os.path.join(os.path.dirname(__file__), "asset_thresholds.json")

last_signal: dict[str, datetime] = {}
_min_score_cache: dict = {"ts": 0.0, "value": None}


def _get_min_score() -> float:
    """يجلب MIN_SCORE من Supabase (cache 5 دقائق)."""
    import time as _t
    now = _t.time()
    if now - float(_min_score_cache["ts"]) < 300 and _min_score_cache["value"]:
        return float(_min_score_cache["value"])
    try:
        val = db.get_config("min_score", "")
        if val:
            _min_score_cache["value"] = float(val)
            _min_score_cache["ts"]    = now
            return float(val)
    except Exception:
        pass
    return config.MIN_SCORE


# ─── Watchlist & Thresholds ───────────────────────────────────────────────────

def _load_watchlist() -> list:
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return list(config.WATCHLIST)


def _load_thresholds() -> dict:
    if os.path.exists(THRESHOLD_FILE):
        try:
            with open(THRESHOLD_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ─── Log helpers ──────────────────────────────────────────────────────────────

def _load_log() -> list:
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _write_log(data: list):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_signal(signal: SignalResult, sent_ok: bool):
    # ── حفظ في Supabase (مشترك مع Dashboard) ─────────────────────────────────
    if sent_ok:
        db.save_signal(signal)

    log = _load_log()
    log.append({
        "id"         : len(log) + 1,
        "timestamp"  : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol"     : signal.symbol,
        "direction"  : signal.direction,
        "confidence" : signal.confidence,
        "score"      : round(signal.score, 2),
        "rr"         : signal.rr,
        "vix"        : signal.vix,
        "mtf_score"  : signal.mtf_score,
        "entry_low"  : signal.entry_low,
        "entry_high" : signal.entry_high,
        "suggested_entry": round((signal.entry_low + signal.entry_high) / 2, 2),
        "stop"       : signal.stop,
        "target1"    : signal.target1,
        "target2"    : signal.target2,
        "expiry"     : signal.expiry,
        "strike"     : signal.strike,
        "entry_type" : signal.entry_type,
        "option_price": signal.option_price,
        "delta"      : signal.delta,
        "contracts"  : signal.contracts,
        "regime"         : signal.regime,
        "smt_divergence" : signal.smt_divergence,
        "smt_direction"  : signal.smt_direction,
        "sent"           : sent_ok,
        "outcome"    : None,
        "notified"   : False,
        "be_notified": False,
    })
    _write_log(log)
    # المراقبة الآن تُدار من Supabase عبر price_monitor (لا حاجة لإضافة يدوية)


# ─── Outcome notifications ────────────────────────────────────────────────────

def _send_breakeven_alert(entry: dict):
    """يرسل تنبيه تحريك الوقف إلى سعر الدخول بعد تحقق الهدف الأول."""
    direction_ar = "كول 🟢" if entry["direction"] == "call" else "بوت 🔴"
    ep = entry.get("suggested_entry") or round(
        (entry["entry_low"] + entry["entry_high"]) / 2, 2)

    msg = (
        f"🔒 حرّك الوقف إلى نقطة التعادل!\n"
        f"📊 {entry['symbol']} | {direction_ar}\n\n"
        f"✅ الهدف الأول حُقِّق: {entry['target1']:.2f}\n"
        f"🎯 البوت يراقب الهدف الثاني: {entry['target2']:.2f}\n\n"
        f"⚠️  حرّك الوقف إلى سعر الدخول {ep:.2f}\n"
        f"   لضمان عدم الخسارة في حال الارتداد.\n\n"
        f"🕐 {entry['timestamp']}"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _send_outcome_msg(entry: dict):
    """يرسل إشعار WIN أو LOSS على Telegram."""
    outcome = entry.get("outcome", "")
    direction_ar = "كول 🟢" if entry["direction"] == "call" else "بوت 🔴"
    ep = entry.get("suggested_entry") or round(
        (entry["entry_low"] + entry["entry_high"]) / 2, 2)

    if "WIN_T2" in outcome:
        header = "✅✅ الهدف الثاني حُقِّق!"
        ref_price = entry["target2"]
        diff = abs(ref_price - ep)
        sign = "+"
    elif "WIN_T1" in outcome:
        header = "✅ الهدف الأول حُقِّق!"
        ref_price = entry["target1"]
        diff = abs(ref_price - ep)
        sign = "+"
    elif "LOSS" in outcome:
        header = "❌ الوقف ضُرب"
        ref_price = entry["stop"]
        diff = abs(ep - ref_price)
        sign = "-"
    else:
        return

    pct = round(diff / ep * 100, 2)
    msg = (
        f"{header}\n"
        f"📊 {entry['symbol']} | {direction_ar}\n\n"
        f"الدخول  : {ep:.2f}\n"
        f"النتيجة : {ref_price:.2f}\n"
        f"الفرق   : {sign}{diff:.2f} نقطة ({sign}{pct}%)\n\n"
        f"🕐 {entry['timestamp']}"
    )
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)


def _check_outcomes():
    log = _load_log()
    updated = False

    for entry in log:
        if entry.get("outcome") is not None:
            # لو كان WIN/LOSS ولم يُرسَل إشعاره بعد
            if entry.get("outcome") not in ("expired", "OPEN", None) \
               and not entry.get("notified") and entry.get("sent"):
                _send_outcome_msg(entry)
                entry["notified"] = True
                updated = True
            continue

        ts = datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")
        age = (datetime.now() - ts).total_seconds() / 60

        if age < 30:
            continue
        if age > 240:
            entry["outcome"] = "expired"
            updated = True
            continue

        try:
            hist = yf.Ticker(entry["symbol"]).history(period="1d", interval="5m")
            if hist.empty:
                continue
            hi = float(hist["High"].max())
            lo = float(hist["Low"].min())

            # تحقق من إصابة الأهداف
            if entry["direction"] == "call":
                t1_hit   = hi >= entry["target1"]
                t2_hit   = hi >= entry["target2"]
                stop_hit = lo <= entry["stop"]
            else:
                t1_hit   = lo <= entry["target1"]
                t2_hit   = lo <= entry["target2"]
                stop_hit = hi >= entry["stop"]

            # تنبيه Break-even: أُرسِل عند تحقق T1 لأول مرة
            if t1_hit and not entry.get("be_notified") and entry.get("sent"):
                _send_breakeven_alert(entry)
                entry["be_notified"] = True
                updated = True

            # النتيجة النهائية
            if t2_hit:         entry["outcome"] = "WIN_T2 ✅✅"
            elif t1_hit:       entry["outcome"] = "WIN_T1 ✅"
            elif stop_hit:     entry["outcome"] = "LOSS ❌"

            if entry.get("outcome"):
                updated = True
        except Exception:
            pass

    if updated:
        _write_log(log)


def _outcome_loop():
    # price_monitor (كل 45 ثانية) يدير دورة حياة الإشارات + تنبيهات Telegram.
    # هذا اللوب يبقى كشبكة أمان للـ expiry فقط (كل 30 دقيقة).
    while True:
        time.sleep(30 * 60)
        try:
            outcome_tracker.check_outcomes()   # expiry + safety net على Supabase
        except Exception:
            pass


# ─── Daily summary ────────────────────────────────────────────────────────────

_daily_summary_sent_date: str = ""

def _send_daily_summary() -> None:
    """يرسل ملخص اليوم على Telegram بعد إغلاق السوق."""
    if not db.is_configured():
        return
    try:
        et       = pytz.timezone(config.TIMEZONE)
        today    = datetime.now(et).strftime("%Y-%m-%d")
        signals  = db.get_all_signals(limit=300)
        today_s  = [s for s in signals
                    if str(s.get("created_at",""))[:10] == today]
        if not today_s:
            return

        decided  = [s for s in today_s
                    if s.get("status") in ("hit_t1","hit_t2","stopped")]
        wins     = [s for s in decided if s.get("status") in ("hit_t1","hit_t2")]
        losses   = [s for s in decided if s.get("status") == "stopped"]
        total_r  = round(sum(float(s.get("r_multiple") or 0) for s in decided), 2)
        open_cnt = len([s for s in today_s if s.get("status") == "open"])

        best = (max(decided, key=lambda x: float(x.get("r_multiple") or 0))
                if decided else None)
        worst= (min(decided, key=lambda x: float(x.get("r_multiple") or 0))
                if decided else None)

        r_sign  = "+" if total_r >= 0 else ""
        r_emoji = "📈" if total_r >= 0 else "📉"
        wr_pct  = round(len(wins)/len(decided)*100) if decided else 0

        lines = [
            f"📊 ملخص اليوم — {today}",
            f"{'─'*30}",
            f"إشارات: {len(today_s)}  |  محسومة: {len(decided)}  |  مفتوحة: {open_cnt}",
            f"✅ فوز: {len(wins)}  ❌ خسارة: {len(losses)}  🎯 WR: {wr_pct}%",
        ]
        if best:
            lines.append(
                f"⭐ الأفضل: {best['symbol']} {best['direction'].upper()}"
                f" +{float(best.get('r_multiple',0)):.1f}R"
            )
        if worst and worst != best:
            lines.append(
                f"👎 الأسوأ: {worst['symbol']} {worst['direction'].upper()}"
                f" {float(worst.get('r_multiple',0)):.1f}R"
            )
        lines += [
            f"{'─'*30}",
            f"{r_emoji} إجمالي اليوم: {r_sign}{total_r}R",
        ]
        send("\n".join(lines), config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
        print("📊 أُرسل ملخص اليوم")
    except Exception as e:
        print(f"  [daily_summary] {e}")


def _daily_summary_loop() -> None:
    """يفحص كل دقيقة — يرسل الملخص الساعة 3:50 م ET أيام الأسبوع."""
    global _daily_summary_sent_date
    et = pytz.timezone(config.TIMEZONE)
    while True:
        time.sleep(60)
        try:
            now       = datetime.now(et)
            today_str = now.strftime("%Y-%m-%d")
            if (now.weekday() < 5
                    and now.hour == 15 and now.minute >= 50
                    and today_str != _daily_summary_sent_date):
                _send_daily_summary()
                _daily_summary_sent_date = today_str
        except Exception as exc:
            print(f"  [daily_summary] {exc}")


# ─── Weekly report ────────────────────────────────────────────────────────────

_weekly_report_sent_date: str = ""   # تتبع تاريخ آخر تقرير أُرسل

def _weekly_report_loop():
    """يُرسل التقرير الأسبوعي كل جمعة بعد إغلاق السوق (3:50 م ET)."""
    global _weekly_report_sent_date
    et = pytz.timezone(config.TIMEZONE)
    while True:
        time.sleep(5 * 60)          # يفحص كل 5 دقائق
        try:
            now = datetime.now(et)
            today_str = now.strftime("%Y-%m-%d")
            # جمعة = weekday() 4  |  بعد 15:50
            if (now.weekday() == 4
                    and now.hour == 15 and now.minute >= 50
                    and today_str != _weekly_report_sent_date):
                print("📊 إرسال التقرير الأسبوعي ...")
                send_weekly_report(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
                _weekly_report_sent_date = today_str
        except Exception as exc:
            print(f"  [weekly_report] خطأ: {exc}")


# ─── Sound ────────────────────────────────────────────────────────────────────

def play_alert():
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        elif platform.system() == "Linux":
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"])
    except Exception:
        pass


# ─── Market hours ──────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    et  = pytz.timezone(config.TIMEZONE)
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=config.MARKET_OPEN_HOUR,  minute=config.MARKET_OPEN_MINUTE,  second=0, microsecond=0)
    close_t = now.replace(hour=config.MARKET_CLOSE_HOUR, minute=config.MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_t <= now <= close_t


def cooldown_ok(symbol: str) -> bool:
    if symbol not in last_signal:
        return True
    return (datetime.now() - last_signal[symbol]).total_seconds() / 60 >= config.SIGNAL_COOLDOWN_MINUTES


# ─── Scan ──────────────────────────────────────────────────────────────────────

def _correlated_group(sym: str):
    for i, grp in enumerate(config.CORRELATED_GROUPS):
        if sym in grp:
            return i
    return None


def _analyze_one(symbol: str, vix: float, sym_min: float):
    """تحليل أصل واحد — يُستدعى بشكل موازٍ."""
    if not cooldown_ok(symbol):
        return symbol, None
    try:
        return symbol, analyze(
            symbol,
            min_score=sym_min,
            high_confidence_threshold=config.HIGH_CONFIDENCE_THRESHOLD,
            min_rr=1.5,
            vix_value=vix,
        )
    except Exception as e:
        print(f"  [scan] {symbol}: {e}")
        return symbol, None


def scan():
    if not is_market_open():
        print(f"[{datetime.now().strftime('%H:%M')}] السوق مغلق.")
        return

    vix        = get_vix()
    watchlist  = _load_watchlist()
    thresholds = _load_thresholds()
    ts         = datetime.now().strftime('%H:%M:%S')

    print(f"\n[{ts}] فحص {len(watchlist)} أصل بالتوازي  |  VIX: {vix:.1f}")
    if vix > 32:
        print("  ⚠️  VIX مرتفع — الحد الأدنى رُفع تلقائياً")

    # ── تحليل موازٍ ───────────────────────────────────────────────────────────
    signals: list = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {
            ex.submit(_analyze_one, sym, vix, thresholds.get(sym, _get_min_score())): sym
            for sym in watchlist
        }
        for future in as_completed(futures):
            symbol, signal = future.result()
            if signal:
                d = 'CALL' if signal.direction == 'call' else 'PUT'
                print(f"  → {symbol} {d} | تقييم: {signal.score:.1f} | R:R {signal.rr:.1f} | MTF: {signal.mtf_score}/3")
                signals.append((symbol, signal))
            else:
                print(f"  → {futures[future]} —")

    # ── إرسال (مرتّب حسب السكور + فلتر الارتباط) ────────────────────────────
    signals.sort(key=lambda x: x[1].score, reverse=True)
    sent_groups: set = set()
    sent = 0

    for symbol, signal in signals:
        grp_id = _correlated_group(symbol)
        if grp_id is not None and grp_id in sent_groups:
            print(f"  → {symbol} ⛔ تخطّى (ارتباط)")
            continue

        msg = format_message(signal)
        try:
            df_chart = _dc.get_bars(symbol, '5m', '2d')
            chart    = generate_signal_chart(df_chart, signal)
        except Exception:
            chart = b""
        ok = send_photo(chart, msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
        log_signal(signal, ok)

        if ok:
            last_signal[symbol] = datetime.now()
            sent += 1
            print(f"     ✓ أُرسلت {symbol}")
            if signal.confidence == "high":
                play_alert()
            if grp_id is not None:
                sent_groups.add(grp_id)
        else:
            print(f"     ✗ فشل {symbol}")

    print(f"اكتمل — {sent} إشارة/إشارات.\n")


# ─── Pre-market scan ─────────────────────────────────────────────────────────

_premarket_sent_date: str = ""   # تتبع تاريخ آخر مسح أُرسل

def _run_premarket_scan():
    """يفحص جميع الأصول ويرسل ملخص أقوى الفرص قبل افتتاح السوق."""
    global _premarket_sent_date

    et     = pytz.timezone(config.TIMEZONE)
    today  = datetime.now(et).strftime("%Y-%m-%d")
    if today == _premarket_sent_date:
        return
    _premarket_sent_date = today

    vix       = get_vix()
    watchlist = _load_watchlist()
    results   = []

    print("🌅 مسح ما قبل السوق ...")
    for sym in watchlist:
        try:
            data = quick_scan(sym)
            if data:
                results.append(data)
        except Exception:
            pass
        time.sleep(1)

    if not results:
        return

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

    lines = [
        f"🌅 مسح ما قبل السوق — {datetime.now(et).strftime('%H:%M')} ET",
        f"{'─' * 33}",
        f"📊 VIX: {vix:.1f}  {'⚠️ مرتفع' if vix > 25 else '✅ طبيعي'}",
        "",
        "🔍 أقوى الفرص اليوم:",
    ]

    for r in top:
        d_ar    = "كول 🟢" if r["direction"] == "call" else "بوت 🔴"
        score   = round(r["score"], 1)
        rsi     = round(r.get("rsi", 0), 1)
        conf_emoji = "⭐" if score >= config.HIGH_CONFIDENCE_THRESHOLD else ""
        lines.append(f"  {r['symbol']:<6} {d_ar}  |  تقييم: {score}  |  RSI: {rsi} {conf_emoji}")

    # تحذيرات الإيرادات
    from analyzer import has_earnings_soon
    earning_warns = [s for s in watchlist if has_earnings_soon(s, days=2)]
    if earning_warns:
        lines.append("")
        lines.append(f"⚠️  إيرادات قريبة — تجنّب: {', '.join(earning_warns)}")

    lines += [
        "",
        f"⏰ السوق يفتح الساعة 9:30 ص ET",
        "🤖 البوت يبدأ الفحص عند 9:35 ص تلقائياً",
    ]

    msg = "\n".join(lines)
    send(msg, config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
    print("✅ أُرسل مسح ما قبل السوق")


def _premarket_loop():
    """يفحص كل دقيقة إذا حان وقت مسح ما قبل السوق (9:00 ص ET، أيام عمل)."""
    et = pytz.timezone(config.TIMEZONE)
    while True:
        time.sleep(60)
        try:
            now = datetime.now(et)
            # أيام الأسبوع فقط، الساعة 9:00 ص بالضبط
            if (now.weekday() < 5
                    and now.hour == 9 and now.minute == 0):
                _run_premarket_scan()
        except Exception as exc:
            print(f"  [premarket] خطأ: {exc}")


# ─── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not config.TELEGRAM_TOKEN:
        print("❌ أضف TELEGRAM_TOKEN في .env"); return
    if not config.TELEGRAM_CHAT_ID:
        print("❌ أضف TELEGRAM_CHAT_ID في .env"); return

    print("🤖 Trading Signal Bot  v3.0")
    print(f"   الأصول    : {', '.join(_load_watchlist())}")
    print(f"   الفحص كل  : {config.SCAN_INTERVAL_MINUTES} دقيقة")
    print(f"   Dashboard  : streamlit run dashboard.py\n")

    threading.Thread(target=_outcome_loop,        daemon=True).start()
    threading.Thread(target=_weekly_report_loop,  daemon=True).start()
    threading.Thread(target=_daily_summary_loop,  daemon=True).start()
    threading.Thread(target=_premarket_loop,      daemon=True).start()
    price_monitor.start()
    start_command_listener(scan_callback=scan)

    scan()
    while True:
        time.sleep(config.SCAN_INTERVAL_MINUTES * 60)
        scan()


if __name__ == "__main__":
    main()
