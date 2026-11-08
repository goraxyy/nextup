-- Who can read which Realtime topics. Every topic is private ("Allow public access" is
-- off in Realtime settings). Clients only listen: there is no insert policy for
-- broadcasts, so only the API, using the secret key, can send.

create policy "nextup: signed-in users read lab topics"
  on realtime.messages for select to authenticated
  using (
    realtime.messages.extension in ('broadcast', 'presence')
    and (select realtime.topic()) like 'lab:%'
  );

create policy "nextup: a browser reads its own topic"
  on realtime.messages for select to authenticated
  using (
    realtime.messages.extension = 'broadcast'
    and (select realtime.topic()) = 'browser:' || (select auth.uid())::text
  );

create policy "nextup: a device reads its own topic"
  on realtime.messages for select to authenticated
  using (
    realtime.messages.extension = 'broadcast'
    and (select realtime.topic()) = 'device:' || ((select auth.jwt()) -> 'app_metadata' ->> 'device_id')
  );

create policy "nextup: managers read their labs' admin topics"
  on realtime.messages for select to authenticated
  using (
    realtime.messages.extension = 'broadcast'
    and (select realtime.topic()) like 'admin:%'
    and ((select auth.jwt()) -> 'app_metadata' ->> 'role') = 'admin'
    and ((select auth.jwt()) -> 'app_metadata' -> 'labs') ? split_part((select realtime.topic()), ':', 2)
  );

-- Pis announce themselves with Presence on their lab's topic.
create policy "nextup: devices track presence on lab topics"
  on realtime.messages for insert to authenticated
  with check (
    realtime.messages.extension = 'presence'
    and (select realtime.topic()) like 'lab:%'
    and ((select auth.jwt()) -> 'app_metadata' ->> 'device_id') is not null
  );
