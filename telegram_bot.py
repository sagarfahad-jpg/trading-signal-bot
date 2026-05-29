import requests
from analyzer import SignalResult


def format_message(s: SignalResult) -> str:
    dir_ar    = 'كول'   if s.direction  == 'call'   else 'بوت'
    dir_emoji = '🟢'    if s.direction  == 'call'   else '🔴'
    conf_ar   = 'عالية' if s.confidence == 'high'   else 'متوسطة'
    conf_emoji= '🟢'    if s.confidence == 'high'   else '🟡'
    scalp_line = '⚡ مناسبة للمضاربة السريعة\n' if s.is_scalp else ''

    return (
        f"🤖 رسالة من البوت الآلي\n"
        f"📊 إشارة تداول {s.symbol}\n"
        f"\n"
        f"الاتجاه: {dir_ar} {dir_emoji}\n"
        f"درجة الثقة: {conf_ar} {conf_emoji}\n"
        f"⏰ صلاحية العقد: 0DTE\n"
        f"\n"
        f"⚙️ خطة التنفيذ:\n"
        f"💠 نوع الدخول: {s.entry_type}\n"
        f"💠 منطقة الدخول: {s.entry_low:.2f} – {s.entry_high:.2f}\n"
        f"💠 سعر الدخول المقترح: {round((s.entry_low + s.entry_high) / 2, 2):.2f} ◀️\n"
        f"💠 مستوى الوقف: {s.stop:.2f}\n"
        f"💠 الهدف الأول: {s.target1:.2f}\n"
        f"💠 الهدف الثاني: {s.target2:.2f}\n"
        f"{scalp_line}"
        f"\n"
        f"📋 العقد المقترح:\n"
        f"Expiry: {s.expiry} | Strike: {s.strike:.1f} | {dir_ar} {dir_emoji}\n"
        + (f"💰 سعر العقد (Premium): ~${s.option_price:.2f}\n" if s.option_price > 0 else "")
        + f"\n"
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
