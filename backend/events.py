"""Campus events from Columbia's public events calendar (Bedework JSON feed).

The tick refreshes them every 30 minutes into Tiger (`campus_events`); the website and stall
screens read them from there, so a slow or broken feed never slows a request.
"""

import datetime as dt
import html
import logging
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("nextup.events")

NY = ZoneInfo("America/New_York")
FEED = ("https://events.columbia.edu/feeder/main/eventsFeed.do"
        "?f=y&sort=dtstart.utc:asc&skinName=list-json&count=300&days=7")
REFRESH_EVERY = dt.timedelta(minutes=30)
MAX_LENGTH = dt.timedelta(hours=12)   # skip all-week entries like "Long Term Guest Access"

FREE_FOOD = re.compile(r"free (food|lunch|dinner|breakfast|pizza)|pizza|(lunch|dinner|breakfast|food) (will be )?provided"
                       r"|refreshments|snacks|light bites", re.I)
CAREER = re.compile(r"career|recruit|internship|networking|resume|interview", re.I)
TAG_RE = re.compile(r"<[^>]+>")
IMAGE_BASE = "https://events.columbia.edu/pubcaldav"  # where the event pages load their images from


def _xprop(event: dict, name: str) -> str | None:
    for prop in event.get("xproperties") or []:
        if name in prop:
            value = (prop[name] or {}).get("values", {}).get("text")
            return value.strip() if value else None
    return None


def _image(event: dict, name: str) -> str | None:
    path = _xprop(event, name)
    if not path or not path.startswith("/"):
        return None
    return IMAGE_BASE + quote(path, safe="/()-_.~")


def _blurb(text: str, limit: int = 220) -> str | None:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _utc(stamp: str | None) -> dt.datetime | None:
    if not stamp:
        return None
    try:
        return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def parse(feed: dict, now: dt.datetime) -> list[dict]:
    """Timed, confirmed events in the next 7 days, at most 12 hours long, tagged."""
    events = []
    for e in feed.get("bwEventList", {}).get("events", []):
        start, end = _utc((e.get("start") or {}).get("utcdate")), _utc((e.get("end") or {}).get("utcdate"))
        if not start or (e.get("start") or {}).get("allday") == "true" or e.get("status") == "CANCELLED":
            continue
        if end and end - start > MAX_LENGTH:
            continue
        if (end or start) < now or start > now + dt.timedelta(days=7):
            continue
        title = html.unescape(html.unescape(e.get("summary") or "")).strip()
        description = html.unescape(TAG_RE.sub(" ", html.unescape(e.get("description") or "")))  # the feed double-escapes
        text = title + " " + description
        local = start.astimezone(NY)
        tags = []
        if FREE_FOOD.search(text):
            tags.append("free food")
        if local.date() == now.astimezone(NY).date() and local.hour >= 17:
            tags.append("tonight")
        if CAREER.search(text):
            tags.append("career")
        address = html.unescape((e.get("location") or {}).get("address") or "")
        events.append({
            "guid": f"{e.get('guid')}|{e.get('recurrenceId') or ''}",
            "title": title,
            "starts_at": start,
            "ends_at": end,
            "location": re.split(r"[,\t]", address)[0].strip() or None,
            "link": e.get("eventlink") or e.get("link") or None,
            "tags": tags,
            "image_url": _image(e, "X-BEDEWORK-IMAGE"),
            "thumb_url": _image(e, "X-BEDEWORK-THUMB-IMAGE"),
            "blurb": _blurb(description),
        })
    return events


def fetch(client: httpx.Client | None = None) -> dict:
    client = client or httpx.Client(timeout=20, headers={"User-Agent": "NextUp (MakeCU project)"})
    response = client.get(FEED)
    response.raise_for_status()
    return response.json()


def refresh_if_stale(conn, fetch_feed=None) -> int | None:
    """Refresh the stored events if they're older than REFRESH_EVERY. Returns how many were stored."""
    row = conn.execute("select updated_at from app_state where key = 'events_fetched_at'").fetchone()
    now = conn.execute("select now() as now").fetchone()["now"]
    if row and now - row["updated_at"] < REFRESH_EVERY:
        return None
    # Claim this refresh first, so overlapping ticks don't all fetch the feed.
    conn.execute("insert into app_state (key, value, updated_at) values ('events_fetched_at', '{}', now())"
                 " on conflict (key) do update set updated_at = now()")
    conn.commit()
    events = parse((fetch_feed or fetch)(), now)
    with conn.transaction():
        conn.execute("delete from campus_events where coalesce(ends_at, starts_at) < now() - interval '1 day'")
        for e in events:
            conn.execute(
                "insert into campus_events (guid, title, starts_at, ends_at, location, link, tags, image_url, thumb_url,"
                " blurb, fetched_at) values (%(guid)s, %(title)s, %(starts_at)s, %(ends_at)s, %(location)s, %(link)s,"
                " %(tags)s, %(image_url)s, %(thumb_url)s, %(blurb)s, now())"
                " on conflict (guid) do update set title = excluded.title, starts_at = excluded.starts_at,"
                " ends_at = excluded.ends_at, location = excluded.location, link = excluded.link,"
                " tags = excluded.tags, image_url = excluded.image_url, thumb_url = excluded.thumb_url,"
                " blurb = excluded.blurb, fetched_at = now()",
                e,
            )
    log.info("stored %d campus events", len(events))
    return len(events)


def upcoming(conn, limit: int = 8, for_screen: bool = False) -> list[dict]:
    """Events not over yet. For the stall screen: only events with a picture, tonight's and
    free-food ones first."""
    where = " and image_url is not null" if for_screen else ""
    order = ("('tonight' = any(tags)) desc, ('free food' = any(tags)) desc, starts_at" if for_screen
             else "starts_at")
    rows = conn.execute(
        "select guid, title, starts_at, ends_at, location, link, tags, image_url, thumb_url, blurb from campus_events"
        " where coalesce(ends_at, starts_at) > now() and starts_at < now() + interval '7 days'"
        f"{where} order by {order} limit %s",
        [limit],
    ).fetchall()
    for r in rows:
        r["when"] = describe_time(r["starts_at"])
    return rows


def describe_time(start: dt.datetime, now: dt.datetime | None = None) -> str:
    """'Tonight 7:00 PM', 'Today 1:30 PM', 'Tomorrow 9:00 AM' or 'Wed 6:00 PM' (New York time)."""
    local = start.astimezone(NY)
    today = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NY).date()
    clock = local.strftime("%I:%M %p").lstrip("0")
    if local.date() == today:
        return ("Tonight " if local.hour >= 17 else "Today ") + clock
    if local.date() == today + dt.timedelta(days=1):
        return "Tomorrow " + clock
    return local.strftime("%a ") + clock
