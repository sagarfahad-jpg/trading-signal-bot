-- ============================================================================
-- 002 — منطقة دخول بوابة الظل: «الأضيق يفوز، والأوسع يبقى سياقاً» (2026-09-26)
-- يُنفَّذ يدوياً في Supabase SQL editor (المالك) قبل دمج fix/gate-zone-fvg-smt:
-- الكود الجديد يكتب هذه الأعمدة في كل تسجيل، وبدونها يفشل insert (PGRST204) فلا تُسجَّل
-- أي مراقبة. أعمدة جديدة فقط، كلها تقبل NULL؛ الصفوف القديمة تبقى NULL. لا يمس signals.
-- ============================================================================

alter table public.shadow_watches
  add column if not exists zone_source       text,     -- htf_4h | htf_1h | daily_edge | context (احتياطي)
  add column if not exists context_zone_low  numeric,  -- المنطقة النشطة (سياق فقط، لا تدخل أي شرط)
  add column if not exists context_zone_high numeric,
  add column if not exists context_zone_tf   text,     -- 1h | 4h | daily
  add column if not exists context_zone_type text;     -- OB | FVG | INV_FVG

comment on column public.shadow_watches.zone_source is
  'مصدر منطقة الدخول (zone_low/zone_high) لمراقبات armed: htf_4h | htf_1h | daily_edge (النصف البعيد من اليومية) | context (احتياطي: السياق نفسه). NULL لـ no_zone وzone_conflict وللصفوف قبل 002.';
comment on column public.shadow_watches.zone_width_atr is
  'عرض المنطقة التي تراقبها البوابة (zone_low/zone_high) بوحدات ATR(14, 5m). منذ 002 = منطقة الدخول لا المنطقة النشطة.';

-- تحديث ذاكرة مخطط PostgREST فوراً (بدلاً من انتظار إعادة التحميل التلقائية)
notify pgrst, 'reload schema';

-- تحقّق بعد التنفيذ: يجب أن يعيد 5 صفوف
select column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public' and table_name = 'shadow_watches'
  and column_name in ('zone_source', 'context_zone_low', 'context_zone_high',
                      'context_zone_tf', 'context_zone_type')
order by column_name;
