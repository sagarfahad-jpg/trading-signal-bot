# مهام تحسين مؤجلة (بعد إصلاح price accuracy)

## متابعة فورية (الجمعة 2026-06-12)

- [x] git push 2a3fdac (10:32 UTC)
- [ ] **4:30 PM جدة (13:30 UTC):** مراقبة market open مع الكود الجديد
- [ ] مقارنة وقت أول signal alert على Telegram مع شموع TradingView
- [ ] لو ظهر `401` أو `fallback to yfinance` → rollback فوراً بـ:
  ```
  cd /Users/saqeralruqi/Desktop/trading_signal_bot && \
  git revert 2a3fdac --no-edit && git push origin main
  ```


## تنسيق وعرض

- [ ] **إصلاح RTL للنص العربي في matplotlib** (`chart_generator.py`)
  - استخدام `arabic_reshaper` + `python-bidi`
  - مشكلة موجودة قبل تعديلات 2026-06-12
- [ ] **رفع timestamp قليلاً عن volume panel** في الشارت
  - يتداخل حالياً مع أشرطة الـ volume

## بيئة Railway

- [ ] **تحديث مفاتيح Alpaca على Railway env vars**
  - السبب: 401 Unauthorized في Railway logs
  - المفاتيح المحلية في `.env` صالحة وتعمل ✅
  - الخطوات في تعليمات الجلسة الأخيرة

## تحسينات مستقبلية

- [ ] إضافة **WebSocket** لـ Alpaca (تأخير ثوانٍ بدل 45 ثانية polling)
- [ ] تقليل `POLL_SECONDS` إلى 15 ثانية كـ safety net
- [ ] إرسال شارت مع تنبيهات الدخول/T1/T2 (حالياً مع الإشارة الأولى فقط في `main.py`)
- [ ] إضافة **min-bars-between-events** لـ `detect_structure_events` (مثلاً 3 شموع) لتقليل ضوضاء الـ CHoCH في الأسواق الجانبية. ليس الآن — ننتظر بيانات أداء فعلية أولاً.
