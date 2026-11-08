-- 003_prefer_real_stalls.sql: real stalls first; only real stalls send "go!".
--
-- The simulator (simulate_stalls) keeps fake stalls busy so the demo looks busy. Two rules change:
--   1. pick_notify_stall() orders by s.simulated, s.since, so a real stall's occupant is
--      pinged before any simulated one (false sorts before true).
--   2. call_next_on_free() also requires "not new.simulated", so only a real stall freeing
--      up calls the next person in the queue. Nobody is sent to a stall that doesn't exist.
--
-- Everything else in both functions is unchanged from 001/002.

create or replace function public.pick_notify_stall()
returns text
language sql
stable
as $function$
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
  order by s.simulated, s.since asc
  limit 1;
$function$;

create or replace function public.call_next_on_free()
returns trigger
language plpgsql
as $function$
declare
  next_id bigint;
begin
  if new.state = 'free' and old.state is distinct from 'free' and not new.simulated then
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
