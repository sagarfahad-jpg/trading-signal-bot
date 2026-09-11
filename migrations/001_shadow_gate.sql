-- ============================================================================
-- المرحلة ٢ — وضع الظل لبوابة الدخول بالتأكيد عند المنطقة (shadow gate)
-- يُنفَّذ يدوياً في Supabase SQL editor (المالك). لا يمس جدول signals إطلاقاً.
-- البوت يكتب بمفتاح service_role (يتجاوز RLS). RLS مفعّل بلا سياسات = anon محجوب.
-- ============================================================================

create table if not exists public.shadow_watches (
  id                  bigserial primary key,
  signal_id           bigint,                 -- signals.id (بلا FK: archive_and_clear_signals يحذف signals)
  symbol              text not null,
  direction           text not null,          -- call | put
  visit_no            integer not null default 1,
  state               text not null,          -- no_zone | zone_conflict | armed | touched | entered | closed | cancelled

  -- المنطقة (مجمّدة لحظة التسجيل — حدود منطقة HTF الفعلية لا الشريط الضحل)
  zone_low            numeric,
  zone_high           numeric,
  zone_tf             text,                   -- 1h | 4h | daily
  zone_type           text,                   -- OB | FVG | INV_FVG
  zone_direction      text,                   -- demand | supply
  zone_width_atr      numeric,

  -- منطقة موافقة بديلة تحتوي السعر (للتحليل فقط عند zone_conflict — لا إنقاذ)
  alt_zone_found      boolean not null default false,
  alt_zone_low        numeric,
  alt_zone_high       numeric,
  alt_zone_tf         text,
  alt_zone_type       text,

  -- سياق الإشارة الحية لحظة المسح
  atr                 numeric,
  score               numeric,
  is_scalp            boolean,
  signal_price        numeric,
  entry_type          text,
  registered_at       timestamptz not null default now(),

  -- الوصول والتأكيد (شموع 5m مغلقة)
  arrival_bar_ts      timestamptz,
  last_bar_ts         timestamptz,            -- آخر شمعة قُيِّمت (idempotent عبر إعادة التشغيل)
  bars_seen           integer not null default 0,
  confirm_type        text,                   -- rejection | cisd | ifvg | fvg
  confirm_bar_ts      timestamptz,
  confirm_close_loc   text,                   -- inside | beyond
  stop_basis          text,                   -- candle | zone | wide_zone_candle
  atr_at_confirm      numeric,

  -- خطة الصفقة (تُحسب لحظة التأكيد: الدخول = إغلاق الشمعة المؤكِّدة)
  entry_price         numeric,
  stop_price          numeric,
  target1             numeric,
  target1_source      text,                   -- pdh | pwh | pmh | swing_5m | swing_1h | strong_high | eqh | htf_* | fallback_2r
  target2             numeric,
  target2_source      text,                   -- ... | extension
  rr                  numeric,
  risk_atr            numeric,

  -- العقد (يُختار لحظة التأكيد كما الحي)
  expiry              text,
  strike              numeric,
  entry_option_price  numeric,
  entry_option_src    text,                   -- quote | chain
  exit_option_price   numeric,
  option_pnl_pct      numeric,
  entry_eval_lag_sec  integer,

  -- النتيجة (مرآة price_monitor)
  entered_at          timestamptz,
  t1_hit_at           timestamptz,
  peak_price          numeric,
  last_tick_at        timestamptz,
  closed_at           timestamptz,
  status              text,                   -- hit_t1 | hit_t2 | stopped | expired
  exit_reason         text,                   -- target2 | stop | stop_after_t1 | trailing_stop | contract_expired
  outcome_price       numeric,
  r_planned           numeric,
  r_actual            numeric,
  max_favorable       numeric,
  max_adverse         numeric,
  highest_price       numeric,
  lowest_price        numeric,
  duration_min        integer,
  gap                 boolean not null default false,   -- حُكم من شموع فائتة (بعد توقف)

  -- الإلغاء + قياس الرجوع للمنطقة بعد timeout (الزيارة الثانية مؤجَّلة)
  cancel_reason       text,                   -- break | timeout | eod
  cancelled_at        timestamptz,
  left_zone_at        timestamptz,
  returned_at         timestamptz,

  notes               jsonb,
  updated_at          timestamptz not null default now()
);

create index if not exists shadow_watches_state_idx      on public.shadow_watches (state);
create index if not exists shadow_watches_signal_idx     on public.shadow_watches (signal_id);
create index if not exists shadow_watches_symbol_idx     on public.shadow_watches (symbol, registered_at desc);
create index if not exists shadow_watches_registered_idx on public.shadow_watches (registered_at desc);

-- سجل الأحداث: صف لكل شمعة مُقيَّمة/حدث (append-only)
create table if not exists public.shadow_events (
  id            bigserial primary key,
  watch_id      bigint not null references public.shadow_watches(id) on delete cascade,
  bar_ts        timestamptz,                  -- null للأحداث غير المرتبطة بشمعة (tick)
  kind          text not null,                -- register | arrival | bar | enter | t1 | exit | cancel | return | gap
  state_before  text,
  state_after   text,
  checks        jsonb,                        -- break, rejection{...}, cisd, ifvg, fvg, displacement, decision...
  created_at    timestamptz not null default now()
);

-- منع الازدواج لو تداخلت حاويتان لحظة النشر: (watch, kind, bar) فريد عندما bar_ts معروف
create unique index if not exists shadow_events_dedupe_idx
  on public.shadow_events (watch_id, kind, bar_ts) where bar_ts is not null;
create index if not exists shadow_events_watch_idx on public.shadow_events (watch_id, created_at);

alter table public.shadow_watches enable row level security;
alter table public.shadow_events  enable row level security;

-- مفتاح إيقاف من الواجهة: '1' = الظل يعمل، '0' = متوقف (يُقرأ كل 5 دقائق)
insert into public.bot_config (key, value, updated_at)
select 'shadow_gate', '1', now()
where not exists (select 1 from public.bot_config where key = 'shadow_gate');
