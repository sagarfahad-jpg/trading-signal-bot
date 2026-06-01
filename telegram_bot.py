import requests
from analyzer import SignalResult


def format_message(s: SignalResult) -> str:
    dir_ar      = 'كول'    if s.direction  == 'call' else 'بوت'
    dir_emoji   = '🟢'     if s.direction  == 'call' else '🔴'
    conf_ar     = 'عالية'  if s.confidence == 'high' else 'متوسطة'
    conf_emoji  = '🟢'     if s.confidence == 'high' else '🟡'
    scalp_line  = '⚡ مناسبة للمضاربة السريعة\n' if s.is_scalp else ''
    expiry_type = '0DTE ⚡' if s.is_scalp else 'أسبوعي 📅'
    mid_entry   = round((s.entry_low + s.entry_high) / 2, 2)

    # ── Regime ────────────────────────────────────────────────────────────────
    regime_map  = {'bull': '📈 صاعد', 'bear': '📉 هابط', 'neutral': '↔ محايد'}
    regime_line = f"🌐 اتجاه السوق: {regime_map.get(s.regime, '—')}\n" if s.regime else ''

    # ── HTF Zone ──────────────────────────────────────────────────────────────
    htf_line = ""
    if s.htf_zone_tf:
        tf_ar    = {'1h': '١ ساعة', '4h': '٤ ساعات', 'daily': 'يومي'}.get(s.htf_zone_tf, s.htf_zone_tf)
        dir_z_ar = 'طلب 🟢' if s.htf_direction == 'demand' else 'عرض 🔴'
        confirm  = ' ⚡ CISD' if s.cisd else (' 💪 Displacement' if s.displacement else ' ⏳ في المنطقة')
        htf_line = f"🏛️ HTF Zone: {tf_ar} {s.htf_zone_type} ({dir_z_ar}){confirm}\n"

        # تحذير تعارض HTF مع الاتجاه
        htf_conflicts = (
            (s.direction == 'call' and s.htf_direction == 'supply') or
            (s.direction == 'put'  and s.htf_direction == 'demand')
        )
        if htf_conflicts:
            htf_line += f"⚠️ تحذير: HTF يعارض الاتجاه — تداول بحذر\n"

    # ── SMT ───────────────────────────────────────────────────────────────────
    smt_line = ""
    if s.smt_divergence:
        smt_dir_ar = "كول 🟢" if s.smt_direction == 'call' else "بوت 🔴"
        smt_emoji  = "✅ يؤكد" if s.smt_direction == s.direction else "⚠️ يعارض"
        smt_line   = f"📡 SMT (NAS100/SPX500): {smt_dir_ar} — {smt_emoji}\n"

    # ── Options Flow (مستويات المؤسسات) ───────────────────────────────────────
    flow_line = ""
    mp = getattr(s, "max_pain", 0)
    cw = getattr(s, "call_wall", 0)
    pw = getattr(s, "put_wall", 0)
    pcr = getattr(s, "pcr", 0)
    if mp or cw or pw:
        bias = ""
        if pcr:
            bias = " 🟢 تحيّز شراء" if pcr < 0.7 else (" 🔴 تحيّز بيع" if pcr > 1.3 else " ↔ محايد")
        flow_line = (
            f"🏦 مستويات المؤسسات:\n"
            f"   Max Pain: {mp:.0f} | Call Wall: {cw:.0f} 🎯 | Put Wall: {pw:.0f} 🛡\n"
            f"   P/C: {pcr:.2f}{bias}\n"
        )

    # ── VWAP ──────────────────────────────────────────────────────────────────
    vwap_line = ""
    if s.vwap > 0:
        pos = "فوق" if s.current_price > s.vwap else "تحت"
        vwap_line = f"📍 VWAP: {s.vwap:.2f} (السعر {pos} الـ VWAP)\n"

    # ── Greeks (يُخفى لو Delta = 0 — بيانات ناقصة) ──────────────────────────
    greeks_line = ""
    if abs(s.delta) > 0.01 and s.iv > 0:
        greeks_line = (
            f"📐 Greeks: Δ {s.delta:.2f} | "
            f"IV {s.iv:.1f}% | "
            f"Θ {s.theta:.3f}\n"
        )

    # ── Position Sizing ───────────────────────────────────────────────────────
    import config as _cfg
    try:
        import db as _db
        _acct = _db.get_account_size(_cfg.ACCOUNT_SIZE)
    except Exception:
        _acct = _cfg.ACCOUNT_SIZE
    risk_usd    = _acct * _cfg.RISK_PCT
    pos_line    = f"📦 حجم الصفقة: {s.contracts} عقد (مخاطرة ~${risk_usd:.0f})\n" if s.contracts > 0 else ""
    mtf_warn    = "⚠️ تحذير: لا تأكيد من أي فريم زمني\n" if s.mtf_score == 0 else ""

    return (
        f"🤖 إشارة تداول — {s.symbol}\n"
        f"{'━' * 28}\n"
        f"الاتجاه: {dir_ar} {dir_emoji}  |  الثقة: {conf_ar} {conf_emoji}\n"
        f"⏰ صلاحية العقد: {expiry_type}\n"
        f"{regime_line}"
        f"{htf_line}"
        f"{smt_line}"
        f"{flow_line}"
        f"\n"
        f"⚙️ خطة التنفيذ:\n"
        f"💠 نوع الدخول: {s.entry_type}\n"
        f"💠 منطقة الدخول: {s.entry_low:.2f} – {s.entry_high:.2f}\n"
        f"💠 سعر الدخول المقترح: {mid_entry:.2f} ◀️\n"
        f"💠 مستوى الوقف: {s.stop:.2f}\n"
        f"💠 الهدف الأول: {s.target1:.2f}\n"
        f"💠 الهدف الثاني: {s.target2:.2f}\n"
        f"📊 R:R = {s.rr:.2f}  |  Score: {s.score:.1f}★  |  MTF: {s.mtf_score}/3\n"
        f"{mtf_warn}"
        f"{scalp_line}"
        f"{vwap_line}"
        f"\n"
        f"📋 العقد المقترح:\n"
        f"Expiry: {s.expiry} | Strike: {s.strike:.1f} | {dir_ar} {dir_emoji}\n"
        + (f"💰 Premium: ~${s.option_price:.2f}\n" if s.option_price > 0 else "")
        + greeks_line
        + pos_line
        + f"{'━' * 28}\n"
        f"للمراقبة فقط — ليست توصية شراء"
    )


def send(text: str, token: str, chat_id: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                url,
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            if r.status_code == 200:
                return True
            print(f"  [telegram] HTTP {r.status_code}: {r.text[:120]}")
            return False
        except Exception as e:
            if attempt < retries:
                import time
                time.sleep(2)
            else:
                print(f"  [telegram] فشل بعد {retries} محاولات: {e}")
    return False


def send_photo(image_bytes: bytes, caption: str, token: str, chat_id: str) -> bool:
    """يرسل صورة الرسم البياني مع النص كـ caption."""
    if not image_bytes:
        return send(caption, token, chat_id)
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": ("chart.png", image_bytes, "image/png")},
            timeout=20,
        )
        if r.status_code == 200:
            return True
        print(f"  [telegram photo] HTTP {r.status_code}: {r.text[:120]}")
        # Fallback: أرسل النص فقط
        return send(caption, token, chat_id)
    except Exception as e:
        print(f"  [telegram photo] {e}")
        return send(caption, token, chat_id)
