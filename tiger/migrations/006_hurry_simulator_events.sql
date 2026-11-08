-- 006_hurry_simulator_events.sql: no floor code, Hurry on any stall, a realistic simulator,
-- and campus events for the stall screen and website. See docs/UI_PLAN.md, items 1, 2, 3, 7, 8.

-- 1. No floor code ---------------------------------------------------------------
update rooms set join_code_hash = null;

-- 2. Simulator settings ------------------------------------------------------------
alter table settings
  add column sim_min_minutes integer not null default 10 check (sim_min_minutes between 1 and 120),
  add column sim_max_minutes integer not null default 20 check (sim_max_minutes between 1 and 180),
  add column sim_last_run timestamptz,
  -- Set to force a gap (minutes) regardless of the hour, e.g. 0.5 to make a demo busy right now.
  add column sim_gap_override_minutes numeric check (sim_gap_override_minutes > 0),
  add constraint settings_sim_range check (sim_min_minutes <= sim_max_minutes);

-- A phone may hurry once every 2 minutes (was 10); a stall once every 5 (unchanged).
update settings set notify_requester_cooldown_minutes = 2 where id = 1;

-- Average minutes between showers at one simulated stall, by New York hour: rushes 7-9 am and
-- 9 pm-midnight, busy evenings, quiet days, dead nights.
create function sim_gap_minutes(p_at timestamptz) returns numeric
language sql stable as $$
  select case
    when h >= 7 and h < 9   then 2
    when h >= 9 and h < 17  then 25
    when h >= 17 and h < 21 then 10
    when h >= 21            then 3
    else 60
  end::numeric
  from (select extract(hour from p_at at time zone 'America/New_York')::int as h) hour_of_day;
$$;

-- Simulated stalls (s2 to s5): showers of 10 to 20 minutes, water off in the last minute,
-- arrivals following sim_gap_minutes(). The chance of an arrival depends on the time since the
-- last run, so calling this more often (the API's tick, an old server) doesn't speed it up.
create or replace function simulate_stalls() returns void
language plpgsql as $$
declare
  st settings;
  s record;
  n record;
  dt_s double precision;
  p_arrive double precision;
  minutes double precision;
begin
  select * into st from settings where id = 1 for update;   -- one caller at a time
  dt_s := least(60, greatest(0, extract(epoch from now() - coalesce(st.sim_last_run, now() - interval '10 seconds'))));
  update settings set sim_last_run = now() where id = 1;
  if not st.simulator_on or dt_s = 0 then
    return;
  end if;
  -- Chance of at least one arrival in dt_s seconds (capped: exp() underflows past about -700).
  p_arrive := 1 - exp(-least(50, dt_s / (coalesce(st.sim_gap_override_minutes, sim_gap_minutes(now()))::float8 * 60)));

  for s in select * from stalls where simulated order by id for update loop
    if s.state = 'free' then
      if random() < p_arrive then
        minutes := st.sim_min_minutes + random() * (st.sim_max_minutes - st.sim_min_minutes);
        update stalls set state = 'in_use', person = true, water = true,
                          timer_end = now() + make_interval(secs => minutes * 60)
         where id = s.id;
      end if;
    elsif s.state = 'in_use' then
      if s.timer_end is null then   -- a session started before this migration
        minutes := st.sim_min_minutes + random() * (st.sim_max_minutes - st.sim_min_minutes);
        update stalls set timer_end = s.since + make_interval(secs => minutes * 60) where id = s.id;
      elsif now() >= s.timer_end - interval '1 minute' then
        update stalls set state = 'finishing', water = false where id = s.id;
      end if;
    elsif s.state = 'finishing' and (s.timer_end is null or now() >= s.timer_end) then
      update stalls set state = 'free', person = false, water = false, timer_end = null where id = s.id;
    end if;
  end loop;

  -- Simulated stalls answer a Hurry: it's "on their screen" at once; 5 to 40 s later, 7 in 10
  -- acknowledge it and finish within 2 minutes. The rest let it expire (tick_notifies).
  for n in
    select nf.id, nf.status, nf.created_at, nf.stall_id
      from notifies nf join stalls sim on sim.id = nf.stall_id
     where sim.simulated and nf.status in ('sent', 'delivered')
  loop
    if n.status = 'sent' then
      update notifies set status = 'delivered', delivered_at = now() where id = n.id;
    end if;
    if now() - n.created_at >= make_interval(secs => 5 + (n.id % 36)) and (n.id * 7919) % 10 < 7 then
      update notifies set status = 'acknowledged', acked_at = now(), closed_at = now() where id = n.id;
      update stalls set timer_end = least(coalesce(timer_end, now() + interval '2 minutes'), now() + interval '2 minutes')
       where id = n.stall_id and state = 'in_use';
    end if;
  end loop;
end $$;

-- 3. Hurry a chosen stall ----------------------------------------------------------
-- Rules: the stall is in use (for at least notify_min_session_minutes), has no open Hurry, isn't
-- in its cooldown, and this phone hasn't hurried anyone within its own cooldown.
create function request_hurry(p_requester text, p_stall text) returns jsonb
language plpgsql as $$
declare
  st settings;
  s stalls;
  v_last timestamptz;
  v_id bigint;
begin
  select * into st from settings where id = 1;
  select * into s from stalls where id = p_stall for update;
  if not found then
    return jsonb_build_object('status', 'unknown_stall');
  end if;
  if s.state = 'free' then
    return jsonb_build_object('status', 'free');
  end if;
  if now() - s.since < make_interval(mins => st.notify_min_session_minutes) then
    return jsonb_build_object('status', 'too_soon', 'retry_in_s',
      ceil(extract(epoch from s.since + make_interval(mins => st.notify_min_session_minutes) - now())));
  end if;
  if exists (select 1 from notifies where stall_id = s.id and status in ('sent', 'delivered')) then
    return jsonb_build_object('status', 'open');
  end if;
  select max(created_at) into v_last from notifies where stall_id = s.id;
  if v_last > now() - make_interval(mins => st.notify_stall_cooldown_minutes) then
    return jsonb_build_object('status', 'stall_cooldown', 'retry_in_s',
      ceil(extract(epoch from v_last + make_interval(mins => st.notify_stall_cooldown_minutes) - now())));
  end if;
  select max(created_at) into v_last from notifies where requester_id = p_requester;
  if v_last > now() - make_interval(mins => st.notify_requester_cooldown_minutes) then
    return jsonb_build_object('status', 'cooldown', 'retry_in_s',
      ceil(extract(epoch from v_last + make_interval(mins => st.notify_requester_cooldown_minutes) - now())));
  end if;

  insert into notifies (requester_id, stall_id) values (p_requester, s.id) returning id into v_id;
  return jsonb_build_object('status', 'sent', 'notify_id', v_id, 'stall_id', s.id, 'stall_name', s.name);
end $$;

-- The Hurry triggers from 004, plus: the stall's screen gets how many people are in line, and
-- the whole room hears that a stall was hurried (no names), so every phone can show it.
create or replace function outbox_notifies() returns trigger
language plpgsql as $$
declare
  v_room text;
begin
  select room_id into v_room from stalls where id = new.stall_id;
  if tg_op = 'INSERT' then
    perform emit('device:' || new.stall_id, 'notify.deliver', jsonb_build_object(
      'notify_id', new.id,
      'waiting', (select count(*) from queue where status = 'waiting')));
    perform emit('browser:' || new.requester_id, 'notify.receipt', jsonb_build_object(
      'notify_id', new.id, 'status', new.status, 'stall_id', new.stall_id,
      'stall_name', (select name from stalls where id = new.stall_id)));
    perform emit('room:' || v_room, 'hurry.changed', jsonb_build_object(
      'stall_id', new.stall_id, 'status', new.status, 'created_at', new.created_at));
    return null;
  end if;
  if new.status is distinct from old.status then
    perform emit('browser:' || new.requester_id, 'notify.receipt', jsonb_build_object(
      'notify_id', new.id, 'status', new.status, 'stall_id', new.stall_id));
    perform emit('room:' || v_room, 'hurry.changed', jsonb_build_object(
      'stall_id', new.stall_id, 'status', new.status, 'created_at', new.created_at));
    if new.status not in ('sent', 'delivered') then
      perform emit('device:' || new.stall_id, 'notify.closed', jsonb_build_object(
        'notify_id', new.id, 'status', new.status));
    end if;
  end if;
  if new.rechimed_at is not null and old.rechimed_at is null then
    perform emit('device:' || new.stall_id, 'notify.rechime', jsonb_build_object('notify_id', new.id));
  end if;
  return null;
end $$;

-- 8. Campus events ------------------------------------------------------------------
-- Filled by the API's tick from Columbia's public events calendar (Bedework JSON feed).
create table campus_events (
  guid        text primary key,
  title       text not null,
  starts_at   timestamptz not null,
  ends_at     timestamptz,
  location    text,
  link        text,
  tags        text[] not null default '{}',
  fetched_at  timestamptz not null default now()
);
create index campus_events_starts_at on campus_events (starts_at);

-- Small key-value store for the API's own bookkeeping (e.g. when events were last fetched).
create table app_state (
  key         text primary key,
  value       jsonb not null,
  updated_at  timestamptz not null default now()
);
