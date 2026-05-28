#!/usr/bin/env python3
"""
Weekly Performance Report — يولّد ويرسل ملخص الأسبوع على Telegram كل جمعة.
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

LOG_FILE = os.path.join(os.path.dirname(__file__), "signals_log.json")


def _load_log() -> list:
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def generate_weekly_report(days: int = 7) -> str:
    """يولّد نص تقرير الأداء الأسبوعي."""
    log   = _load_log()
    cutoff = datetime.now() - timedelta(days=days)

    # تصفية إشارات الأسبوع المُرسَلة فعلاً
    week_entries = []
    for e in log:
        if not e.get("sent"):
            continue
        try:
            ts = datetime.strptime(e["timestamp"], "%Y-%m-%d %H:%M:%S")
            if ts >= cutoff:
                week_entries.append(e)
        except Exception:
            pass

    total  = len(week_entries)
    if total == 0:
        return (
            "📊 التقرير الأسبوعي\n"
            "─────────────────\n"
            "لا توجد إشارات مُرسَلة هذا الأسبوع."
        )

    # حساب النتائج
    outcomes = [e.get("outcome", "") or "" for e in week_entries]
    wins_t2  = sum(1 for o in outcomes if "WIN_T2" in o)
    wins_t1  = sum(1 for o in outcomes if "WIN_T1" in o and "WIN_T2" not in o)
    losses   = sum(1 for o in outcomes if "LOSS"   in o)
    open_s   = sum(1 for o in outcomes if o in ("", "OPEN", None))
    expired  = sum(1 for o in outcomes if o == "expired")

    resolved = wins_t2 + wins_t1 + losses
    wr        = round(((wins_t2 + wins_t1) / resolved * 100) if resolved else 0, 1)

    # أفضل / أسوأ أصل
    sym_stats: dict = defaultdict(lambda: {"wins": 0, "losses": 0, "signals": 0})
    for e in week_entries:
        sym = e.get("symbol", "—")
        o   = e.get("outcome", "") or ""
        sym_stats[sym]["signals"] += 1
        if "WIN" in o:
            sym_stats[sym]["wins"] += 1
        elif "LOSS" in o:
            sym_stats[sym]["losses"] += 1

    best_sym  = max(sym_stats, key=lambda s: sym_stats[s]["wins"])   if sym_stats else "—"
    worst_sym = max(sym_stats, key=lambda s: sym_stats[s]["losses"]) if sym_stats else "—"

    best_wr  = round(sym_stats[best_sym]["wins"]   / sym_stats[best_sym]["signals"]  * 100, 0) if sym_stats else 0
    worst_wr = round(sym_stats[worst_sym]["losses"] / sym_stats[worst_sym]["signals"] * 100, 0) if sym_stats else 0

    # أعلى R:R
    rr_vals  = [e.get("rr") for e in week_entries if e.get("rr")]
    best_rr  = round(max(rr_vals), 2) if rr_vals else "—"

    # الاتجاه السائد
    calls = sum(1 for e in week_entries if e.get("direction") == "call")
    puts  = total - calls
    trend_ar = "كول 🟢" if calls >= puts else "بوت 🔴"

    # ─── بناء الرسالة ────────────────────────────────────────────────
    from_dt = cutoff.strftime("%m/%d")
    to_dt   = datetime.now().strftime("%m/%d")

    lines = [
        f"📊 التقرير الأسبوعي  {from_dt} – {to_dt}",
        "─────────────────────────────",
        f"📨 الإشارات المُرسَلة : {total}",
        f"✅ WIN T2             : {wins_t2}",
        f"✅ WIN T1             : {wins_t1}",
        f"❌ LOSS               : {losses}",
        f"⏳ لم تُحسم           : {open_s + expired}",
        "─────────────────────────────",
        f"🎯 نسبة النجاح        : {wr}%  ({wins_t2 + wins_t1}/{resolved})",
        f"📈 أفضل أصل          : {best_sym}  ({best_wr:.0f}% نجاح)",
        f"📉 أضعف أصل          : {worst_sym}  ({worst_wr:.0f}% خسارة)",
        f"⚡ أعلى R:R           : {best_rr}",
        f"🔮 الاتجاه السائد    : {trend_ar}  ({max(calls, puts)}/{total})",
        "─────────────────────────────",
    ]

    # تفاصيل لكل أصل
    lines.append("📋 تفصيل الأصول:")
    for sym, st in sorted(sym_stats.items(), key=lambda x: -x[1]["signals"]):
        sym_resolved = st["wins"] + st["losses"]
        sym_wr = f"{round(st['wins']/sym_resolved*100)}%" if sym_resolved else "—"
        lines.append(f"  {sym:<6}  {st['signals']} إشارة  |  نجاح {sym_wr}")

    lines.append("")
    lines.append("🤖 تقرير تلقائي — بوت التداول الآلي")

    return "\n".join(lines)


def send_weekly_report(token: str, chat_id: str):
    """يولّد التقرير ويرسله على Telegram."""
    from telegram_bot import send
    msg = generate_weekly_report()
    ok  = send(msg, token, chat_id)
    if ok:
        print("✅ تم إرسال التقرير الأسبوعي على Telegram")
    else:
        print("❌ فشل إرسال التقرير الأسبوعي")
    return ok


if __name__ == "__main__":
    import config
    print(generate_weekly_report())
    print("\n--- إرسال على Telegram ---")
    send_weekly_report(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
