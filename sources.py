"""Fetchers for each kind of seminar source.

Every fetcher returns a list of Event objects and never raises: problems are
recorded in a SourceStatus so the digest can report them in its footer.
"""
from __future__ import annotations

import html as htmllib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import parse_qs, quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Mozilla/5.0 (compatible; UChicagoSeminarDigest/1.0; personal weekly digest)"


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Event:
    title: str
    start: datetime                     # timezone-aware
    end: Optional[datetime]
    location: str
    url: str
    description: str                    # plain text
    source: str                         # human-readable source name
    kind: str                           # livewhale | pme | table | ics
    key: str                            # de-duplication key
    all_day: bool = False
    groups: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    track_hints: list[str] = field(default_factory=list)
    filter_mode: str = "all"            # all | bio | campus
    online: bool = False
    canceled: bool = False
    host: str = ""                      # faculty host, from schedule tables
    speaker: str = ""                   # pulled from the description when the title lacks it
    series: str = ""                    # CME series name (grand rounds)
    alt_url: str = ""                   # second link (CME page) when merged from two sources
    must_match: list[str] = field(default_factory=list)  # per-group title requirement
    owner: str = ""                     # LiveWhale group that originally posted the event
    lw_id: int = 0                      # LiveWhale event id (lowest = original posting)
    owner_mode: str = ""                # filter of the group that originally posted it
    # filled in by classify.py
    track: str = ""
    verdict: str = ""                   # keep | unclassified | drop
    reason: str = ""
    interest_hits: list[str] = field(default_factory=list)
    is_defense: bool = False


@dataclass
class SourceStatus:
    name: str
    url: str
    count: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


FILTER_RANK = {"campus": 0, "title": 1, "bio": 2, "all": 3}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    text = BeautifulSoup(s, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


# --------------------------------------------------------------------------
# LiveWhale (events.uchicago.edu)
# --------------------------------------------------------------------------
LW_FIELDS = "location,summary,description,group_title,event_types,tags"


def _lw_pages(session, url, pause=0.15):
    items = []
    seen_pages = 0
    while url and seen_pages < 20:
        r = session.get(url, timeout=45)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("data") or [])
        url = (data.get("links") or {}).get("next")
        seen_pages += 1
        time.sleep(pause)
    return items


def _lw_event(item, group_cfg, mode, tz) -> Optional[Event]:
    try:
        start = datetime.fromisoformat(item["date_iso"]).astimezone(tz)
    except Exception:
        return None
    end = None
    if item.get("date2_iso"):
        try:
            end = datetime.fromisoformat(item["date2_iso"]).astimezone(tz)
        except Exception:
            end = None
    url = item.get("url") or ""
    m = re.search(r"/event/(\d+)", url)
    canonical = m.group(1) if m else str(item.get("id"))
    desc = html_to_text(item.get("description")) or html_to_text(item.get("summary"))
    groups = []
    if item.get("group_title"):
        groups.append(clean(item["group_title"]))
    if group_cfg and group_cfg["name"] not in groups:
        groups.append(group_cfg["name"])
    location = clean(item.get("location") or item.get("location_title") or "")
    online = bool(item.get("is_online")) or bool(item.get("online_url"))
    if not location and online:
        location = "Online"
    return Event(
        title=clean(item.get("title")),
        start=start,
        end=end,
        location=location,
        url=url,
        description=desc,
        source="UChicago events calendar",
        kind="livewhale",
        # multi-day all-day events come back once per day; collapse them
        key=f"lw:{canonical}:allday" if item.get("is_all_day") else f"lw:{canonical}:{start:%Y%m%d%H%M}",
        all_day=bool(item.get("is_all_day")),
        groups=groups,
        event_types=list(item.get("event_types") or []),
        track_hints=[group_cfg["track"]] if group_cfg else ["auto"],
        filter_mode=mode,
        online=online,
        canceled=bool(item.get("is_canceled")),
        must_match=[group_cfg["title_must_match"]] if group_cfg and group_cfg.get("title_must_match") else [],
        # cross-posted copies carry the copying group's id; only the original names the owner
        owner=clean(item.get("group_title")) if str(item.get("id")) == canonical else "",
        lw_id=int(canonical) if canonical.isdigit() else 0,
        owner_mode=mode if str(item.get("id")) == canonical else "",
    )


def fetch_livewhale(cfg, start: date, end: date, tz, session) -> tuple[list[Event], list[SourceStatus]]:
    base = cfg.get("base_url", "https://events.uchicago.edu").rstrip("/")
    window = f"start_date/{start:%Y-%m-%d}/end_date/{end:%Y-%m-%d}/response_fields/{LW_FIELDS}/"
    events: list[Event] = []

    st = SourceStatus("UChicago events calendar (department groups)", base)
    known = _lw_group_names(session, base, st)
    for g in cfg.get("groups", []):
        if known and g["name"] not in known:
            st.errors.append(f'Unknown group name "{g["name"]}" (check spelling with --list-groups)')
            continue
        url = f"{base}/live/json/events/group/{quote(g['name'], safe='')}/{window}"
        try:
            items = _lw_pages(session, url)
        except Exception as exc:  # noqa: BLE001
            st.errors.append(f"{g['name']}: {friendly_error(exc)}")
            continue
        for it in items:
            mode = g.get("filter", "all")
            if g.get("keep_title") and re.search(g["keep_title"], clean(it.get("title")), re.I):
                mode = "all"
            ev = _lw_event(it, g, mode, tz)
            if ev:
                events.append(ev)
    st.count = len({e.key for e in events})
    statuses = [st]

    if cfg.get("campus_scan", True):
        st2 = SourceStatus("UChicago events calendar (campus-wide scan)", base)
        ignore = set(cfg.get("campus_scan_ignore_groups") or [])
        try:
            items = _lw_pages(session, f"{base}/live/json/events/{window}")
            n = 0
            for it in items:
                if clean(it.get("group_title")) in ignore:
                    continue
                ev = _lw_event(it, None, "campus", tz)
                if ev:
                    events.append(ev)
                    n += 1
            st2.count = n
            st2.notes.append("All campus events; only clearly bio-related talks are kept.")
        except Exception as exc:  # noqa: BLE001
            st2.errors.append(friendly_error(exc))
        statuses.append(st2)
    return events, statuses


def _lw_group_names(session, base, st) -> set[str]:
    try:
        items = _lw_pages(session, f"{base}/live/json/groups/", pause=0.05)
        return {clean(g.get("title")) for g in items} | {clean(g.get("fullname")) for g in items}
    except Exception as exc:  # noqa: BLE001
        st.notes.append(f"Could not verify group names ({friendly_error(exc)}).")
        return set()


def list_livewhale_groups(base="https://events.uchicago.edu") -> list[str]:
    s = make_session()
    items = _lw_pages(s, f"{base.rstrip('/')}/live/json/groups/", pause=0.05)
    return sorted(clean(g.get("title")) for g in items)


# --------------------------------------------------------------------------
# PME website (Drupal "upcoming events" view)
# --------------------------------------------------------------------------
def _parse_gcal_dates(value: str, tz):
    """Parse '20261117T143000Z/20261117T153000Z' or '20261208/20261210'."""
    a, _, b = value.partition("/")

    def one(v):
        if "T" in v:
            dt = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
            return (dt.replace(tzinfo=ZoneInfo("UTC")) if v.endswith("Z") else dt.replace(tzinfo=tz)).astimezone(tz), False
        return datetime.strptime(v, "%Y%m%d").replace(tzinfo=tz), True

    start, all_day = one(a)
    end = one(b)[0] if b else None
    return start, end, all_day


def fetch_pme(src, start: date, end: date, tz, session) -> tuple[list[Event], SourceStatus]:
    st = SourceStatus(src["name"], src["url"])
    events: dict[str, Event] = {}
    params = {"event_date_range[min]": f"{start:%Y-%m-%d}", "event_date_range[max]": f"{end:%Y-%m-%d}"}
    try:
        for page in range(0, 10):
            r = session.get(src["url"], params={**params, "page": page}, timeout=45)
            r.raise_for_status()
            soup = BeautifulSoup(re.sub(r"<!--.*?-->", "", r.text, flags=re.S), "html.parser")
            scope = soup.select_one(".events-block__results") or soup
            cards = scope.select("article.node--type-event")
            new = 0
            for card in cards:
                ev = _pme_card(card, src, tz)
                if ev and ev.key not in events:
                    events[ev.key] = ev
                    new += 1
            if not cards or not new:
                break
            time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        st.errors.append(friendly_error(exc))
    st.count = len(events)
    return list(events.values()), st


def _pme_card(card, src, tz) -> Optional[Event]:
    link = card.select_one("h2 a, h3 a")
    gcal = card.select_one('a.add-to-calendar[href*="calendar.google.com"]')
    if not link or not gcal:
        return None
    q = parse_qs(urlparse(gcal["href"]).query)
    try:
        start, end, all_day = _parse_gcal_dates(q.get("dates", [""])[0], tz)
    except Exception:
        return None
    # Event-type eyebrow follows a "TYPE OF EVENT" label; location follows "LOCATION".
    types, location = [], clean(q.get("location", [""])[0])
    label = None
    for el in card.select(".card-event__content > *"):
        if el.name == "strong":
            label = el.get_text(strip=True).lower()
        elif "eyebrow" in (el.get("class") or []):
            if label and "type" in label:
                types.append(el.get_text(strip=True))
            elif not location:
                location = el.get_text(" ", strip=True)
    url = urljoin(src["url"], link["href"])
    title = clean(link.get_text(" "))
    return Event(
        title=title,
        start=start,
        end=end,
        location=location,
        url=url,
        description=html_to_text(q.get("details", [""])[0]),
        source=src["name"],
        kind="pme",
        key=f"pme:{urlparse(url).path}:{start:%Y%m%d%H%M}",
        all_day=all_day,
        groups=["Pritzker School of Molecular Engineering"],
        event_types=types,
        track_hints=[src.get("track", "bioengineering")],
        filter_mode=src.get("filter", "bio"),
        online=location.lower().startswith("virtual"),
    )


# --------------------------------------------------------------------------
# Schedule tables (season schedules posted as an HTML table)
# --------------------------------------------------------------------------
def _infer_year(month: int, day: int, today: date) -> date:
    candidates = []
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(y, month, day))
        except ValueError:
            pass
    return min(candidates, key=lambda d: abs((d - today).days))


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_table_date(text: str, today: date) -> Optional[date]:
    t = text.lower().replace(".", " ")
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:\D+(\d{4}))?", t)
    if m:
        month, day = MONTHS[m.group(1)], int(m.group(2))
        if m.group(3):
            return date(int(m.group(3)), month, day)
        return _infer_year(month, day, today)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, month, day)
        return _infer_year(month, day, today)
    return None


def fetch_schedule_table(src, start: date, end: date, tz, session, today: date) -> tuple[list[Event], SourceStatus]:
    st = SourceStatus(src["name"], src["url"])
    events: list[Event] = []
    cols = {k: v.lower() for k, v in (src.get("columns") or {}).items()}
    try:
        r = session.get(src["url"], timeout=45)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table, header = None, []
        for t in soup.select("table"):
            rows = t.select("tr")
            if not rows:
                continue
            hdr = [c.get_text(" ", strip=True).lower() for c in rows[0].select("th,td")]
            if cols.get("date", "date") in hdr:
                table, header = t, hdr
                break
        if table is None:
            st.errors.append(f'No table with a "{src.get("columns", {}).get("date", "Date")}" column found; '
                             "the page may have moved or changed.")
            return events, st
        idx = {field: header.index(name) for field, name in cols.items() if name in header}
        hh, mm = (int(x) for x in src.get("time", "12:00").split(":"))
        total_rows = 0
        for row in table.select("tr")[1:]:
            cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in row.select("th,td")]
            if not any(cells):
                continue
            total_rows += 1

            def get(field_name):
                i = idx.get(field_name)
                return cells[i].strip() if i is not None and i < len(cells) else ""

            d = _parse_table_date(get("date"), today)
            if not d or not (start <= d <= end):
                continue
            speaker = re.sub(r"-\s+", "-", get("speaker"))
            inst = get("institution")
            s_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
            title = f"{src['name']}: {speaker}" + (f" ({inst})" if inst else "")
            host = re.sub(r"-\s+", "-", get("host"))
            events.append(Event(
                title=title,
                start=s_dt,
                end=s_dt + timedelta(minutes=int(src.get("duration_min", 60))),
                location=get("location") or src.get("location", ""),
                url=src["url"],
                description="",
                source=src["name"],
                kind="table",
                key=f"table:{src['name']}:{d:%Y%m%d}:{speaker}",
                groups=[src.get("host_group") or src["name"]],
                event_types=["Lectures-Seminars"],
                track_hints=[src.get("track", "biology")],
                filter_mode="all",
                host=host,
            ))
        st.notes.append(f"{total_rows} season entries on page, {len(events)} in this window.")
        if total_rows == 0:
            st.errors.append("Schedule table is empty; the page may have moved for the new academic year.")
    except Exception as exc:  # noqa: BLE001
        st.errors.append(friendly_error(exc))
    st.count = len(events)
    return events, st


# --------------------------------------------------------------------------
# iCalendar feeds
# --------------------------------------------------------------------------
def fetch_ics(src, start: date, end: date, tz, session) -> tuple[list[Event], SourceStatus]:
    st = SourceStatus(src["name"], src["url"])
    events: list[Event] = []
    try:
        import icalendar
        import recurring_ical_events

        r = session.get(src["url"], timeout=45)
        r.raise_for_status()
        cal = icalendar.Calendar.from_ical(r.content)
        win_start = datetime(start.year, start.month, start.day, tzinfo=tz)
        win_end = datetime(end.year, end.month, end.day, 23, 59, tzinfo=tz)
        for comp in recurring_ical_events.of(cal).between(win_start, win_end):
            dtstart = comp.get("DTSTART").dt
            dtend = comp.get("DTEND").dt if comp.get("DTEND") else None
            all_day = not isinstance(dtstart, datetime)

            def to_dt(v):
                if v is None:
                    return None
                if not isinstance(v, datetime):
                    return datetime(v.year, v.month, v.day, tzinfo=tz)
                return v.replace(tzinfo=tz) if v.tzinfo is None else v.astimezone(tz)

            s_dt = to_dt(dtstart)
            uid = str(comp.get("UID", "")) or str(comp.get("SUMMARY", ""))
            events.append(Event(
                title=clean(str(comp.get("SUMMARY", ""))),
                start=s_dt,
                end=to_dt(dtend),
                location=clean(str(comp.get("LOCATION", ""))),
                url=str(comp.get("URL", "")) or src.get("link", src["url"]),
                description=html_to_text(str(comp.get("DESCRIPTION", ""))),
                source=src["name"],
                kind="ics",
                key=f"ics:{uid}:{s_dt:%Y%m%d%H%M}",
                all_day=all_day,
                groups=[src["name"]],
                event_types=["Lectures-Seminars"],
                track_hints=[src.get("track", "biology")],
                filter_mode=src.get("filter", "all"),
                canceled=str(comp.get("STATUS", "")).upper() == "CANCELLED",
            ))
    except Exception as exc:  # noqa: BLE001
        st.errors.append(friendly_error(exc))
    st.count = len(events)
    return events, st


# --------------------------------------------------------------------------
# UChicago Center for Continuing Medical Education (cme.uchicago.edu, EthosCE)
# Every accredited departmental grand rounds is a "Regularly Scheduled Series"
# there, so this is the one place that has all UCMC grand rounds.
# --------------------------------------------------------------------------
RSS_CODE_RE = re.compile(r"RSS\s*#\s*-?\s*([A-Z0-9][A-Z0-9-]*?)\s*-?\s*CY\s*(\d{2})", re.I)


def clean_series_name(title: str) -> str:
    name = re.sub(r"\s*-*\s*RSS\s*#.*$", "", title, flags=re.I).strip(" -")
    name = re.sub(r"\s*-\s*", " - ", name).replace(" - Grand Rounds", " Grand Rounds").strip()
    # The CME office appends "Grand Rounds" to every lecture-type series. Research
    # seminar series (e.g. Pathology's Pathobiology of Disease seminars) aren't grand rounds.
    if re.search(r"seminar|lecture series", name, re.I):
        name = re.sub(r"\s*-?\s*Grand Rounds\s*$", "", name, flags=re.I).strip(" -")
    return name


def _cme_soup(session, url, params=None):
    r = session.get(url, params=params, timeout=45)
    r.raise_for_status()
    time.sleep(0.15)
    return BeautifulSoup(r.text, "html.parser")


def _cme_catalog(session, base) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for page in range(0, 40):
        soup = _cme_soup(session, f"{base}/series", {"page": page})
        new = 0
        for a in soup.select("#content a[href]"):
            text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            if "RSS" in text and a["href"] not in catalog:
                catalog[urljoin(base, a["href"])] = text
                new += 1
        if not new:
            break
    return catalog


def _cme_sessions_on_page(soup, series_url, tz):
    slug = urlparse(series_url).path.strip("/")
    out = []
    for a in soup.select(f'a[href*="/{slug}/content/"], a[href^="/content/"]'):
        row = a.find_parent(["tr", "li"]) or a.find_parent("div", class_=re.compile("views-row"))
        if not row:
            continue
        d = row.select_one(".date-display-start[content], .date-display-single[content]")
        e = row.select_one(".date-display-end[content]")
        if not d:
            continue
        try:
            start = datetime.fromisoformat(d["content"]).astimezone(tz)
            end = datetime.fromisoformat(e["content"]).astimezone(tz) if e else None
        except ValueError:
            continue
        out.append((clean(a.get_text(" ")), urljoin(series_url, a["href"]), start, end))
    return out


def _cme_series_sessions(session, series_url, start, end, tz):
    first = _cme_soup(session, series_url)
    pages = [int(m.group(1)) for a in first.select('a[href*="page="]')
             if (m := re.search(r"page=(\d+)", a["href"]))]
    last = max(pages, default=0)
    found = {}
    # Sessions are listed oldest first, so read from the last page backwards.
    for page in range(last, -1, -1):
        soup = first if page == 0 else _cme_soup(session, series_url, {"page": page})
        rows = _cme_sessions_on_page(soup, series_url, tz)
        for title, url, s_dt, e_dt in rows:
            if start <= s_dt.date() <= end:
                found[url] = (title, url, s_dt, e_dt)
        if not rows or min(r[2].date() for r in rows) < start:
            break
    return list(found.values())


def _cme_details(session, url) -> tuple[str, str]:
    """Return (speaker, location) from a session page."""
    try:
        soup = _cme_soup(session, url)
    except Exception:  # noqa: BLE001
        return "", ""
    text = re.sub(r"\s+", " ", (soup.select_one("#content") or soup).get_text(" "))
    sp = re.search(r"Speaker(?: Name)?s?\s*:\s*(.+?)(?=\s+(?:Location|Session date|Credit|Objectives?|"
                   r"Target audience|Course summary|Please login|Disclosure)|$)", text)
    loc = re.search(r"Location\s*:\s*(.+?)(?=\s+(?:Session date|Speaker|Credit|Please login)|$)", text)
    speaker = sp.group(1).strip()[:120] if sp else ""
    location = loc.group(1).strip() if loc else ""
    location = re.split(r"\s+(?:United States|See map|Get directions)\b", location)[0].strip(" ,")
    return speaker, location[:120]


def fetch_cme(cfg, start: date, end: date, tz, session, detail_until: date) -> tuple[list[Event], SourceStatus]:
    base = cfg.get("base_url", "https://cme.uchicago.edu").rstrip("/")
    st = SourceStatus("UChicago CME grand rounds (all departments)", f"{base}/series")
    events: list[Event] = []
    try:
        catalog = _cme_catalog(session, base)
        want = re.compile(cfg.get("series_match", "grand rounds"), re.I)
        skip = [re.compile(p, re.I) for p in cfg.get("exclude_series") or []]
        years = {f"{y % 100:02d}" for y in range(start.year, end.year + 1)}
        chosen = {}
        for url, title in catalog.items():
            m = RSS_CODE_RE.search(title)
            code, cy = (m.group(1).upper(), m.group(2)) if m else ("", "")
            if not want.search(title) or (cy and cy not in years):
                continue
            ucmc = bool(re.fullmatch(r"\d{2}-\d{2}-\d{3}", code))
            if not cfg.get("include_affiliates", False) and (not ucmc or re.search(r"endeavor", title, re.I)):
                continue
            if any(p.search(title) for p in skip):
                continue
            chosen[url] = title
        st.notes.append(f"{len(chosen)} series checked")
        if not chosen:
            st.errors.append("No matching series found in the CME catalog; the site may have changed.")
        excl = [re.compile(p, re.I) for p in cfg.get("exclude_title") or []]

        def read_series(item):
            url, title = item
            series = clean_series_name(title)
            try:
                return series, _cme_series_sessions(session, url, start, end, tz), None
            except Exception as exc:  # noqa: BLE001
                return series, [], f"{series}: {friendly_error(exc)}"

        sessions = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for series, found, err in pool.map(read_series, chosen.items()):
                if err:
                    st.errors.append(err)
                sessions += [(series, *f) for f in found if not any(p.search(f[0]) for p in excl)]

        def details(item):
            series, s_title, s_url, s_dt, e_dt = item
            if cfg.get("fetch_details", True) and s_dt.date() <= detail_until:
                return item, _cme_details(session, s_url)
            return item, ("", "")

        with ThreadPoolExecutor(max_workers=4) as pool:
            for (series, s_title, s_url, s_dt, e_dt), (speaker, location) in pool.map(details, sessions):
                generic = not s_title or re.fullmatch(r"(tba|tbd|grand rounds)", s_title, re.I)
                events.append(Event(
                    title=series if generic else s_title,
                    start=s_dt,
                    end=e_dt,
                    location=location,
                    url=s_url,
                    description="",
                    source=st.name,
                    kind="cme",
                    key=f"cme:{urlparse(s_url).path.rsplit('/', 1)[-1]}:{s_dt:%Y%m%d%H%M}",
                    groups=[series],
                    event_types=["Lectures-Seminars"],
                    track_hints=[cfg.get("track", "grand_rounds") if re.search("grand rounds", series, re.I)
                                 else cfg.get("seminar_track", "medicine")],
                    filter_mode="all",
                    speaker=speaker,
                    series=series,
                ))
        if cfg.get("course_match"):
            try:
                courses = _cme_courses(session, base, start, end, tz, cfg["course_match"])
                events += courses
                st.notes.append(f"{len(courses)} one-off courses (symposia)")
            except Exception as exc:  # noqa: BLE001
                st.errors.append(f"courses: {friendly_error(exc)}")
    except Exception as exc:  # noqa: BLE001
        st.errors.append(friendly_error(exc))
    st.count = len(events)
    return events, st


def _cme_courses(session, base, start: date, end: date, tz, pattern: str) -> list[Event]:
    """One-off accredited courses (symposia, conferences) from the CME weekly calendar.
    These have short URLs like /MBCS27 rather than /content/<session>."""
    rx = re.compile(pattern, re.I)
    found: dict[str, Event] = {}
    d = start - timedelta(days=start.weekday())
    while d <= end:
        y, w, _ = d.isocalendar()
        soup = _cme_soup(session, f"{base}/calendar/week/{y}-W{w:02d}")
        for a in soup.select("a[href]"):
            href, title = a["href"], clean(a.get_text(" "))
            path = urlparse(urljoin(base, href)).path
            if "/content/" in path or not rx.search(title) or re.search(r"tumor board|case conference", title, re.I):
                continue
            # courses sit in an "upcoming courses" table: the date is in a sibling cell of the row
            row = a.find_parent("tr") or a.find_parent(["div", "td"])
            ds = row.select_one(".date-display-start[content], .date-display-single[content]") if row else None
            de = row.select_one(".date-display-end[content]") if row else None
            if not ds:
                continue
            s_dt = datetime.fromisoformat(ds["content"]).astimezone(tz)
            e_dt = datetime.fromisoformat(de["content"]).astimezone(tz) if de else None
            if not (start <= s_dt.date() <= end) or path in found:
                continue
            found[path] = Event(
                title=title, start=s_dt, end=e_dt, location="", url=urljoin(base, href), description="",
                source="UChicago CME courses", kind="cme", key=f"cmecourse:{path}:{s_dt:%Y%m%d}",
                groups=["UChicago Center for Continuing Medical Education"], event_types=["Conference-Symposium"],
                track_hints=["cancer" if re.search(r"cancer|oncolog|tumou?r|ucccc", title, re.I) else "medicine"],
                filter_mode="all", series="CME course")
        d += timedelta(days=7)
    return list(found.values())


def friendly_error(exc: Exception) -> str:
    """Turn requests' long exception text into something readable."""
    msg = str(exc)
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return f"site returned HTTP {exc.response.status_code}"
    m = re.search(r"too many (\d{3}) error responses", msg)
    if m:
        return f"site returned HTTP {m.group(1)} repeatedly (usually temporary; will retry next week)"
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in msg.lower():
        return "site did not respond in time"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "could not connect to the site"
    return msg[:200]


# --------------------------------------------------------------------------
# Seminar announcements posted as news items (BSD department sites)
# Some departments (e.g. Ben May) announce seminars only as "Save the date"
# news posts with Guest Speaker / Topic / When / Where lines.
# --------------------------------------------------------------------------
def _field(text, label, stops):
    m = re.search(rf"(?:{label})\s*:\s*(.+?)(?=\s+(?:{stops})\s*:|\s+(?:No Registration|Reception|In-Person|Zoom)|$)",
                  text, re.I)
    return m.group(1).strip(" ,.") if m else ""


def fetch_news_announcements(src, start: date, end: date, tz, session) -> tuple[list[Event], SourceStatus]:
    from dateutil import parser as dateparser

    site = src["site"].rstrip("/")
    st = SourceStatus(src["name"], f"{site}/news")
    events: list[Event] = []
    want = re.compile(src.get("title_match", "seminar|lecture|symposium"), re.I)
    stops = "Guest Speaker|Speaker|Topic|When|Where|Time|Location"
    try:
        r = session.get(f"{site}/api/v1/news/list/all", timeout=45)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
        posts = [it for it in items if want.search(it.get("title") or "")]
        st.notes.append(f"{len(posts)} seminar announcements among {len(items)} recent news posts")
        for it in posts:
            url = it.get("url") or ""
            page = session.get(url, timeout=45)
            if page.status_code != 200:
                continue
            soup = BeautifulSoup(page.text, "html.parser")
            for x in soup(["script", "style", "nav", "header", "footer"]):
                x.decompose()
            text = re.sub(r"\s+", " ", (soup.select_one("main") or soup).get_text(" "))
            when = _field(text, "When", stops)
            if not when:
                continue
            try:
                s_dt = dateparser.parse(when, fuzzy=True).replace(tzinfo=tz)
            except (ValueError, OverflowError):
                st.errors.append(f'Could not read the date "{when[:40]}" in {url}')
                continue
            if not (start <= s_dt.date() <= end):
                continue
            speaker = _field(text, "Guest Speaker|Speaker", stops)
            topic = _field(text, "Topic", stops)
            series = re.sub(r"^save the date:?\s*", "", clean(it.get("title")), flags=re.I)
            series = series.split(" - ")[0].strip() or src["name"]
            title = topic if topic and not re.search(r"lectureship|seminar", topic, re.I) else series
            events.append(Event(
                title=title,
                start=s_dt,
                end=s_dt + timedelta(minutes=int(src.get("duration_min", 60))),
                location=_field(text, "Where", stops),
                url=url,
                description=text[text.find(speaker):][:600] if speaker and speaker in text else "",
                source=src["name"],
                kind="news",
                key=f"news:{urlparse(url).path}:{s_dt:%Y%m%d%H%M}",
                groups=[src.get("host_group") or src["name"]],
                event_types=["Lectures-Seminars"],
                track_hints=[src.get("track", "biology")],
                filter_mode="all",
                speaker=speaker,
                series=series,
            ))
    except Exception as exc:  # noqa: BLE001
        st.errors.append(friendly_error(exc))
    st.count = len(events)
    return events, st


# --------------------------------------------------------------------------
# Hand-entered schedules (extra_seminars.yaml) for series that announce their
# talks only by email. They are merged with any calendar listing of the same talk.
# --------------------------------------------------------------------------
def load_extra_seminars(path, start: date, end: date, tz) -> tuple[list[Event], SourceStatus]:
    import yaml

    st = SourceStatus("Hand-entered schedules (extra_seminars.yaml)", str(path))
    events: list[Event] = []
    try:
        with open(path, encoding="utf-8") as fh:
            series_list = yaml.safe_load(fh) or []
        total = 0
        for ser in series_list:
            for talk in ser.get("talks") or []:
                total += 1
                d = talk.get("date")
                d = d if isinstance(d, date) else date.fromisoformat(str(d))
                if not (start <= d <= end):
                    continue
                hh, mm = (int(x) for x in str(talk.get("time") or ser.get("time", "12:00")).split(":"))
                s_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
                speaker = talk.get("speaker", "")
                inst = talk.get("institution", "")
                head = f"{ser['series']}: {speaker}" + (f" ({inst})" if inst else "")
                events.append(Event(
                    title=talk.get("title") or head,
                    start=s_dt,
                    end=s_dt + timedelta(minutes=int(talk.get("duration_min") or ser.get("duration_min", 60))),
                    location=talk.get("location") or ser.get("location", ""),
                    url=talk.get("url") or ser.get("url", ""),
                    description=talk.get("abstract", ""),
                    source=st.name,
                    kind="table",
                    key=f"extra:{ser['series']}:{d:%Y%m%d}:{speaker}",
                    groups=[ser.get("host_group") or ser["series"]],
                    event_types=["Lectures-Seminars"],
                    track_hints=[ser.get("track", "biology")],
                    filter_mode="all",
                    host=talk.get("host", ""),
                    speaker=speaker if talk.get("title") else "",
                    series=ser["series"],
                ))
        st.notes.append(f"{total} talks entered, {len(events)} in this window")
    except FileNotFoundError:
        st.notes.append("No extra_seminars.yaml file")
    except Exception as exc:  # noqa: BLE001
        st.errors.append(f"Could not read the file: {str(exc)[:150]}")
    st.count = len(events)
    return events, st
