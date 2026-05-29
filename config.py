import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Alpaca — بيانات لحظية (مجاني مع حساب Paper Trading)
# سجّل على: https://alpaca.markets
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

# الأصول المراقبة
WATCHLIST = [
    "QQQ", "SPY", "NVDA", "AAPL", "TSLA",
    "AMZN", "META", "GOOGL", "MSFT", "AMD"
]

# مجموعات الارتباط — لا يُرسَل أكثر من إشارة واحدة من كل مجموعة في نفس المسح
CORRELATED_GROUPS = [
    ["QQQ", "SPY"],          # كلاهما يتبع نفس مؤشر ناسداك/S&P
    ["GOOGL", "META"],       # قطاع الإعلانات الرقمية
]

# إدارة المخاطر — Position Sizing
ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "10000"))  # حجم الحساب بالدولار
RISK_PCT     = 0.01   # نسبة المخاطرة لكل صفقة (1%)

# إعدادات المسح
SCAN_INTERVAL_MINUTES = 15
MIN_SCORE = 5.5               # الحد الأدنى لإرسال الإشارة (من 10)
HIGH_CONFIDENCE_THRESHOLD = 7.5
SIGNAL_COOLDOWN_MINUTES = 45  # لا يُعاد إرسال إشارة لنفس الأصل خلال هذه المدة

# ساعات السوق (توقيت نيويورك)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 35
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 45
TIMEZONE = "America/New_York"
