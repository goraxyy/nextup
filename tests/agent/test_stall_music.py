"""Experimental shower music (MUSIC=spotify): lyrics, player status, radar gestures. No hardware."""

import asyncio
import json

import httpx
import pytest

from device.nextup_agent.config import load
from device.nextup_agent.stall_music import (Gesture, LibrespotPlayer, SpotifyWebPlayer, StallMusic, now_playing,
                                               parse_lrc, web_now_playing)

ENV = {"NEXTUP_API": "http://api.test", "NEXTUP_STALL": "s1", "NEXTUP_DEVICE_TOKEN": "tok",
       "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "pk",
       "PRESENCE_SENSOR": "ld2410c", "ULTRASONIC": "off"}

STATUS = {"username": "u", "device_name": "NextUp Stall 1", "stopped": False, "paused": False, "buffering": False,
          "track": {"uri": "spotify:track:1", "name": "Golden Hour", "artist_names": ["A", "B"],
                    "album_name": "Album", "position": 65_000, "duration": 209_000}}


class FakeKiosk:
    def __init__(self):
        self.state = {}

    def publish(self, **changes):
        self.state.update(changes)


def test_lrc_lines_are_parsed_and_sorted():
    text = "[ar:Someone]\n[00:12.50]First line\n[00:05.00][01:00.00]Chorus\n\n[00:20]Plain stamp"
    assert parse_lrc(text) == [[5.0, "Chorus"], [12.5, "First line"], [20.0, "Plain stamp"], [60.0, "Chorus"]]


def test_player_status_becomes_what_the_screen_shows():
    np = now_playing(STATUS)
    assert (np["title"], np["artist"], np["duration_s"], np["position_s"], np["playing"]) == \
        ("Golden Hour", "A, B", 209.0, 65.0, True)
    assert now_playing({**STATUS, "paused": True})["playing"] is False
    assert now_playing({**STATUS, "stopped": True}) is None and now_playing(None) is None
    assert now_playing({**STATUS, "track": None}) is None


def run_gesture(pattern, step=0.1):
    """pattern: list of (seconds, near) segments → actions fired."""
    g, t, actions = Gesture(), 0.0, []
    for seconds, near in pattern:
        end = t + seconds
        while t < end - 1e-9:
            action, _ = g.update(near, t)
            actions += [action] if action else []
            t += step
    return actions


def test_a_short_raise_plays_or_pauses_and_a_long_one_skips():
    assert run_gesture([(1, False), (1.0, True), (2, False)]) == ["playpause"]
    assert run_gesture([(1, False), (3.0, True), (2, False)]) == ["next"]          # no pause after the skip
    assert run_gesture([(1, False), (0.2, True), (2, False)]) == []                # a blip isn't a gesture


def test_radar_flicker_does_not_split_a_hold():
    # The radar loses the hand for half a second now and then (seen on the Pi): still one skip.
    assert run_gesture([(1.0, True), (0.5, False), (1.0, True), (0.5, False), (1.0, True), (2, False)]) == ["next"]


def player(status=STATUS, lyrics="[01:00.00]Hello\n[01:10.00]Again"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/status":
            return httpx.Response(200, json=status) if status else httpx.Response(204)
        if request.url.path.startswith("/player/"):
            return httpx.Response(200)
        if request.url.host == "lrclib.net":
            return httpx.Response(200, json={"syncedLyrics": lyrics})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_the_screen_gets_the_song_lyrics_and_gesture_commands():
    async def go():
        client, calls = player()
        kiosk = FakeKiosk()
        music = StallMusic(LibrespotPlayer("http://127.0.0.1:3678"), kiosk)
        task = asyncio.create_task(music.run(client))
        await asyncio.sleep(0.2)
        local = kiosk.state["local_music"]
        assert local["now_playing"]["title"] == "Golden Hour" and local["now_playing"]["local"] is True
        assert local["lyrics"] == [[60.0, "Hello"], [70.0, "Again"]]
        # A raised hand while the song plays: pause, with feedback for the screen.
        for i in range(10):
            music.on_hand(True, 100 + i * 0.1, hurry_showing=False)
        for i in range(10):
            music.on_hand(False, 101 + i * 0.1, hurry_showing=False)
        await asyncio.sleep(0.1)
        assert ("POST", "/player/playpause") in calls
        assert kiosk.state["music_feedback"]["action"] == "paused"
        task.cancel()
        await client.aclose()

    asyncio.run(go())


def test_gestures_wait_while_a_hurry_shows_and_without_music():
    async def go():
        kiosk = FakeKiosk()
        music = StallMusic(LibrespotPlayer("http://127.0.0.1:3678"), kiosk)
        music.client, calls = player()
        for i in range(40):  # no song loaded: nothing happens
            music.on_hand(True, i * 0.1, hurry_showing=False)
        music.track = {"uri": "x", "playing": True}
        for i in range(40):  # a Hurry is showing: the hand answers the Hurry instead
            music.on_hand(True, 10 + i * 0.1, hurry_showing=True)
        await asyncio.sleep(0.05)
        assert not [c for c in calls if c[1].startswith("/player/")]
        await music.client.aclose()

    asyncio.run(go())


def test_music_is_off_unless_asked_for(tmp_path):
    from device.nextup_agent.agent import Agent

    off = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path)}))
    assert off.stall_music is None and off.kiosk.state["music_hint"] is None   # the screen shows no hint
    on = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path), "MUSIC": "spotify"}))
    assert on.stall_music is not None and on.kiosk.state["music_hint"] == ""
    web = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path), "MUSIC": "spotify-web", "SPOTIFY_CLIENT_ID": "abc"}))
    assert isinstance(web.stall_music.player, SpotifyWebPlayer)
    assert web.stall_music.player.token_file == tmp_path / "spotify-token.json"
    assert load({**ENV, "MUSIC": "off"}).music == ""
    with pytest.raises(SystemExit, match="MUSIC"):
        load({**ENV, "MUSIC": "youtube"})
    with pytest.raises(SystemExit, match="SPOTIFY_CLIENT_ID"):
        load({**ENV, "MUSIC": "spotify-web"})


# MUSIC=spotify-web: the official app plays, the Pi follows the account ------------------

CURRENT = {"is_playing": True, "progress_ms": 42_000, "currently_playing_type": "track",
           "item": {"uri": "spotify:track:9", "name": "Poison", "duration_ms": 260_000,
                    "artists": [{"name": "Bell Biv DeVoe"}], "album": {"name": "Poison"}}}


def test_web_api_playback_becomes_what_the_screen_shows():
    np = web_now_playing(CURRENT)
    assert (np["title"], np["artist"], np["position_s"], np["duration_s"], np["playing"]) ==         ("Poison", "Bell Biv DeVoe", 42.0, 260.0, True)
    assert web_now_playing({**CURRENT, "currently_playing_type": "episode"}) is None
    assert web_now_playing({**CURRENT, "item": None}) is None and web_now_playing(None) is None


def spotify(token_file, current=CURRENT):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.host == "accounts.spotify.com":
            assert b"refresh_token=old" in request.content and b"client_id=abc" in request.content
            return httpx.Response(200, json={"access_token": "A1", "expires_in": 3600, "refresh_token": "new"})
        if request.url.path == "/v1/me/player/currently-playing":
            return httpx.Response(200, json=current) if current else httpx.Response(204)
        return httpx.Response(204)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_the_pi_follows_the_account_and_keeps_the_rotated_refresh_token(tmp_path):
    token_file = tmp_path / "spotify-token.json"
    token_file.write_text('{"refresh_token": "old"}')
    player = SpotifyWebPlayer("abc", token_file)

    async def go():
        client, calls = spotify(token_file)
        np = await player.status(client)
        assert np["title"] == "Poison" and calls[-1][2] == "Bearer A1"
        assert json.loads(token_file.read_text())["refresh_token"] == "new"   # Spotify rotated it
        await player.command(client, "playpause", playing=True)
        await player.command(client, "playpause", playing=False)
        await player.command(client, "next", playing=True)
        assert [(m, p) for m, p, _ in calls[-3:]] == [("PUT", "/v1/me/player/pause"), ("PUT", "/v1/me/player/play"),
                                                      ("POST", "/v1/me/player/next")]
        assert sum(1 for c in calls if c[1] == "/api/token") == 1          # the access token is reused
        nothing, _ = spotify(token_file, current=None)
        assert await player.status(nothing) is None
        await client.aclose()
        await nothing.aclose()

    asyncio.run(go())


def test_without_a_token_file_it_says_how_to_sign_in(tmp_path):
    from device.nextup_agent.stall_music import MusicError

    async def go():
        client, _ = spotify(tmp_path / "missing.json")
        with pytest.raises(MusicError, match="spotify_login.py"):
            await SpotifyWebPlayer("abc", tmp_path / "missing.json").status(client)
        await client.aclose()

    asyncio.run(go())


# The "shower faster" nudge every few songs (MUSIC_NUDGE_EVERY) ---------------------------


from device.nextup_agent.stall_music import SongCounter  # noqa: E402


def test_every_new_song_counts_skipped_or_not_and_a_repeat_counts_once():
    c = SongCounter()
    assert [c.song(f"spotify:track:{i}") for i in range(1, 6)] == [1, 2, 3, 4, 5]
    assert c.song("spotify:track:5") is None and c.count == 5           # the same song again (repeat)
    c.reset()
    assert c.count == 0 and c.song(None) is None


def test_every_fifth_song_brings_a_nudge_and_zero_turns_it_off():
    kiosk = FakeKiosk()
    music = StallMusic(LibrespotPlayer("http://x"), kiosk, nudge_every=5)
    for i in range(1, 11):
        music.publish(song(i))
        if i in (5, 10):
            assert kiosk.state["music_nudge"]["songs"] == i
            kiosk.state["music_nudge"] = None
        else:
            assert kiosk.state.get("music_nudge") is None
    off, off_kiosk = None, FakeKiosk()
    off = StallMusic(LibrespotPlayer("http://x"), off_kiosk, nudge_every=0)
    for i in range(1, 11):
        off.publish(song(i))
    assert off_kiosk.state.get("music_nudge") is None


def song(i):
    return {"uri": f"t{i}", "title": "T", "artist": "A", "album": None, "duration_s": 200, "position_s": 0, "playing": True}


def test_songs_only_count_while_someone_is_showering():
    kiosk, showering = FakeKiosk(), {"on": False}
    music = StallMusic(LibrespotPlayer("http://x"), kiosk, in_use=lambda: showering["on"], nudge_every=2)
    music.publish(song(1))                                    # stall free: doesn't count
    showering["on"] = True
    music.new_shower()                                        # a shower starts: from zero
    music.publish(song(1))
    assert kiosk.state.get("music_nudge") is None
    music.publish(song(2))
    nudge = kiosk.state["music_nudge"]
    assert (nudge["songs"], nudge["level"]) == (2, 1) and nudge["until"] > 0
    music.publish(song(3))
    music.publish(song(4))
    assert (kiosk.state["music_nudge"]["songs"], kiosk.state["music_nudge"]["level"]) == (4, 2)  # firmer
    music.new_shower()                                        # the shower ends: the pop-up goes
    assert kiosk.state["music_nudge"] is None and music.counter.count == 0


def test_the_nudge_setting(tmp_path):
    assert load(ENV).music_nudge_every == 5
    assert load({**ENV, "MUSIC_NUDGE_EVERY": "0"}).music_nudge_every == 0


def test_the_shampoo_moment_comes_once_when_the_third_song_starts():
    kiosk = FakeKiosk()
    music = StallMusic(LibrespotPlayer("http://x"), kiosk, shampoo_after=2)
    music.publish(song(1))
    music.publish(song(2))
    assert kiosk.state.get("shampoo") is None
    music.publish(song(3))
    assert kiosk.state["shampoo"]["until"] > 0
    kiosk.state["shampoo"] = None
    for i in range(4, 9):
        music.publish(song(i))
    assert kiosk.state["shampoo"] is None                              # once per shower
    music.new_shower()
    assert music.counter.count == 0


def test_the_lion_celebrates_a_real_shower_on_the_way_out(tmp_path):
    import datetime as dt

    from device.nextup_agent.agent import Agent

    agent = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path)}))
    started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=6)).isoformat()
    agent.kiosk.publish(stall_state="in_use", since=started)
    agent.celebrate()
    assert 355 <= agent.kiosk.state["celebrate"]["secs"] <= 365
    blip = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path)}))     # 20 s in the stall: a sensor blip
    blip.kiosk.publish(stall_state="in_use", since=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)).isoformat())
    blip.celebrate()
    assert blip.kiosk.state["celebrate"] is None
    off = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path), "CELEBRATE_SECONDS": "0"}))
    off.kiosk.publish(stall_state="in_use", since=started)
    off.celebrate()
    assert off.kiosk.state["celebrate"] is None
