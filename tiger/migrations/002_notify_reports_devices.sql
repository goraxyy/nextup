-- 002_notify_reports_devices.sql: Notify, problem reports and device health.
--
-- Rebuilt on 2026-11-08 from the live Tiger database (the original file wasn't in the repo).
-- Tiger already has it applied, so the migration runner skips it there.

-- Notify ------------------------------------------------------------------------

create table notifies (
  id            bigint generated always as identity primary key,
  requester_id  text not null,
  stall_id      text not null references stalls (id),
  status        text not null default 'sent'
                check (status in ('sent', 'delivered', 'acknowledged', 'expired', 'cancelled')),
  created_at    timestamptz not null default now(),
  delivered_at  timestamptz,
  rechimed_at   timestamptz,
  acked_at      timestamptz,
  closed_at     timestamptz
);
create index notifies_stall_idx on notifies (stall_id, created_at desc);
create index notifies_requester_idx on notifies (requester_id, created_at desc);
create index notifies_active_idx on notifies (status) where status in ('sent', 'delivered');

create table notify_events (
  time           timestamptz not null default now(),
  notify_id      bigint not null,
  stall_id       text not null,
  event          text not null,
  since_sent_ms  double precision
);
select create_hypertable('notify_events', by_range('time', interval '7 days'));

-- The stall whose occupant gets the pop-up: the longest session at least
-- notify_min_session_minutes long, with no open pop-up and not in its cooldown.
create function pick_notify_stall() returns text
language sql stable as $function$
  select s.id
  from stalls s, settings st
  where st.id = 1
    and s.state in ('in_use', 'finishing')
    and now() - s.since >= make_interval(mins => st.notify_min_session_minutes)
    and not exists (
      select 1 from notifies n
      where n.stall_id = s.id
        and (n.status in ('sent', 'delivered')
             or n.created_at > now() - make_interval(mins => st.notify_stall_cooldown_minutes))
    )
  order by s.since asc
  limit 1;
$function$;

-- Cancel pop-ups for stalls that freed up, re-chime once, then expire.
create function tick_notifies() returns void
language plpgsql as $function$
declare
  st settings%rowtype;
begin
  select * into st from settings where id = 1;

  update notifies n set status = 'cancelled', closed_at = now()
  from stalls s
  where n.stall_id = s.id and s.state = 'free' and n.status in ('sent', 'delivered');

  update notifies set rechimed_at = now()
  where status in ('sent', 'delivered') and rechimed_at is null
    and created_at < now() - make_interval(secs => st.notify_rechime_s);

  update notifies set status = 'expired', closed_at = now()
  where status in ('sent', 'delivered')
    and created_at < now() - make_interval(secs => st.notify_expire_s);
end $function$;

create function notifies_log_event() returns trigger
language plpgsql as $function$
begin
  if tg_op = 'INSERT' then
    insert into notify_events (notify_id, stall_id, event, since_sent_ms) values (new.id, new.stall_id, new.status, 0);
  elsif new.status is distinct from old.status or new.rechimed_at is distinct from old.rechimed_at then
    insert into notify_events (notify_id, stall_id, event, since_sent_ms)
    values (new.id, new.stall_id,
            case when new.status is distinct from old.status then new.status else 'rechimed' end,
            extract(epoch from now() - new.created_at) * 1000);
  end if;
  return null;
end $function$;

create trigger notifies_log_event after insert or update on notifies for each row execute function notifies_log_event();
create trigger notifies_notify after insert or update on notifies for each row execute function notify_change();

-- Problem reports ------------------------------------------------------------------

create table reports (
  id           bigint generated always as identity primary key,
  stall_id     text not null references stalls (id),
  type         text not null
               check (type in ('no_hot_water', 'clogged_drain', 'broken_light', 'low_pressure', 'other')),
  note         text check (char_length(note) <= 140),
  reporter_id  text not null,
  status       text not null default 'open' check (status in ('open', 'resolved')),
  created_at   timestamptz not null default now(),
  resolved_at  timestamptz
);
create index reports_open_idx on reports (stall_id) where status = 'open';
create trigger reports_notify after insert or update on reports for each row execute function notify_change();

-- Device health ------------------------------------------------------------------

create table devices (
  stall_id   text primary key references stalls (id),
  last_seen  timestamptz,
  version    text,
  ip         text,
  cpu_temp   real,
  uptime_s   integer,
  radar_ok   boolean
);
create trigger devices_notify after update on devices for each row execute function notify_change();

insert into devices (stall_id) select id from stalls;
