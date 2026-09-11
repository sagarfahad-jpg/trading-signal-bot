"""
Outcome Tracker — شبكة أمان للانتهاء فقط (منذ 2026-09-11).

الحكم على النتائج (T1/T2/Stop) أُزيل: كان يقيس أعلى/أدنى شمعة 5m من وقت الإشارة
لا الدخول، ويفحص الهدف قبل الوقف، فسجّل "فوزاً" لصفقات ضُرب ستوبها
(220 صفقة بفوز 78% مقابل 24% للحكم الحي). price_monitor هو الحكم الوحيد الآن.

ما بقي: إنهاء الصفقات المفتوحة التي مات عقدها دون أن يلمس السعر مستوى عند أي فحص،
حتى لا تبقى مفتوحة إلى الأبد (تحجب إشارات الرمز عبر has_open_signal وتُراقَب بعقد ميت).
الكتابة مشروطة بـ status='open' لحظة التنفيذ (فلتر على الخادم) — لا كتابة مزدوجة.
"""

from __future__ import annotations

import datetime
import pytz
import db

ET            = pytz.timezone("America/New_York")
MAX_AGE_HOURS = 48         # احتياط فقط لصفوف بلا expiry صالح
CLOSE_CUTOFF  = (16, 15)   # بعد هذا الوقت ET يُعتبر عقد اليوم منتهياً


def _expiry_date(sig: dict) -> datetime.date | None:
    """expiry محفوظ كـ YYYYMMDD (أو YYYY-MM-DD)."""
    s = str(sig.get("expiry") or "").replace("-", "")[:8]
    try:
        return datetime.datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_ts(value) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _age_hours(sig: dict, now: datetime.datetime) -> float:
    created = _parse_ts(sig.get("created_at", ""))
    return (now - created).total_seconds() / 3600 if created else 0.0


def _contract_dead(sig: dict, now_et: datetime.datetime) -> bool:
    """هل مات عقد الصفقة؟ (انتهاء أقدم من اليوم ET، أو اليوم نفسه بعد الإغلاق)."""
    exp = _expiry_date(sig)
    if exp is None:
        return _age_hours(sig, now_et) > MAX_AGE_HOURS
    if exp < now_et.date():
        return True
    return exp == now_et.date() and (now_et.hour, now_et.minute) >= CLOSE_CUTOFF


def expire_stale() -> None:
    """نقطة الدخول — يستدعيها main.py كل 30 دقيقة. لا يحكم على نتيجة، يُنهي فقط."""
    if not db.is_configured():
        return
    now_et = datetime.datetime.now(ET)
    for sig in db.get_open_signals():          # status = 'open'
        if not sig.get("entry_filled"):
            continue   # المعلّقة يلغيها price_monitor بعد 24 ساعة (expired_no_entry)
        if not _contract_dead(sig, now_et):
            continue
        t0  = _parse_ts(sig.get("entry_time") or sig.get("created_at"))
        dur = int((now_et - t0).total_seconds() / 60) if t0 else None
        ok  = db.update_outcome(
            sig["id"], "expired", 0.0, 0.0,
            exit_reason="contract_expired", duration_min=dur,
            only_if_status="open",             # لا كتابة لو أغلقها price_monitor في هذه الأثناء
        )
        sym = sig.get("symbol", "?")
        if ok:
            print(f"  [tracker] ⌛ {sym} #{sig['id']} انتهى عقدها بلا حسم → expired")
        else:
            print(f"  [tracker] ↷ {sym} #{sig['id']} أُغلقت قبل الكتابة (أو فشل الطلب) — تُركت")
