# 📘 توثيق استراتيجية بوت التحوّل — من قراءة الكود الفعلية

> **إعادة كتابة كاملة** (استبدلت مسودة `0d32315` القديمة بالكامل — لم تُدمَج فوقها).
> مُستخرَج من قراءة فعلية لـ: `analyzer.py` · `market_structure.py` · `htf_zones.py` · `config.py` · `price_monitor.py` · `outcome_tracker.py`
> الحالة عند: `HEAD = ed5930e`، شاملة إصلاحات أول يوليو (`9908193` منطق MTF الاستمراري، `f917d39` خفض عتبة scalp، `e390939` فكّ البوابات) ودمج `ed5930e` (P&L حقيقي للعقد + أمانة البيانات).
> المراجع أدناه بأسماء الدوال (أدوم من أرقام الأسطر) مع الثوابت الحرجة.

---

## 0️⃣ الفلسفة (Philosophy)

البوت **continuation-based** (تتبّع الاستمرار)، وليس mean-reversion على مستوى البنية:

- **البنية السوقية تقود القرار**: أحداث `BOS / CHoCH / MSS` على الـ5m (طبقتا Swing + Internal) هي العمود الفقري للاتجاه.
- **HTF يجيب «وين» / 5m يجيب «متى»**: الفريمات العليا (1h/4h/daily) تحدّد *مناطق* الطلب/العرض (OB/FVG/Inversion)، والـ5m يعطي *توقيت* الدخول عبر تأكيدات `CISD` و`Displacement`.
- **سكور تراكمي مرجّح**: يُبنى سكوران متوازيان `bs` (bull) و`ps` (bear) من ~20 عاملاً، والاتجاه = الأعلى، ويُقبل فقط إذا تجاوز عتبة `effective_min` المتكيّفة.
- **أفضل بيئة تشغيل**: سوق مُتّجه/متقلّب داخل نافذة 10:00ص–2:00م ET، مع توافق منطقة HTF + تأكيد 5m + عدد فريمات MTF + R:R مقبول.

> ⚠️ **تنبيه معماري مهم**: طبقة السكور الرئيسية لمؤشر RSI لا تزال **mean-reversion** (تكافئ `rsi<30` للـCALL)، بينما تأكيد MTF **استمراري**. هذا **تناقض معروف غير محلول** — موثّق في [القسم 9️⃣](#9️⃣-ملاحظات-معمارية-موثّقة-صراحةً) وليس عطلاً يُصلَح الآن.

---

## 1️⃣ الإعدادات الأساسية (`config.py`)

| المعامل | القيمة | المعنى |
|---|---|---|
| `WATCHLIST` | QQQ, SPY, NVDA, AAPL, TSLA, AMZN, META, GOOGL, MSFT, AMD | الأصول المراقبة |
| `CORRELATED_GROUPS` | [QQQ,SPY] · [GOOGL,META] | لا تُرسل أكثر من إشارة لكل مجموعة في نفس المسح |
| `ACCOUNT_SIZE` | $10,000 | حجم الحساب الافتراضي (يُقرأ من DB إن وُجد) |
| `RISK_PCT` | 1% | المخاطرة لكل صفقة (Position Sizing) |
| `SCAN_INTERVAL_MINUTES` | 15 | الفحص الافتراضي (تكيّفي ٥/١٠/١٥ حسب VIX) |
| `MIN_SCORE` | 5.5 | الحد الأدنى الأساسي لإرسال إشارة (يُقرأ per-symbol من DB) |
| `HIGH_CONFIDENCE_THRESHOLD` | 7.5 | حدّ «ثقة عالية» |
| `SIGNAL_COOLDOWN_MINUTES` | 45 | لا إعادة إرسال لنفس السهم خلال هذه المدة |
| ساعات السوق | 9:35 – 15:45 ET | لا يُرسِل خارج هذه الفترة |

---

## 2️⃣ الفريمات الزمنية الستة ودورها

| الفريم | الفترة المجلوبة | الدور الفعلي |
|---|---|---|
| **5m** | 3d | **الأساسي**: pivots, FVG, OB, InvFVG, structure events (Swing+Internal), EQH/EQL, OTE, VWAP، السكور، مستويات الدخول/الوقف/الأهداف |
| **15m** | 5d | تأكيد MTF **فقط** (`_quick_direction`) |
| **1h** | 14d / 30d | HTF zones + تأكيد MTF + Premium/Discount + **مصدر SMT** |
| **4h** | 30d / 60d | HTF zones + تأكيد MTF + Premium/Discount |
| **1d** | 90d | Regime (SMA يومي)، PDH/PDL، PWH/PWL/PMH/PML (weekly/monthly مُشتقّة منه بـ resample)، Premium/Discount اليومي |
| **1m** | 1d | مراقبة لحظية للمس منطقة الدخول (`price_monitor.py`) — **ليست للسكور** |

> **ملاحظة**: الأسبوعي (`W-FRI`) والشهري (`ME`) لا يُجلبان منفصلين — يُشتقّان من اليومي عبر `resample` داخل `prev_period_levels`.

---

## 3️⃣ Bias / Trend — البنية السوقية

### التعريفات الدقيقة (`market_structure.detect_structure_events`)

الكسر يُقاس **على إغلاق الشمعة** مقابل آخر pivot مؤكَّد (`close_i > last_pivot_high` أو `close_i < last_pivot_low`):

| الحدث | التعريف بالكود | الدلالة |
|---|---|---|
| **CHoCH** | إغلاق يكسر آخر pivot **عكس** `trend_bias` الحالي → يرفع علم `awaiting_bos` | تحوّل محتمل |
| **BOS** | أول كسر في الاتجاه الجديد بعد CHoCH (أو أول كسر عندما `trend_bias = None`) | تأكيد التحوّل |
| **MSS** | أي كسر إضافي في **نفس** الاتجاه بعد BOS | ترند راسخ/متسارع |

### الطبقة الحاكمة: **Swing يحكم، Internal مساند** (`detect_structure_dual`, على 5m)

| الطبقة | نافذة pivot | أوزان السكور | عقوبة معاكسة |
|---|---|---|---|
| **Swing** (الحاكمة) | `swing_size = 15` | MSS +2.5 · BOS +2.0 · CHoCH +1.0 | −1.2 |
| **Internal** (المساندة) | `internal_size = 5` | MSS +1.5 · BOS +1.0 · CHoCH +0.5 | −0.8 |

- **Confluence bonus** عند توافق الطبقتين (`alignment == 'aligned'`): **+1.0**.
- **Confluence filter**: إذا طابق حدث Internal حدثَ Swing ضمن `0.1%` → يُلغى Internal لتجنّب العدّ المزدوج.
- **الاتجاه النهائي للصفقة** = مقارنة مجموع `bs` مقابل `ps` (وليس متغيّر bias مفرد): `direction = 'call' if bs >= ps else 'put'`.

### مُدخلات الـBias عالية الوزن (متعددة الفريمات)
1. **اتجاه منطقة HTF + تأكيدها** (الأقوى): توافق demand/supply على 1h/4h/daily يضيف `zone_strength (1/2/3) + confirm_bonus (حتى +4.0)`، أو **−2.0** عند التعارض.
2. **تأكيد MTF الاستمراري** (15m/1h/4h): +0.9/+0.3/−0.5/−3.0 حسب العدد.
3. **Premium/Discount متعدد الفريمات + Regime**: Daily>4H>1H أوزاناً، + regime ±0.5.
4. **SMT** (`^NDX` مقابل `^GSPC` على 1h، تباعد ≥0.3%): ±2.0.

---

## 4️⃣ Levels — نقاط الاهتمام (POIs) المحسوبة فعليًا

### A. Order Blocks / FVG / Inversion FVG (`market_structure`)
| النوع | كيف يُكتشف | خصائص |
|---|---|---|
| **Order Block** | شمعة تُغلق عكس الاتجاه + الشموع الأربع التالية تخترق `2× span` | فلتر تقلب ATR + تتبّع mitigation → `breaker_bull/breaker_bear` |
| **FVG** | `low[i] > high[i-2]` (bull) أو `high[i] < low[i-2]` (bear) | Dynamic threshold + شرط الإغلاق + mitigation → `demand/supply` |
| **Inversion FVG** | فجوة اخترقها السعر فانقلب دورها | مستوى ثانوي |

> على **HTF** (1h/4h/daily) تُستخرج المناطق بـ`track_mitigation=False`؛ الـmitigation فعّال على **5m** فقط.

### B. HTF Zones (`htf_zones._zones_from_df`) — «وين»
- تجمع OB + FVG + InvFVG من كل فريم أعلى، بقوة: **1H = 1.0 · 4H = 2.0 · Daily = 3.0** (‏+0.5 للـInversion).
- `price_in_zone` تُرجع أقوى منطقة يقع فيها السعر؛ `nearest_zone` تلتقط منطقة ضمن 0.6% في الاتجاه الصحيح.

### C. مستويات السيولة HTF (`prev_period_levels`)
| المستوى | المصدر | tolerance | scoring |
|---|---|---|---|
| **PDH / PDL** | High/Low اليوم السابق | 0.5% | ±2.5 |
| **PWH / PWL** | الأسبوع المكتمل (`W-FRI`) | 0.6% | ±3.0 |
| **PMH / PML** | الشهر المكتمل (`ME`) | 0.6% | ±3.5 |

### D. EQH / EQL (`detect_equal_levels`) — ✅ فعّالة
- نافذة pivot = 3، العتبة `|Δ| < 0.1 × ATR(50)` = «متساويان».
- تتبّع state: `swept` (منقلب) أو pending (سيولة كامنة).
- scoring: `near_eql` pending → bs +2.0 / swept → ps +1.5 · `near_eqh` pending → ps +2.0 / swept → bs +1.5.

### E. Strong / Weak Highs & Lows (`strength` block)
- trailing extremes منذ آخر حدث بنيوي. ترند صاعد: Strong Low (لم يُكسر) دعم قوي، Weak High مقاومة ضعيفة (والعكس هبوطاً).
- scoring: `near_strong_low` bs +2.0 · `near_weak_low` +0.5 · وعقوبة counter-trend (CALL أمام Strong High) −1.0.

### F. OTE Setup (`detect_ote_setup`) — ICT
- يتفعّل فقط إذا `swing.current_bias ∈ {bullish,bearish}` **و** `swing.last_event ∈ {BOS, MSS}` **و** `leg_length > 2.0 × ATR(50)`.
- OTE = 61.8–79% · Golden Pocket = 70.5% (±0.5%) · Inverse OTE عند كسر 79%.
- scoring: `in_golden` ±2.5 · `in_ote` ±1.5 · `in_inverse` ±2.0 (معكوس الاتجاه).

### G. Premium / Discount / Equilibrium (`detect_pd_zones_mtf`)
- نسبة موقع السعر من نطاق آخر 50 شمعة: Premium ≥95% · Discount ≤5% · Equilibrium 47.5–52.5%.
- تراكمي: Daily (‎+0.5/−0.75) > 4H (‎+0.3/−0.45) > 1H (‎+0.2/−0.30).

### H. Pivots / VWAP على 5m
- `_pivot_levels(lookback=5)` → `near_sup` / `near_res`؛ `at_sup/at_res` ضمن 0.4%. VWAP position ±1.0.

---

## 5️⃣ نظام الـScoring (كيف يتراكم `bs`/`ps`)

```python
bs = ps = 0.0
# تُضاف كل عوامل الأقسام 3️⃣+4️⃣ + المؤشرات أدناه
direction = 'call' if bs >= ps else 'put'
score     = bs if direction == 'call' else ps
```

المؤشرات التقليدية المُضافة:

| العامل | الوزن | ملاحظة |
|---|---|---|
| RSI < 30 / > 70 | ±3.0 | ⚠️ mean-reversion (انظر 9️⃣) |
| RSI 40–50 صاعد / 50–60 هابط | ±2.0 | — |
| RSI 50 → اتجاه | ±1.0 | للـCALL فقط إذا `rsi<50` |
| RSI Divergence | ±2.5 | + شرط دخول مشدّد لاحقاً |
| Liquidity Sweep | ±1.5 | wick + رجوع للنطاق |
| OB نشط / Breaker / FVG / InvFVG | +2.0 / +1.5 / +1.5 / +1.0 | قرب السعر |
| VWAP / SMA10+30 / SMA يومي / Volume surge / Regime | ±1.0 / ±0.5 / ±0.5 / ±1.0 / ±0.5 | — |

### تأكيدات داخل منطقة HTF متوافقة (5m)
| التأكيد | bonus |
|---|---|
| **CISD** (Change in State of Delivery) | **+4.0** ⭐ |
| **Inversion FVG** داخل المنطقة | +3.5 |
| **Displacement** (body > 1.2×ATR + إغلاق بأعلى/أدنى 30%) | +3.0 |
| **FVG confluence** | +2.5 |
| داخل المنطقة فقط (بلا تأكيد) | +1.0 |
| إشارة معاكسة لاتجاه المنطقة | **−2.0** |

---

## 6️⃣ Entry Model — تسلسل البوابات

الاتجاه والسكور يُحسبان أولاً، ثم تُطبَّق البوابات بالترتيب (أي فشل → `return None`):

1. **كفاية البيانات**: `df5 ≥ 60` و`df1d ≥ 20` شمعة.
2. **فلتر Earnings**: يُرفض كليًا إذا كانت هناك أرباح خلال يومين (`has_earnings_soon`).
3. **عتبة السكور**: الجهة الفائزة ≥ `effective_min`.
4. **تعديل MTF**: يُضاف +0.9/+0.3/−0.5/−3.0.
5. ⚠️ **Scalp Gate (المخفّفة / «unlocked»)**: يُرفَض **فقط** إذا `is_scalp` **و** `mtf_score == 0`.
   - `is_scalp = (atr / price) < 0.004` (`SCALP_ATR_PCT` — خُفِّض من 0.7% إلى 0.4% في `f917d39`).
   - `SCALP_ATR_PCT_OLD = 0.007` يُستخدم في **shadow logging** للمقارنة فقط، لا يؤثّر على القرار.
6. **إعادة فحص** `score ≥ effective_min` بعد تعديل MTF.
7. تعيين `entry_type` (انظر الأولوية أدناه).
8. **RSI Divergence مشدّد**: إذا كان `entry_type` انحرافيًا → يتطلّب `score ≥ effective_min + 1.5`.
9. **Auto-Weight** (`_get_perf_adj`): تعديل بحسب WR التاريخي per `entry_type` ثم إعادة فحص العتبة.
10. **سقف الهدف** `MAX_RR = 4.0` (يمنع R:R وهمي).
11. **R:R Gate** (لا تزال مفعّلة — لم تُفتح): `rr ≥ min_rr = 1.5`.
12. **HTF Stop Override** (يضيّق الوقف لحافة المنطقة) ثم **MIN_STOP_DIST** = `max(atr×0.5, $0.50)`.
13. **إعادة حساب R:R** بعد ضبط الوقف — ويُرفَض ثانيةً إذا نزل تحت 1.5.
14. **سقف تكلفة العقد** (`max_contract_cost` إن ضُبط).

### `effective_min` المتكيّفة
```
effective_min = min_score (5.5 افتراضياً، أو per-symbol من DB)
  + 1.0  إذا VIX > 25
  + 1.5  إذا VIX > 32        (تتراكم: VIX>32 ⇒ +2.5 إجمالاً)
  + 1.5  إذا قبل 10:00 ET
  + 0.5  إذا بعد 14:00 ET
  + 1.0  افتتاح الإثنين (قبل 10:30)
  + 1.0  الجمعة بعد 13:00
```

### أولوية `entry_type` (CALL — والـPUT مرآة)
`RSI Divergence 📐` › `Liquidity Sweep 🌊` › `Breaker Block 🔄` › `Order Block 🏛️` › `FVG ⚡` › `PML 🌙` › `PWL 📅` › `إعادة اختبار` (at_sup/near_pdl) › `اختراق`.

### حساب المستويات (CALL)
```python
base       = near_sup if at_sup else (price - atr * 0.2)
entry_low  = base
entry_high = base + atr * 0.35
stop       = entry_low - atr * 0.5     # ثم HTF override + MIN_STOP_DIST
target1    = near_res أو (entry_high + atr * 0.5)   # مقيّد بـ MAX_RR
target2    = target1 + atr * 0.6
contracts  = max(1, int(account * RISK_PCT / (option_price * 100)))
```
- **اختيار العقد** (`_get_contract`): scalp → 0DTE؛ غير ذلك → أقرب انتهاء بعد 10 أيام (score≥7.5) أو 5 أيام. أولوية سعر Alpaca على yfinance.

---

## 7️⃣ Supporting — فلاتر/تأكيدات داعمة (غير حاسمة للدخول وحدها)

| الفلتر | الأثر |
|---|---|
| **VIX** | يرفع `effective_min` (>25:+1.0، >32:+1.5) |
| **Earnings** | رفض كامل ضمن يومين (`has_earnings_soon`، cache 6h) |
| **فلتر تقلب OB** | تجاوز شمعة مداها ≥ 2×ATR(200) (news spikes / liquidity wicks) |
| **عتبة FVG الديناميكية** | شمعة وسطى `|Δ|% > 2× المتوسط` + شرط الإغلاق (displacement حقيقي) |
| **فلتر الجلسة/اليوم** | تعديلات وقتية على العتبة (ليست ICT Kill Zones — انظر 9️⃣) |
| **Options Flow** | max_pain / call_wall / put_wall / PCR — **سياق يُسجَّل فقط، لا يدخل السكور** |
| **Auto-Weight** | تعديل ± بحسب WR التاريخي per `entry_type` (≥5 صفقات محسومة) |
| **Correlated groups + Cooldown** | منع إشارات متعدّدة من نفس المجموعة / نفس السهم |

---

## 8️⃣ المخرجات وتتبّع الأداء (`price_monitor.py` + `outcome_tracker.py`)

### دورة حياة الإشارة
```
pending ⏳ → دخول 🟢 → active 📈 → T1 ✓ → trailing → T2 ✅ | stop ❌
```
- **Polling** كل `POLL_SECONDS = 45` ثانية على شمعة **1m** + أسعار Alpaca اللحظية.
- **pending**: منطقة عريضة (`width_pct ≥ 0.5`) تنتظر النصف الأعمق؛ الضيقة يكفيها wick. `fill_price` = اللمسة الفعلية. تُلغى بعد `PENDING_MAX_HOURS = 24`.
- **active**: تتبّع MFE/MAE بالـR؛ تنبيهات ربح `PCT_MILESTONES = [25, 50, 100]%` وتآكل `CONTRACT_LOSS_ALERTS = [-40, -60]%`.
- **Trailing** بعد T1: `trail_gap = |target1 − entry| × 0.5`.

### مفردات النتيجة (`status`) والـR-Multiple
| status | متى | R-Multiple |
|---|---|---|
| `hit_t2` | بلوغ الهدف الثاني | `+rr` كامل |
| `hit_t1` | بلوغ T1 ثم وقف/trailing | `+rr × 0.5` |
| `stopped` | الوقف قبل T1 | `−1.0` |
| `expired` | انتهى دون حسم | `0.0` |
| `manual_exit` | خروج يدوي | R محسوبة لحظتها |

### الحقول المُسجَّلة (أمانة البيانات — `ed5930e`)
- `r_multiple` = **R على السهم** (وليس P&L العقد).
- `option_pnl_pct` + `exit_option_price` = **P&L حقيقي للعقد** (يُلتقط لحظة الخروج).
- `max_favorable` / `max_adverse` (MFE/MAE) — البذرة عند الدخول = 0.0 (MAE ≤ 0 دائماً).
- `price_source` = `alpaca_iex | yfinance | unknown` — مصدر بيانات توليد الإشارة (df5).

---

## 9️⃣ ملاحظات معمارية موثّقة صراحةً

هذه سلوكيات **مقصودة/معروفة** في الكود الحالي — ليست أخطاءً تنتظر إصلاحاً فوريًا:

1. ✅ **EQH/EQL فعّالة ومربوطة بالسكور** (`detect_equal_levels` + scoring في `analyze` و`quick_scan`، ومُختبَرة في `tests/test_equal_levels.py`). **ليست deferred.**

2. ✅ **`require_mtf` بارامتر ميّت**: موجود في توقيع `analyze(...)` لكنه **لا يُستخدَم في الجسم إطلاقاً**. البوابة الصلبة القديمة (رفض عند عدم تأكيد MTF) **أُزيلت** في `e390939`؛ MTF الآن مجرّد تعديل سكور + بوابة scalp المخفّفة. (يُمرَّر فقط من `dashboard.py` بلا أثر.)

3. ⚠️ **تناقض RSI معروف وغير محلول** (ليس عطلاً يُصلَح الآن):
   - **السكور الرئيسي** mean-reversion: `rsi<30 → bs+3.0` (CALL على تشبّع بيعي)، `rsi>70 → ps+3.0`. لاحظ أن مساهمة RSI للـCALL **تنعدم** فوق 50 (السلّم يتوقّف عند `rsi<50 صاعد`).
   - **تأكيد MTF** استمراري (`_direction_signals.new` عبر `_quick_direction`): CALL تتطلّب `price>sma20 & rsi صاعد & rsi<70` — أي تؤكّد الزخم الصاعد لا التشبّع.
   - **النتيجة**: ترند صاعد صحّي (RSI ~55–69 صاعد) يأخذ صفراً من كتلة RSI الرئيسية بينما يؤكّده MTF — فلسفتان متعاكستان تتعايشان في النظام. *يستحق نقاشاً منفصلاً إن رغبت بمواءمتهما لاحقاً.*

4. ℹ️ **«SMA200» تسمية مضلّلة**: `_market_regime` و`sma200d` يستخدمان فعليًا `rolling(20)` على اليومي — أي SMA لـ20 يوماً، لا 200.

5. ⛔ **غير مُنفَّذ (Deferred / غير فعّال)** — لا تُوثَّق كأنها شغّالة:
   - **Kill Zones (ICT London/NY)**: غير موجودة. الموجود «فلتر جلسة زمني» يعدّل العتبة فقط.
   - **News Filter (ماكرو FOMC/CPI)**: غير موجود — الموجود فلتر Earnings فقط.
   - **COT (Commitment of Traders)**: غير موجود إطلاقاً.
   - **Mitigation Block كـPOI مستقل**: غير موجود كنوع منفصل — لكن *تتبّع الـmitigation* على OB/FVG (→ breaker/inversion) فعّال على 5m.

---

## 🔟 shadow logging (تشخيص جانبي)

- `_shadow_record` يكتب إلى `shadow_log.jsonl` مقارنة «القديم مقابل الجديد» عند اختلاف قرار البوابة/القبول فقط — **لا يؤثّر على القرار الفعلي**، ويُستخدَم لمعايرة أثر إصلاحات يوليو (منطق MTF + عتبة scalp). (الملف ephemeral على Railway.)

---

> هذا الملف **توثيق للكود لا مصدر حقيقة**؛ عند أي تعارض، الكود هو المرجع. يُحدَّث مع كل تعديل جوهري في `analyzer.py` / `market_structure.py` / `htf_zones.py`.
