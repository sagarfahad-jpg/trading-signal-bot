-- ============================================================================
-- analysis/shadow_compare.sql — مقارنة الظل بالحي على نفس الإشارات (قراءة فقط)
-- تُنفَّذ في Supabase SQL editor بعد أسبوعي قياس. عدّل :since لتاريخ نشر المرحلة ٢.
-- تنبيه: صفوف signals بحالات hit_t1/hit_t2/stopped مع exit_reason IS NULL قبل
-- 2026-09-11 أحكام tracker قديم مضخّمة — استبعدها من أي تجميع حي.
-- ============================================================================

-- 0) نسب التصنيف عند التسجيل (كم إشارة حية لا تخضع للبوابة أصلاً)
select state, count(*) as n, round(100.0 * count(*) / sum(count(*)) over (), 1) as pct,
       count(*) filter (where alt_zone_found) as alt_zone_found
from public.shadow_watches
group by state order by n desc;

-- 1) جدول لكل إشارة: الحي مقابل الظل (المادة الخام للمقارنة)
select s.id, s.symbol, s.direction,
       (s.created_at at time zone 'America/New_York') as created_et,
       s.status as live_status, s.exit_reason as live_exit, s.option_pnl_pct as live_pnl,
       s.r_multiple as live_r, s.duration_min as live_min,
       w.state as shadow_state, w.cancel_reason, w.bars_seen, w.confirm_type, w.confirm_close_loc,
       w.stop_basis, w.target1_source, w.risk_atr, w.rr,
       w.status as shadow_status, w.exit_reason as shadow_exit, w.option_pnl_pct as shadow_pnl,
       w.r_planned, w.r_actual, w.duration_min as shadow_min, w.entry_eval_lag_sec, w.gap
from public.signals s
join public.shadow_watches w on w.signal_id = s.id
where s.created_at >= '2026-09-14'   -- :since
order by s.created_at;

-- 2) الفئة المعاكسة (zone_conflict): خسائر حية تجنّبها الظل بمجرد عدم المراقبة
--    (منفصل تماماً عن أثر التأكيد على الفئة الموافقة في 3)
select count(*) as conflict_signals,
       count(*) filter (where s.status in ('hit_t1','hit_t2','stopped') and s.exit_reason is not null) as live_decided,
       count(s.option_pnl_pct) as with_live_pnl,
       round(avg(s.option_pnl_pct)::numeric, 1) as live_avg_pnl,
       round(sum(s.option_pnl_pct)::numeric, 0) as live_sum_pnl_avoided,
       round(100.0 * count(*) filter (where s.option_pnl_pct > 0) / nullif(count(s.option_pnl_pct), 0), 0) as live_wr,
       count(*) filter (where w.alt_zone_found) as alt_zone_found,
       round(avg(s.option_pnl_pct) filter (where w.alt_zone_found)::numeric, 1) as live_avg_pnl_when_alt_found
from public.shadow_watches w
join public.signals s on s.id = w.signal_id
where w.state = 'zone_conflict';

-- 3) الفئة الموافقة: أثر التأكيد (الحي مقابل الظل على نفس الإشارات)
with a as (
  select s.id, s.status as live_status, s.option_pnl_pct as live_pnl, s.duration_min as live_min,
         w.state, w.cancel_reason, w.confirm_type, w.status as shadow_status, w.option_pnl_pct as shadow_pnl
  from public.shadow_watches w
  join public.signals s on s.id = w.signal_id
  where w.state not in ('no_zone', 'zone_conflict')
)
select
  count(*) as aligned_signals,
  count(*) filter (where confirm_type is not null) as shadow_entered,
  count(*) filter (where state = 'cancelled') as shadow_cancelled,
  count(*) filter (where state = 'cancelled' and live_status = 'stopped') as live_stopped_shadow_skipped,
  count(*) filter (where state = 'cancelled' and live_status = 'stopped' and live_min <= 30) as live_fast_stops_skipped,
  count(*) filter (where state = 'cancelled' and live_status in ('hit_t1','hit_t2')) as live_won_shadow_missed,
  round(sum(live_pnl) filter (where state = 'cancelled')::numeric, 0) as live_pnl_sum_when_shadow_cancelled,
  round(avg(live_pnl) filter (where confirm_type is not null)::numeric, 1) as live_avg_pnl_when_shadow_entered,
  round(avg(shadow_pnl)::numeric, 1) as shadow_avg_pnl,
  round(sum(shadow_pnl)::numeric, 0) as shadow_sum_pnl,
  round(100.0 * count(*) filter (where shadow_pnl > 0) / nullif(count(shadow_pnl), 0), 0) as shadow_wr
from a;

-- 4) ربح الظل حسب نوع التأكيد
select confirm_type, count(*) as n, count(option_pnl_pct) as with_pnl,
       round(avg(option_pnl_pct)::numeric, 1) as avg_pnl, round(sum(option_pnl_pct)::numeric, 0) as sum_pnl,
       round(100.0 * count(*) filter (where option_pnl_pct > 0) / nullif(count(option_pnl_pct), 0), 0) as wr,
       round(avg(r_actual)::numeric, 2) as avg_r_actual, round(avg(duration_min)::numeric, 0) as avg_min,
       round(avg(bars_seen)::numeric, 1) as avg_bars_to_confirm
from public.shadow_watches
where state = 'closed'
group by confirm_type order by n desc;

-- 5) ربح الظل حسب مصدر الهدف وأساس الستوب (يكشف fallback_2r والمنطقة العريضة)
select target1_source, stop_basis, count(*) as n,
       round(avg(option_pnl_pct)::numeric, 1) as avg_pnl,
       round(100.0 * count(*) filter (where status in ('hit_t1','hit_t2')) / count(*), 0) as stock_wr,
       round(avg(risk_atr)::numeric, 2) as avg_risk_atr
from public.shadow_watches
where state = 'closed'
group by 1, 2 order by n desc;

-- 6) الإلغاءات: كم timeout تبعه رجوع للمنطقة (مادة قرار الزيارة الثانية)
select cancel_reason, count(*) as n,
       count(*) filter (where returned_at is not null) as returned_after,
       round(avg(bars_seen)::numeric, 1) as avg_bars
from public.shadow_watches
where state = 'cancelled'
group by cancel_reason order by n desc;

-- 7) جودة البيانات: تأخر التقييم، الفجوات، غياب سعر العقد
select count(*) as entered,
       round(avg(entry_eval_lag_sec)::numeric, 0) as avg_lag_sec,
       max(entry_eval_lag_sec) as max_lag_sec,
       count(*) filter (where gap) as gap_closes,
       count(*) filter (where entry_option_price is null) as no_entry_opt,
       count(*) filter (where state = 'closed' and option_pnl_pct is null) as closed_no_pnl
from public.shadow_watches
where entered_at is not null;

-- 8) سجل شمعة بشمعة لمراقبة محدّدة (تدقيق يدوي مع الشارت)
-- select bar_ts at time zone 'America/New_York' as bar_et, kind, state_before, state_after, checks
-- from public.shadow_events where watch_id = :watch_id order by bar_ts, id;
