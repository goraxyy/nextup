-- 005_web_access.sql: what the Vercel API needs on top of the team's schema.
--
-- Additive only: two new columns, one new table and new functions. Nothing from 001-004
-- changes, so a server still running the old code keeps working.

alter table rooms add column join_code_hash text;          -- sha256 hex of the floor code; null = open
alter table devices add column token_hash text unique;     -- sha256 hex of the device's API token

-- Device events carry a UUID made on the Pi, so an offline replay is applied only once.
create table device_event_ids (
  event_id     uuid primary key,
  received_at  timestamptz not null default now()
);

-- The line ----------------------------------------------------------------------

-- Join the line once; joining again returns the same entry.
create function join_queue(p_visitor text) returns jsonb
language plpgsql as $$
declare
  q queue;
begin
  select * into q from queue
   where visitor_id = p_visitor and status in ('waiting', 'called')
   order by joined_at desc limit 1;
  if not found then
    insert into queue (visitor_id) values (p_visitor) returning * into q;
  end if;
  return jsonb_build_object(
    'queue_id', q.id,
    'status', q.status,
    'position', (select count(*) from queue w
                  where w.status = 'waiting' and (w.joined_at, w.id) <= (q.joined_at, q.id)));
end $$;

create function leave_queue(p_visitor text, p_queue_id bigint) returns boolean
language sql as $$
  with left_line as (
    update queue set status = 'left'
     where id = p_queue_id and visitor_id = p_visitor and status in ('waiting', 'called')
    returning 1)
  select exists (select 1 from left_line);
$$;

-- A real stall that is free with nobody called for it (because its called person passed or
-- didn't show up in time) goes to the next person in line. Called on every tick.
create function reassign_free_stalls() returns integer
language plpgsql as $$
declare
  s record;
  next_id bigint;
  v_called integer := 0;
begin
  perform expire_queue();
  for s in
    select id from stalls
     where state = 'free' and not simulated
       and not exists (select 1 from queue where called_stall_id = stalls.id and status = 'called')
     order by since
  loop
    select id into next_id from queue
     where status = 'waiting'
     order by joined_at, id
     limit 1
     for update skip locked;
    exit when next_id is null;
    update queue set status = 'called', called_stall_id = s.id, called_at = now() where id = next_id;
    v_called := v_called + 1;
  end loop;
  return v_called;
end $$;

-- Notify ------------------------------------------------------------------------

create function request_notify(p_requester text) returns jsonb
language plpgsql as $$
declare
  st settings;
  v_stall text;
  s stalls;
  v_id bigint;
begin
  select * into st from settings where id = 1;
  if exists (select 1 from notifies
              where requester_id = p_requester
                and created_at > now() - make_interval(mins => st.notify_requester_cooldown_minutes)) then
    return jsonb_build_object('status', 'cooldown');
  end if;

  v_stall := pick_notify_stall();
  if v_stall is null then
    return jsonb_build_object('status', 'none');
  end if;

  -- Lock the stall, then check again in case another request picked it at the same moment.
  select * into s from stalls where id = v_stall for update;
  if exists (select 1 from notifies where stall_id = v_stall and status in ('sent', 'delivered')) then
    return jsonb_build_object('status', 'none');
  end if;

  insert into notifies (requester_id, stall_id) values (p_requester, v_stall) returning id into v_id;
  return jsonb_build_object('status', 'sent', 'notify_id', v_id, 'stall_id', s.id, 'stall_name', s.name);
end $$;

-- A hand-hold in the stall acknowledges its open pop-up.
create function ack_notify(p_stall text) returns boolean
language sql as $$
  with acked as (
    update notifies set status = 'acknowledged', acked_at = now(), closed_at = now()
     where id = (select id from notifies
                  where stall_id = p_stall and status in ('sent', 'delivered')
                  order by created_at desc limit 1)
    returning 1)
  select exists (select 1 from acked);
$$;

create function notify_delivered(p_stall text) returns boolean
language sql as $$
  with delivered as (
    update notifies set status = 'delivered', delivered_at = now()
     where stall_id = p_stall and status = 'sent'
    returning 1)
  select exists (select 1 from delivered);
$$;

-- Device events -----------------------------------------------------------------

-- Apply one event from a stall's Pi. Returns false for a replay already applied.
create function record_device_event(p_stall text, p_event_id uuid, p_ts timestamptz,
                                    p_type text, p_payload jsonb default '{}')
returns boolean
language plpgsql as $$
declare
  s stalls;
  v_new integer;
begin
  select * into s from stalls where id = p_stall;
  if not found then
    raise exception 'unknown stall %', p_stall;
  end if;

  insert into device_event_ids (event_id) values (p_event_id) on conflict do nothing;
  get diagnostics v_new = row_count;
  if v_new = 0 then
    return false;
  end if;

  if p_type = 'session.start' then
    update stalls set state = 'in_use', person = true where id = p_stall and state <> 'in_use';
  elsif p_type = 'session.end' then
    update stalls set state = 'free', person = false, water = false, timer_end = null
     where id = p_stall and state <> 'free';
  elsif p_type = 'state' then
    update stalls set
      state     = coalesce(p_payload ->> 'state', state),
      person    = coalesce((p_payload ->> 'person')::boolean, person),
      water     = coalesce((p_payload ->> 'water')::boolean, water),
      timer_end = case when p_payload ? 'timer_end' then (p_payload ->> 'timer_end')::timestamptz else timer_end end
     where id = p_stall;
  elsif p_type = 'reading' then
    insert into sensor_readings (time, stall_id, presence, distance_cm, water, water_level)
    values (p_ts, p_stall,
            coalesce((p_payload ->> 'presence')::boolean, false),
            (p_payload ->> 'distance_cm')::real,
            coalesce((p_payload ->> 'water')::boolean, false),
            (p_payload ->> 'water_level')::real);
  elsif p_type = 'heartbeat' then
    insert into devices (stall_id, last_seen, version, ip, cpu_temp, uptime_s, radar_ok)
    values (p_stall, now(), p_payload ->> 'version', p_payload ->> 'ip',
            (p_payload ->> 'cpu_temp')::real, (p_payload ->> 'uptime_s')::integer,
            (p_payload ->> 'radar_ok')::boolean)
    on conflict (stall_id) do update set
      last_seen = now(),
      version   = coalesce(excluded.version, devices.version),
      ip        = coalesce(excluded.ip, devices.ip),
      cpu_temp  = coalesce(excluded.cpu_temp, devices.cpu_temp),
      uptime_s  = coalesce(excluded.uptime_s, devices.uptime_s),
      radar_ok  = coalesce(excluded.radar_ok, devices.radar_ok);
  elsif p_type = 'input.hold' then
    perform ack_notify(p_stall);
  elsif p_type = 'notify.delivered' then
    perform notify_delivered(p_stall);
  elsif p_type = 'safety.alert' then
    -- "Still in there?" went unanswered: only RAs hear about it, never the public room topic.
    perform emit('admin:' || s.room_id, 'safety.alert', jsonb_build_object(
      'stall_id', s.id, 'stall_name', s.name, 'at', p_ts));
  else
    raise exception 'unknown event type %', p_type;
  end if;

  if p_type <> 'heartbeat' then
    update devices set last_seen = now() where stall_id = p_stall;
  end if;
  return true;
end $$;

-- Timers ------------------------------------------------------------------------

-- Called every 10 s by Supabase Cron through POST /api/tick. Runs the team's timers and
-- simulator, gives free real stalls to the next person, and trims old bookkeeping.
create function api_tick() returns jsonb
language plpgsql as $$
declare
  v_called integer;
begin
  perform tick_notifies();
  perform simulate_stalls();
  v_called := reassign_free_stalls();   -- also runs expire_queue()
  delete from device_event_ids where received_at < now() - interval '1 day';
  -- Live updates go stale fast; drop anything unsent for 5 minutes, and sent rows after a day.
  delete from outbox where (sent_at is null and created_at < now() - interval '5 minutes')
                        or sent_at < now() - interval '1 day';
  return jsonb_build_object('called', v_called);
end $$;
