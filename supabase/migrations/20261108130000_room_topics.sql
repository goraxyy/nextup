-- Back to the shower design: room topics are room:<id> (a shower room), not lab:<id>.
-- Managers list their rooms in app_metadata.rooms.

drop policy if exists "nextup: signed-in users read lab topics" on realtime.messages;
drop policy if exists "nextup: managers read their labs' admin topics" on realtime.messages;
drop policy if exists "nextup: devices track presence on lab topics" on realtime.messages;

create policy "nextup: signed-in users read room topics"
  on realtime.messages for select to authenticated
  using (
    realtime.messages.extension in ('broadcast', 'presence')
    and (select realtime.topic()) like 'room:%'
  );

create policy "nextup: RAs read their rooms' admin topics"
  on realtime.messages for select to authenticated
  using (
    realtime.messages.extension = 'broadcast'
    and (select realtime.topic()) like 'admin:%'
    and ((select auth.jwt()) -> 'app_metadata' ->> 'role') = 'admin'
    and ((select auth.jwt()) -> 'app_metadata' -> 'rooms') ? split_part((select realtime.topic()), ':', 2)
  );

create policy "nextup: devices track presence on room topics"
  on realtime.messages for insert to authenticated
  with check (
    realtime.messages.extension = 'presence'
    and (select realtime.topic()) like 'room:%'
    and ((select auth.jwt()) -> 'app_metadata' ->> 'device_id') is not null
  );
