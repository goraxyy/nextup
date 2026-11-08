-- 001_init.sql: rooms, stalls, settings, the queue, stall history and the SQL simulator.
--
-- Rebuilt on 2026-11-08 from the live Tiger database (the original file wasn't in the repo).
-- Tiger already has it applied, so the migration runner skips it there; locally and in tests
-- it recreates the same tables, functions, triggers and policies.

create extension if not exists timescaledb;

-- Rooms and stalls --------------------------------------------------------------

create table rooms (
  id    text primary key,
  name  text not null
);

create table stalls (
  id          text primary key,
  name        text not null,
  state       text not null default 'free' check (state in ('free', 'in_use', 'finishing')),
  person      boolean not null default false,
  water       boolean not null default false,
  since       timestamptz not null default now(),
  timer_end   timestamptz,
  simulated   boolean not null default false,
  updated_at  timestamptz not null default now(),
  room_id     text not null default 'r1' references rooms (id)
);

create table settings (
  id                                 integer primary key default 1 check (id = 1),
  simulator_on                       boolean not null default true,
  typical_minutes                    integer not null default 8 check (typical_minutes between 1 and 60),
  notify_min_session_minutes         integer not null default 10 check (notify_min_session_minutes between 0 and 120),
  notify_stall_cooldown_minutes      integer not null default 5 check (notify_stall_cooldown_minutes between 0 and 60),
  notify_requester_cooldown_minutes  integer not null default 10 check (notify_requester_cooldown_minutes between 0 and 60),
  notify_rechime_s                   integer not null default 60 check (notify_rechime_s between 10 and 600),
  notify_expire_s                    integer not null default 120 check (notify_expire_s between 20 and 1200)
);

-- The line ----------------------------------------------------------------------

create table queue (
  id               bigint generated always as identity primary key,
  visitor_id       text not null,
  joined_at        timestamptz not null default now(),
  status           text not null default 'waiting' check (status in ('waiting', 'called', 'left', 'expired')),
  called_stall_id  text references stalls (id),
  called_at        timestamptz
);
create index queue_active_idx on queue (status, joined_at);
create index queue_visitor_idx on queue (visitor_id);

-- History (hypertables) -----------------------------------------------------------

create table stall_events (
  time             timestamptz not null default now(),
  stall_id         text not null,
  state            text not null,
  prev_state       text,
  prev_duration_s  double precision
);
select create_hypertable('stall_events', by_range('time', interval '7 days'));
create index stall_events_stall_time_idx on stall_events (stall_id, time desc);

create table sensor_readings (
  time         timestamptz not null,
  stall_id     text not null,
  presence     boolean not null,
  distance_cm  real,
  water        boolean not null,
  water_level  real
);
select create_hypertable('sensor_readings', by_range('time', interval '1 day'));
create index sensor_readings_stall_time_idx on sensor_readings (stall_id, time desc);
alter table sensor_readings set (timescaledb.compress, timescaledb.compress_segmentby = 'stall_id',
                                 timescaledb.compress_orderby = 'time desc');
select add_compression_policy('sensor_readings', interval '1 day');
select add_retention_policy('sensor_readings', interval '30 days');

-- Triggers and functions ------------------------------------------------------------

create function stalls_touch() returns trigger
language plpgsql as $function$
begin
  new.updated_at := now();
  if new.state is distinct from old.state and new.since is not distinct from old.since then
    new.since := now();
  end if;
  return new;
end $function$;

create function stalls_log_event() returns trigger
language plpgsql as $function$
begin
  if new.state is distinct from old.state then
    insert into stall_events (time, stall_id, state, prev_state, prev_duration_s)
    values (now(), new.id, new.state, old.state, extract(epoch from now() - old.since));
  end if;
  return null;
end $function$;

-- Every change, for a server that LISTENs on 'nextup'.
create function notify_change() returns trigger
language plpgsql as $function$
declare
  r record;
begin
  if tg_op = 'DELETE' then r := old; else r := new; end if;
  perform pg_notify('nextup', json_build_object('table', tg_table_name, 'op', tg_op, 'row', row_to_json(r))::text);
  return null;
end $function$;

create function expire_queue() returns void
language sql as $function$
  update queue set status = 'expired'
  where (status = 'waiting' and joined_at < now() - interval '30 minutes')
     or (status = 'called'  and called_at < now() - interval '2 minutes');
$function$;

create function call_next_on_free() returns trigger
language plpgsql as $function$
declare
  next_id bigint;
begin
  if new.state = 'free' and old.state is distinct from 'free' then
    perform expire_queue();
    select id into next_id from queue
      where status = 'waiting'
      order by joined_at, id
      limit 1
      for update skip locked;
    if next_id is not null then
      update queue
        set status = 'called', called_stall_id = new.id, called_at = now()
        where id = next_id;
    end if;
  end if;
  return null;
end $function$;

create function simulate_stalls() returns void
language plpgsql as $function$
declare
  s record;
  elapsed interval;
begin
  if not coalesce((select simulator_on from settings where id = 1), false) then
    return;
  end if;
  for s in select * from stalls where simulated loop
    elapsed := now() - s.since;
    if s.state = 'free' and elapsed > interval '40 seconds' and random() < 0.35 then
      update stalls set state = 'in_use', person = true, water = true where id = s.id;
    elsif s.state = 'in_use' and elapsed > interval '3 minutes' and random() < 0.3 then
      update stalls set state = 'finishing', water = false where id = s.id;
    elsif s.state = 'finishing' and elapsed > interval '50 seconds' then
      update stalls set state = 'free', person = false, water = false, timer_end = null where id = s.id;
    end if;
  end loop;
end $function$;

create trigger stalls_touch before update on stalls for each row execute function stalls_touch();
create trigger stalls_log_event after update of state on stalls for each row execute function stalls_log_event();
create trigger stalls_call_next after update of state on stalls for each row execute function call_next_on_free();
create trigger stalls_notify after insert or delete or update on stalls for each row execute function notify_change();
create trigger queue_notify after insert or delete or update on queue for each row execute function notify_change();
create trigger settings_notify after update on settings for each row execute function notify_change();

-- Busy hours and shower lengths (continuous aggregates) -----------------------------------

create materialized view occupancy_hourly
with (timescaledb.continuous, timescaledb.materialized_only = false) as
select time_bucket('1 hour', time) as bucket,
       stall_id,
       count(*) filter (where state = 'in_use' and prev_state = 'free')                as showers_started,
       count(*) filter (where prev_state = 'in_use')                                  as showers_ended,
       avg(prev_duration_s) filter (where prev_state = 'in_use')                      as avg_shower_s,
       sum(prev_duration_s) filter (where prev_state in ('in_use', 'finishing'))      as busy_s
from stall_events
group by bucket, stall_id
with no data;
select add_continuous_aggregate_policy('occupancy_hourly',
  start_offset => interval '30 days', end_offset => interval '1 minute', schedule_interval => interval '5 minutes');

create materialized view shower_duration_daily
with (timescaledb.continuous, timescaledb.materialized_only = false) as
select time_bucket('1 day', time) as bucket,
       count(*)              as showers,
       avg(prev_duration_s)  as avg_s,
       max(prev_duration_s)  as max_s,
       min(prev_duration_s)  as min_s
from stall_events
where prev_state = 'in_use'
group by bucket
with no data;
select add_continuous_aggregate_policy('shower_duration_daily',
  start_offset => interval '60 days', end_offset => interval '1 minute', schedule_interval => interval '15 minutes');

-- Seed: one room, one real stall (s1) and four simulated ones --------------------------------

insert into rooms (id, name) values ('r1', 'Room 1');
insert into settings (id) values (1);
insert into stalls (id, name, simulated) values
  ('s1', 'Stall 1', false),
  ('s2', 'Stall 2', true),
  ('s3', 'Stall 3', true),
  ('s4', 'Stall 4', true),
  ('s5', 'Stall 5', true);
