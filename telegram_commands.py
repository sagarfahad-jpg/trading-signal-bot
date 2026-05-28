#!/usr/bin/env python3
"""
Telegram Command Listener — يستقبل أوامر من المستخدم مباشرة في الخاص.

الأوامر:
  /help       — قائمة الأوامر
  /status     — حالة البوت وإحصائيات اليوم
  /scan       — فحص فوري للأصول
  /report     — تقرير الأداء (آخر 7 أيام)
  /watchlist  — قائمة الأصول المراقبة حالياً

طريقة الاستخدام: أرسل الأمر مباشرة لبوت التيليغرام في الخاص.
"""

import requests
import threading
import time
import json
import os
from datetime import datetime

import config


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _get_updates(token: str, offset: int = 0, timeout: int = 20) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 5,
        )
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception:
        pass
    return []


def _reply(token: str, chat_id, text: str):
    """يرسل رداً على أمر."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


# ─── Command handlers ─────────────────────────────────────────────────────────

def _cmd_help(token, chat_id, **_):
    msg = (
        "🤖 أوامر البوت المتاحة:\n\n"
        "/status     — حالة البوت وإحصائيات اليوم\n"
        "/scan       — فحص فوري للأصول الآن\n"
        "/report     — تقرير الأداء (آخر 7 أيام)\n"
        "/watchlist  — قائمة الأصول المراقبة\n"
        "/help       — هذه القائمة\n\n"
        "📌 أرسل الأمر مباشرة هنا في الخاص."
    )
    _reply(token, chat_id, msg)


def _cmd_status(token, chat_id, **_):
    log_file = os.path.join(os.path.dirname(__file__), "signals_log.json")
    today    = datetime.now().strftime("%Y-%m-%d")
    total_today = wins = losses = 0

    try:
        with open(log_file, encoding="utf-8") as f:
            log = json.load(f)
        for e in log:
            if e.get("timestamp", "").startswith(today) and e.get("sent"):
                total_today += 1
                o = e.get("outcome", "") or ""
                if "WIN" in o:   wins   += 1
                if "LOSS" in o:  losses += 1
    except Exception:
        pass

    open_signals = total_today - wins - losses
    watchlist_file = os.path.join(os.path.dirname(__file__), "watchlist.json")
    wl_count = 0
    try:
        with open(watchlist_file, encoding="utf-8") as f:
            wl_count = len(json.load(f))
    except Exception:
        wl_count = len(config.WATCHLIST)

    msg = (
        f"📡 حالة البوت — {today}\n"
        f"─────────────────────\n"
        f"✅ يعمل بشكل طبيعي\n"
        f"📊 الأصول المراقبة  : {wl_count}\n"
        f"⏱  الفحص كل         : {config.SCAN_INTERVAL_MINUTES} دقيقة\n"
        f"──── إشارات اليوم ────\n"
        f"📨 مُرسَلة           : {total_today}\n"
        f"✅ WIN               : {wins}\n"
        f"❌ LOSS              : {losses}\n"
        f"⏳ مفتوحة            : {open_signals}\n"
    )
    _reply(token, chat_id, msg)


def _cmd_scan(token, chat_id, scan_callback, **_):
    _reply(token, chat_id, "🔍 بدأ الفحص الفوري...\nسيُرسَل كل إشارة وجدها مباشرة على القناة.")
    if scan_callback:
        threading.Thread(target=scan_callback, daemon=True).start()


def _cmd_report(token, chat_id, **_):
    from weekly_report import generate_weekly_report
    report = generate_weekly_report(days=7)
    _reply(token, chat_id, report)


def _cmd_watchlist(token, chat_id, **_):
    wf = os.path.join(os.path.dirname(__file__), "watchlist.json")
    try:
        with open(wf, encoding="utf-8") as f:
            wl = json.load(f)
    except Exception:
        wl = list(config.WATCHLIST)

    threshold_file = os.path.join(os.path.dirname(__file__), "asset_thresholds.json")
    thresholds = {}
    try:
        with open(threshold_file, encoding="utf-8") as f:
            thresholds = json.load(f)
    except Exception:
        pass

    lines = [f"📋 الأصول المراقبة ({len(wl)}):\n"]
    for sym in wl:
        thresh = thresholds.get(sym, config.MIN_SCORE)
        lines.append(f"  • {sym:<6}  (حد: {thresh})")

    lines.append(f"\n⚙️  الحد الافتراضي: {config.MIN_SCORE}")
    _reply(token, chat_id, "\n".join(lines))


# ─── Dispatcher ───────────────────────────────────────────────────────────────

COMMANDS = {
    "/help":      _cmd_help,
    "/start":     _cmd_help,
    "/status":    _cmd_status,
    "/scan":      _cmd_scan,
    "/report":    _cmd_report,
    "/watchlist": _cmd_watchlist,
}


# ─── Polling loop ─────────────────────────────────────────────────────────────

def start_command_listener(scan_callback=None):
    """
    يُشغّل حلقة الاستماع للأوامر في خيط خلفي (daemon thread).
    scan_callback: دالة يستدعيها الأمر /scan
    """
    token = config.TELEGRAM_TOKEN
    if not token:
        return

    def _loop():
        offset = 0
        print("📨 مستمع الأوامر يعمل — أرسل /help للبوت في الخاص")

        while True:
            updates = _get_updates(token, offset=offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = msg.get("chat", {}).get("id")

                if not text or not chat_id:
                    continue

                # استخرج الأمر (يتجاهل @bot_name)
                cmd = text.split("@")[0].split()[0].lower()
                handler = COMMANDS.get(cmd)
                if handler:
                    print(f"  [cmd] {cmd} ← chat_id={chat_id}")
                    try:
                        handler(
                            token=token,
                            chat_id=chat_id,
                            scan_callback=scan_callback,
                        )
                    except Exception as exc:
                        print(f"  [cmd] خطأ في تنفيذ {cmd}: {exc}")

            if not updates:
                time.sleep(2)

    threading.Thread(target=_loop, daemon=True).start()
