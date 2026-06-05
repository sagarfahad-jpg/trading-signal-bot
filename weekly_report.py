#!/usr/bin/env python3
"""
Weekly Performance Report — يولّد ويرسل ملخص الأسبوع على Telegram كل جمعة.
المصدر الأساسي: Supabase (مطابق للداشبورد). Fallback: signals_log.json محلياً.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import db

LOG_FILE = os.path.join(os.path.dirname(__file__), "signals_log.json")

# تحويل status من Supabase إلى outcome legacy
_STATUS_MAP = {
    "hit_t2":  "WIN_T2",
    "hit_t1":  "WIN_T1",
    "stopped": "LOSS",
    "expired": "expired",
    "open":    "",
}


def _parse_ts(s: str) -> datetime | None:
    """يقبل ISO (Supabase) أو 'YYYY-MM-DD HH:MM:SS' (JSON قديم)."""
    if not s:
        return None
    s = str(s)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def _load_from_supabase() -> list:
    """يجلب الإشارات من Supabase ويحوّلها لصيغة موحّدة."""
    if not db.is_configured():
        return []
    try:
        raw = db.get_all_signals(limit=500)
    except Exception:
        return []
    out = []
    for r in raw or []:
        out.append({
            "timestamp": str(r.get("created_at", ""))[:19].replace("T", " "),
            "symbol":    r.get("symbol", "—"),
            "direction": r.get("direction", ""),
            "outcome":   _STATUS_MAP.get(r.get("status", "open"), ""),
            "rr":        r.get("rr"),
            "sent":      True,
        })
    return out


def _load_from_json() -> list:
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _load_log() -> tuple[list, str]:
    """يُرجع (entries, source) حيث source هو 'supabase' أو 'json' أو 'empty'."""
    entries = _load_from_supabase()
    if entries:
        return entries, "supabase"
    entries = _load_from_json()
    if entries:
        return entries, "json"
    return [], "empty"


def generate_weekly_report(days: int = 7) -> str:
    """يولّد نص تقرير الأداء الأسبوعي."""
    log, source = _load_log()
    cutoff = datetime.now() - timedelta(days=days)

    week_entries = []
    for e in log:
        if not e.get("sent", True):
            continue
        ts = _parse_ts(e.get("timestamp", ""))
        if ts and ts >= cutoff:
            week_entries.append(e)

    total = len(week_entries)
    if total == 0:
        return (
            "📊 التقرير الأسبوعي\n"
            "─────────────────\n"
            "لا توجد إشارات مُرسَلة هذا الأسبوع."
        )

    outcomes = [e.get("outcome", "") or "" for e in week_entries]
    wins_t2  = sum(1 for o in outcomes if "WIN_T2" in o)
    wins_t1  = sum(1 for o in outcomes if "WIN_T1" in o and "WIN_T2" not in o)
    losses   = sum(1 for o in outcomes if "LOSS" in o)
    expired  = sum(1 for o in outcomes if o == "expired")
    open_s   = total - wins_t2 - wins_t1 - losses - expired

    resolved = wins_t2 + wins_t1 + losses
    wr       = round(((wins_t2 + wins_t1) / resolved * 100), 1) if resolved else 0.0

    # إحصاءات لكل أصل
    sym_stats: dict = defaultdict(lambda: {"wins": 0, "losses": 0, "signals": 0})
    for e in week_entries:
        sym = e.get("symbol", "—")
        o   = e.get("outcome", "") or ""
        sym_stats[sym]["signals"] += 1
        if "WIN" in o:
            sym_stats[sym]["wins"] += 1
        elif "LOSS" in o:
            sym_stats[sym]["losses"] += 1

    # أفضل / أسوأ أصل — فقط لو في نتائج محسومة فعلية
    syms_with_wins   = [s for s, st in sym_stats.items() if st["wins"]   > 0]
    syms_with_losses = [s for s, st in sym_stats.items() if st["losses"] > 0]

    if syms_with_wins:
        best_sym = max(syms_with_wins, key=lambda s: sym_stats[s]["wins"])
        best_wr  = round(sym_stats[best_sym]["wins"] / sym_stats[best_sym]["signals"] * 100)
        best_line = f"📈 أفضل أصل          : {best_sym}  ({best_wr}% نجاح)"
    else:
        best_line = "📈 أفضل أصل          : — (لا نتائج محسومة)"

    if syms_with_losses:
        worst_sym = max(syms_with_losses, key=lambda s: sym_stats[s]["losses"])
        worst_wr  = round(sym_stats[worst_sym]["losses"] / sym_stats[worst_sym]["signals"] * 100)
        worst_line = f"📉 أضعف أصل          : {worst_sym}  ({worst_wr}% خسارة)"
    else:
        worst_line = "📉 أضعف أصل          : — (لا خسائر محسومة)"

    rr_vals = [e.get("rr") for e in week_entries if e.get("rr")]
    best_rr = round(max(rr_vals), 2) if rr_vals else "—"

    calls = sum(1 for e in week_entries if e.get("direction") == "call")
    puts  = total - calls
    trend_ar = "كول 🟢" if calls >= puts else "بوت 🔴"

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
        best_line,
        worst_line,
        f"⚡ أعلى R:R           : {best_rr}",
        f"🔮 الاتجاه السائد    : {trend_ar}  ({max(calls, puts)}/{total})",
        "─────────────────────────────",
    ]

    # تفصيل لكل أصل — يميّز بين المحسوم والمفتوح
    lines.append("📋 تفصيل الأصول:")
    for sym, st in sorted(sym_stats.items(), key=lambda x: -x[1]["signals"]):
        sym_resolved = st["wins"] + st["losses"]
        if sym_resolved > 0:
            sym_wr = round(st["wins"] / sym_resolved * 100)
            detail = f"نجاح {sym_wr}% ({st['wins']}/{sym_resolved})"
        else:
            detail = f"مفتوحة ({st['signals']})"
        lines.append(f"  {sym:<6}  {st['signals']} إشارة  |  {detail}")

    lines.append("")
    if source == "json":
        lines.append("⚠️ المصدر: ملف محلي (Supabase غير متاح)")
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
