-- 004_outbox.sql: live updates for Supabase Realtime, through an outbox.
--
-- The pg_notify('nextup', ...) triggers from 001/002 stay, so a server that LISTENs keeps
-- working. Vercel Functions can't LISTEN, so these extra triggers also write each change
-- the screens and phones care about to `outbox`, addressed to a Realtime topic:
--   room:<room_id>    every phone and screen in the room: stall.changed, queue.changed, report.changed
--   browser:<id>      one phone (its queue visitor_id / notify requester_id): queue.go, queue.expired, notify.receipt
--   device:<stall_id> one stall's Pi: notify.deliver, notify.rechime, notify.closed, stall.reserved
-- The API sends unsent rows to Realtime after every request and on every tick. A rolled-back
-- change never sends anything, because its outbox rows roll back with it.

create table outbox (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  topic       text not null,
  event       text not null,
  payload     jsonb not null,
  sent_at     timestamptz
);
create index outbox_unsent on outbox (id) where sent_at is null;

create function emit(p_topic text, p_event text, p_payload jsonb) returns void
language sql as $$
  insert into outbox (topic, event, payload) values (p_topic, p_event, p_payload);
$$;

-- The line is shared by the whole floor, so its changes go to every room.
create function emit_rooms(p_event text, p_payload jsonb) returns void
language sql as $$
  insert into outbox (topic, event, payload) select 'room:' || id, p_event, p_payload from rooms;
$$;

-- Stalls ------------------------------------------------------------------------

create function outbox_stalls() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    perform emit('room:' || old.room_id, 'stall.removed', jsonb_build_object('id', old.id));
    return null;
  end if;
  -- Skip updates that only touch updated_at.
  if tg_op = 'UPDATE'
     and new.state is not distinct from old.state and new.since is not distinct from old.since
     and new.person is not distinct from old.person and new.water is not distinct from old.water
     and new.timer_end is not distinct from old.timer_end and new.name is not distinct from old.name then
    return null;
  end if;
  perform emit('room:' || new.room_id, 'stall.changed', jsonb_build_object(
    'id', new.id, 'name', new.name, 'state', new.state, 'since', new.since,
    'person', new.person, 'water', new.water, 'timer_end', new.timer_end, 'simulated', new.simulated));
  return null;
end $$;

create trigger stalls_outbox after insert or update or delete on stalls
  for each row execute function outbox_stalls();

-- The line ----------------------------------------------------------------------

create function outbox_queue() returns trigger
language plpgsql as $$
declare
  v_name text;
begin
  if tg_op = 'DELETE' or tg_op = 'INSERT' or new.status is distinct from old.status then
    perform emit_rooms('queue.changed', jsonb_build_object(
      'waiting', (select count(*) from queue where status = 'waiting')));
  end if;
  if tg_op = 'UPDATE' and new.status = 'called' and old.status is distinct from 'called' then
    select name into v_name from stalls where id = new.called_stall_id;
    perform emit('browser:' || new.visitor_id, 'queue.go', jsonb_build_object(
      'queue_id', new.id, 'stall_id', new.called_stall_id, 'stall_name', v_name,
      'claim_by', new.called_at + interval '2 minutes'));
    perform emit('device:' || new.called_stall_id, 'stall.reserved', jsonb_build_object(
      'queue_id', new.id, 'until', new.called_at + interval '2 minutes'));
  elsif tg_op = 'UPDATE' and new.status = 'expired' and old.status = 'called' then
    perform emit('browser:' || new.visitor_id, 'queue.expired', jsonb_build_object('queue_id', new.id));
  end if;
  return null;
end $$;

create trigger queue_outbox after insert or update or delete on queue
  for each row execute function outbox_queue();

-- Notify ------------------------------------------------------------------------

create function outbox_notifies() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    perform emit('device:' || new.stall_id, 'notify.deliver', jsonb_build_object('notify_id', new.id));
    perform emit('browser:' || new.requester_id, 'notify.receipt', jsonb_build_object(
      'notify_id', new.id, 'status', new.status,
      'stall_name', (select name from stalls where id = new.stall_id)));
    return null;
  end if;
  if new.status is distinct from old.status then
    perform emit('browser:' || new.requester_id, 'notify.receipt', jsonb_build_object(
      'notify_id', new.id, 'status', new.status));
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

create trigger notifies_outbox after insert or update on notifies
  for each row execute function outbox_notifies();

-- Problem reports ---------------------------------------------------------------

create function outbox_reports() returns trigger
language plpgsql as $$
begin
  perform emit('room:' || s.room_id, 'report.changed', jsonb_build_object(
    'id', new.id, 'stall_id', new.stall_id, 'type', new.type, 'status', new.status))
  from stalls s where s.id = new.stall_id;
  return null;
end $$;

create trigger reports_outbox after insert or update on reports
  for each row execute function outbox_reports();
