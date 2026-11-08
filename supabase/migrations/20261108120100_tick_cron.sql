-- Every 10 s, call the API's tick: flush the outbox, expire unclaimed turns and
-- unanswered pop-ups, mark silent devices offline.
--
-- The URL and the shared secret live in Supabase Vault, never in git. Set them once:
--   select vault.create_secret('https://<site>/api/tick', 'nextup_tick_url');
--   select vault.create_secret('<CRON_SECRET from Vercel>', 'nextup_cron_secret');
--
-- The job starts inactive. Turn it on once Tiger is connected:
--   select cron.alter_job((select jobid from cron.job where jobname = 'nextup-tick'), active := true);

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

select cron.schedule('nextup-tick', '10 seconds', $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'nextup_tick_url'),
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'X-Cron-Secret', (select decrypted_secret from vault.decrypted_secrets where name = 'nextup_cron_secret')),
    body := '{}'::jsonb,
    timeout_milliseconds := 5000);
$$);

select cron.alter_job((select jobid from cron.job where jobname = 'nextup-tick'), active := false);

-- Supabase Cron never cleans its run history: at 10 s that's about 8,600 rows a day.
select cron.schedule('nextup-cron-cleanup', '0 4 * * *', $$
  delete from cron.job_run_details where end_time < now() - interval '1 day';
$$);
