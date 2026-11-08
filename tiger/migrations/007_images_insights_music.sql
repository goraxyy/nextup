-- 007_images_insights_music.sql: event images, rush-hour insights, and Apple Music now playing
-- with synced lyrics. See docs/UI_PLAN.md, items 4, 8 and 9.

-- 8. Event images and a short description ------------------------------------------------
alter table campus_events
  add column image_url text,     -- full image on events.columbia.edu
  add column thumb_url text,     -- its thumbnail
  add column blurb text;         -- the description as plain text, shortened

-- 4. Rush hours -------------------------------------------------------------------------
-- Average share of the room's stalls in use for each weekday and hour (New York time), from the
-- continuous aggregate occupancy_hourly, over the last p_weeks weeks. Hours with no activity
-- count as empty. Returns rows dow (0 = Sunday), hour, occupancy (0 to 1).
create function busy_profile(p_room text, p_weeks integer default 4)
returns table (dow integer, hour integer, occupancy double precision)
language sql stable as $$
  with stall_count as (
    select count(*)::double precision as n from stalls where room_id = p_room
  ),
  busy as (
    select extract(dow from o.bucket at time zone 'America/New_York')::integer as dow,
           extract(hour from o.bucket at time zone 'America/New_York')::integer as hour,
           sum(least(o.busy_s, 3600)) as busy_s
      from occupancy_hourly o join stalls s on s.id = o.stall_id
     where s.room_id = p_room and o.bucket > now() - make_interval(weeks => p_weeks)
     group by 1, 2
  ),
  span as (  -- weeks of history actually covered, so a young database isn't averaged down
    select greatest(1.0, least(p_weeks::double precision,
             extract(epoch from now() - min(o.bucket)) / (7 * 86400))) as weeks
      from occupancy_hourly o join stalls s on s.id = o.stall_id
     where s.room_id = p_room and o.bucket > now() - make_interval(weeks => p_weeks)
  )
  select d.dow, h.hour,
         least(1.0, coalesce(b.busy_s, 0) / (span.weeks * stall_count.n * 3600)) as occupancy
    from generate_series(0, 6) as d(dow)
    cross join generate_series(0, 23) as h(hour)
    cross join stall_count cross join span
    left join busy b on b.dow = d.dow and b.hour = h.hour
   order by 1, 2;
$$;

-- Water: an estimate from shower minutes with the water on (the 'in_use' state; 'finishing' is
-- water off). 2.5 gal/min is the EPA's figure for a standard showerhead (WaterSense: 2.0 or less).
alter table settings add column water_gpm numeric not null default 2.5 check (water_gpm > 0 and water_gpm <= 10);

-- 9. Apple Music: what's playing in each room --------------------------------------------
alter table rooms add column audio_token_hash text unique;   -- sha256 hex of the Mac script's token

create table now_playing (
  room_id      text primary key references rooms (id),
  track_key    text not null,             -- artist|title|duration, to find the lyrics
  title        text not null,
  artist       text,
  album        text,
  duration_s   double precision,
  started_at   timestamptz not null,      -- server clock: when position 0 was (or would be) heard
  playing      boolean not null,
  position_s   double precision,          -- where it stopped, while paused
  updated_at   timestamptz not null default now()
);

-- Synced lyrics from LRCLIB, cached per track. lines: [[seconds, "text"], ...]; found = false
-- remembers that LRCLIB has none, so we don't ask again.
create table lyrics (
  track_key   text primary key,
  found       boolean not null,
  lines       jsonb,
  source      text not null default 'lrclib',
  fetched_at  timestamptz not null default now()
);

-- Every change goes to the room's phones and to each stall screen in the room.
create function outbox_now_playing() returns trigger
language plpgsql as $$
declare
  v_payload jsonb;
begin
  v_payload := jsonb_build_object(
    'track_key', new.track_key, 'title', new.title, 'artist', new.artist,
    'duration_s', new.duration_s, 'started_at', new.started_at, 'playing', new.playing,
    'position_s', new.position_s);
  perform emit('room:' || new.room_id, 'nowplaying.changed', v_payload);
  perform emit('device:' || s.id, 'nowplaying.changed', v_payload) from stalls s where s.room_id = new.room_id;
  return null;
end $$;

create trigger now_playing_outbox after insert or update on now_playing
  for each row execute function outbox_now_playing();
