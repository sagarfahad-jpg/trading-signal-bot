#!/usr/bin/env python3
"""
اختبر اتصال Telegram قبل تشغيل البوت الكامل.
شغّله مرة واحدة فقط:  python3 test_connection.py
"""

import requests
import config


def test_telegram():
    print("─" * 40)
    print("اختبار اتصال Telegram")
    print("─" * 40)

    if not config.TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN فارغ — افتح .env وأضفه")
        return False
    if not config.TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID فارغ — افتح .env وأضفه")
        return False

    # 1) التحقق من البوت
    r = requests.get(
        f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getMe",
        timeout=10,
    )
    if r.status_code != 200:
        print(f"❌ التوكن خاطئ أو منتهي الصلاحية (HTTP {r.status_code})")
        return False

    bot_name = r.json()["result"]["username"]
    print(f"✅ البوت متصل: @{bot_name}")

    # 2) إرسال رسالة تجريبية
    msg = (
        "🤖 اختبار البوت الآلي\n\n"
        "✅ الاتصال يعمل بشكل صحيح!\n"
        "البوت جاهز لإرسال إشارات التداول."
    )
    r2 = requests.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg},
        timeout=10,
    )
    if r2.status_code == 200:
        print("✅ رسالة تجريبية أُرسلت بنجاح إلى القناة/المجموعة")
        print("\n🚀 كل شيء جاهز — شغّل البوت بـ:  python3 main.py")
        return True
    else:
        err = r2.json().get("description", r2.text)
        print(f"❌ فشل إرسال الرسالة: {err}")
        print("تأكد أن Chat ID صحيح وأن البوت أُضيف للقناة/المجموعة كـ Admin")
        return False


if __name__ == "__main__":
    test_telegram()
