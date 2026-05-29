"""
Outcome Tracker
يتتبع نتائج الإشارات المفتوحة في Supabase ويحدّثها تلقائياً.

المنطق:
  - يجلب كل الإشارات بـ status='open'
  - لكل إشارة يجلب أعلى/أدنى سعر منذ الإرسال
  - يحدّث النتيجة: hit_t1 | hit_t2 | stopped | expired
  - R-Multiple:  hit_t2 = R:R كامل | hit_t1 = R:R × 0.5 | stopped = −1R
"""

from __future__ import annotations

import datetime
import data_client as dc
import db

MAX_AGE_HOURS = 48   # إشارات أقدم من يومين → منتهية الصلاحية


def check_outcomes() -> None:
    """نقطة الدخول الرئيسية — يستدعيها main.py كل 30 دقيقة."""
    if not db.is_configured():
        return

    open_signals = db.get_open_signals()
    if not open_signals:
        return

    print(f"  [tracker] فحص {len(open_signals)} إشارة مفتوحة ...")
    for sig in open_signals:
        try:
            _check_one(sig)
        except Exception as e:
            sym = sig.get("symbol", "?")
            print(f"  [tracker] خطأ في {sym}: {e}")


def _check_one(sig: dict) -> None:
    symbol    = sig["symbol"]
    direction = sig.get("direction", "")
    entry     = float(sig.get("entry_price") or 0)
    stop      = float(sig.get("stop_price")  or 0)
    target1   = float(sig.get("target1")     or 0)
    target2   = float(sig.get("target2")     or 0)
    rr        = float(sig.get("rr")          or 1.5)
    sig_id    = sig["id"]

    if entry <= 0 or stop <= 0:
        return

    # ── تحقق من العمر ────────────────────────────────────────────────────────
    try:
        created_str = sig.get("created_at", "")
        # Supabase يُرجع مثلاً: "2025-05-29T14:00:00+00:00"
        created = datetime.datetime.fromisoformat(
            created_str.replace("Z", "+00:00")
        )
        age_hours = (
            datetime.datetime.now(datetime.timezone.utc) - created
        ).total_seconds() / 3600
    except Exception:
        age_hours = 0

    if age_hours > MAX_AGE_HOURS:
        db.update_outcome(sig_id, "expired", 0.0, 0.0)
        print(f"  [tracker] ⏰ {symbol} منتهية ({age_hours:.0f}h)")
        return

    # ── جلب بيانات السعر ─────────────────────────────────────────────────────
    try:
        df = dc.get_bars(symbol, "5m", "2d")
        if df.empty:
            return

        # فلتر: فقط الشموع بعد وقت الإشارة
        try:
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df = df[df.index >= created]
        except Exception:
            pass  # إذا فشل الفلتر استخدم كل البيانات

        if df.empty:
            return

        hi = float(df["High"].max())
        lo = float(df["Low"].min())
    except Exception:
        return

    # ── قرار النتيجة ─────────────────────────────────────────────────────────
    if direction == "call":
        if hi >= target2:
            db.update_outcome(sig_id, "hit_t2", target2, round(rr, 3))
            print(f"  [tracker] ✅✅ {symbol} CALL → T2  +{rr:.2f}R")
        elif hi >= target1:
            db.update_outcome(sig_id, "hit_t1", target1, round(rr * 0.5, 3))
            print(f"  [tracker] ✅  {symbol} CALL → T1  +{rr*0.5:.2f}R")
        elif lo <= stop:
            db.update_outcome(sig_id, "stopped", stop, -1.0)
            print(f"  [tracker] ❌  {symbol} CALL → Stop  −1R")

    elif direction == "put":
        if lo <= target2:
            db.update_outcome(sig_id, "hit_t2", target2, round(rr, 3))
            print(f"  [tracker] ✅✅ {symbol} PUT  → T2  +{rr:.2f}R")
        elif lo <= target1:
            db.update_outcome(sig_id, "hit_t1", target1, round(rr * 0.5, 3))
            print(f"  [tracker] ✅  {symbol} PUT  → T1  +{rr*0.5:.2f}R")
        elif hi >= stop:
            db.update_outcome(sig_id, "stopped", stop, -1.0)
            print(f"  [tracker] ❌  {symbol} PUT  → Stop  −1R")
