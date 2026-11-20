"""Render the digest as email-safe HTML, plain text, and an .ics calendar file."""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sources import Event, SourceStatus

# Palette (light). Dark-mode equivalents live in the <style> block.
INK = "#262626"
MUTED = "#6E6A64"
RULE = "#E6E1DA"
PAPER = "#FFFFFF"
MAROON = "#800000"          # UChicago maroon
PICK_BG = "#F8F1F1"
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

UTC = ZoneInfo("UTC")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def fmt_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def fmt_day(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"


def fmt_range(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start:%B} {start.day} to {end.day}, {end.year}"
    return f"{start:%B} {start.day} to {end:%B} {end.day}, {end.year}"


def end_of(ev: Event) -> datetime:
    return ev.end if ev.end and ev.end > ev.start else ev.start + timedelta(hours=1)


def gcal_link(ev: Event) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    q = {
        "action": "TEMPLATE",
        "text": ev.title,
        "dates": f"{ev.start.astimezone(UTC):{fmt}}/{end_of(ev).astimezone(UTC):{fmt}}",
        "details": f"{ev.url}\n\n{ev.description[:900]}",
        "location": ev.location,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(q)


def outlook_link(ev: Event) -> str:
    q = {
        "path": "/calendar/action/compose",
        "rru": "addevent",
        "subject": ev.title,
        "startdt": ev.start.isoformat(),
        "enddt": end_of(ev).isoformat(),
        "body": f"{ev.url}\n\n{ev.description[:900]}",
        "location": ev.location,
    }
    return "https://outlook.office.com/calendar/0/deeplink/compose?" + urlencode(q)


def lighten(hex_color: str, amount: float = 0.45) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: round(c + (255 - c) * amount)  # noqa: E731
    return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"


def snippet(text: str, n: int = 260) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.") + "\u2026"


def hosts_line(ev: Event, limit: int = 3) -> str:
    groups = [g for g in ev.groups if g and g != "BSD"] or ev.groups
    shown = groups[:limit]
    more = len(groups) - len(shown)
    s = ", ".join(shown)
    return s + (f" and {more} more" if more > 0 else "")


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def _style_block(tracks: dict, dark: bool = True) -> str:
    """dark=False gives a light-only page (for embedding in a light website)."""
    track_css_light = "\n".join(
        f".t-{k} {{ color: {v['color']}; border-color: {v['color']}; }}" for k, v in tracks.items())
    track_css_dark = "\n".join(
        f"  .t-{k} {{ color: {lighten(v['color'])} !important; border-color: {lighten(v['color'])} !important; }}"
        for k, v in tracks.items())
    dark_css = f"""
@media (prefers-color-scheme: dark) {{
  :root {{
    --paper: #1E1E1F; --ink: #ECE9E4; --muted: #A9A39B; --rule: #3A3836;
    --accent: #E3A1A1; --pick: #2A2324;
  }}
  body, .d-page {{ background: var(--paper) !important; color: var(--ink) !important; }}
  .d-ink, a.d-title {{ color: var(--ink) !important; }}
  .d-muted {{ color: var(--muted) !important; }}
  .d-accent, a {{ color: var(--accent) !important; }}
  .d-rule {{ border-color: var(--rule) !important; }}
  .d-pick {{ background: var(--pick) !important; }}
{track_css_dark}
}}""" if dark else ""
    return f"""
<style>
:root {{
  color-scheme: {"light dark" if dark else "light"};
  --paper: {PAPER}; --ink: {INK}; --muted: {MUTED}; --rule: {RULE};
  --accent: {MAROON}; --pick: {PICK_BG};
}}
body, .d-page {{ background: var(--paper); color: var(--ink); }}
.d-ink {{ color: var(--ink); }}
.d-muted {{ color: var(--muted); }}
.d-accent {{ color: var(--accent); }}
.d-rule {{ border-color: var(--rule); }}
.d-pick {{ background: var(--pick); }}
a {{ color: var(--accent); }}
a.d-title {{ color: var(--ink); }}
a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
{track_css_light}{dark_css}
@media (max-width: 520px) {{
  .d-time {{ width: 64px !important; font-size: 13px !important; }}
  .d-headline {{ font-size: 26px !important; }}
}}
</style>"""


def _track_span(track: str, tracks: dict) -> str:
    t = tracks.get(track, {"label": track.title(), "color": MUTED})
    return (f'<span class="t-{esc(track)}" style="color:{t["color"]};font-weight:600;">'
            f'{esc(t["label"])}</span>')


def _event_block(ev: Event, tracks: dict, show_interest: bool = True) -> str:
    color = tracks.get(ev.track, {}).get("color", MUTED)
    end_line = (f'<div class="d-muted" style="color:{MUTED};font-size:12px;font-weight:400;">'
                f'to {fmt_time(ev.end)}</div>') if ev.end and not ev.all_day and ev.end > ev.start else ""
    time_cell = "All day" if ev.all_day else fmt_time(ev.start)

    flags = []
    if ev.is_defense:
        flags.append("Thesis defense")
    if ev.online and (ev.location or "").lower() not in ("online", "virtual"):
        flags.append("Online option")
    flag_html = "".join(
        f'<span class="d-muted" style="color:{MUTED};font-size:12px;border:1px solid {RULE};'
        f'border-radius:3px;padding:1px 6px;margin-right:6px;" >{esc(f)}</span>' for f in flags)

    interest = ""
    if ev.interest_hits and show_interest:
        interest = (f'<div class="d-accent" style="color:{MAROON};font-size:13px;margin-top:4px;">'
                    f'Matches your interests: {esc(", ".join(ev.interest_hits))}</div>')

    host = (f'<div class="d-muted" style="color:{MUTED};font-size:13px;margin-top:2px;">'
            f'Faculty host: {esc(ev.host)}</div>') if ev.host else ""
    speaker = (f'<div class="d-ink" style="color:{INK};font-size:14px;margin-top:3px;">'
               f'{esc(ev.speaker)}</div>') if ev.speaker else ""
    abstract = (f'<div class="d-ink" style="color:#4A4744;font-size:13.5px;line-height:1.5;margin-top:6px;">'
                f'{esc(snippet(ev.description))}</div>') if len(ev.description) > 40 else ""
    location = (f'<div class="d-muted" style="color:{MUTED};font-size:13px;margin-top:3px;">'
                f'{esc(ev.location)}</div>') if ev.location else ""
    cme_link = (f'&nbsp;&nbsp;&nbsp;<a href="{esc(ev.alt_url)}" style="color:{MAROON};">CME page</a>'
                if ev.alt_url else "")

    return f"""
<tr>
  <td class="d-time d-ink" valign="top" style="width:84px;padding:14px 12px 14px 0;font-family:{SANS};font-size:14px;font-weight:600;color:{INK};white-space:nowrap;">
    {time_cell}{end_line}
  </td>
  <td valign="top" class="t-{esc(ev.track)}" style="padding:12px 0 14px 14px;border-left:3px solid {color};font-family:{SANS};">
    <a class="d-title" href="{esc(ev.url)}" style="font-family:{SERIF};font-size:17px;line-height:1.35;color:{INK};text-decoration:none;">{esc(ev.title)}</a>
    {speaker}
    {location}
    <div class="d-muted" style="color:{MUTED};font-size:13px;margin-top:3px;">{_track_span(ev.track, tracks)}&nbsp;&nbsp;{esc(hosts_line(ev))}</div>
    {host}
    {interest}
    {('<div style="margin-top:6px;">' + flag_html + '</div>') if flag_html else ''}
    {abstract}
    <div style="font-size:12.5px;margin-top:8px;">
      <a href="{esc(ev.url)}" style="color:{MAROON};">Event page</a>&nbsp;&nbsp;&nbsp;
      <a href="{esc(gcal_link(ev))}" style="color:{MAROON};">Add to Google Calendar</a>&nbsp;&nbsp;&nbsp;
      <a href="{esc(outlook_link(ev))}" style="color:{MAROON};">Add to Outlook</a>{cme_link}
    </div>
  </td>
</tr>"""


def _compact_rows(evs: list[Event], tracks: dict, show_day=True) -> str:
    rows = []
    for ev in evs:
        when = f"{ev.start:%a} {ev.start.day}, {fmt_time(ev.start)}" if show_day else fmt_time(ev.start)
        rows.append(f"""
<tr>
  <td class="d-muted" valign="top" style="padding:6px 12px 6px 0;font-family:{SANS};font-size:13px;color:{MUTED};white-space:nowrap;">{esc(when)}</td>
  <td valign="top" style="padding:6px 0;font-family:{SANS};font-size:14px;line-height:1.4;">
    <a class="d-title" href="{esc(ev.url)}" style="color:{INK};text-decoration:underline;text-decoration-color:{RULE};">{esc(ev.title)}</a>{(' <span class="d-ink" style="color:' + INK + ';">with ' + esc(ev.speaker) + '</span>') if ev.speaker else ''}
    <span class="d-muted" style="color:{MUTED};font-size:12.5px;">&nbsp; {_track_span(ev.track, tracks)}{(', ' + esc(ev.location)) if ev.location else ''}</span>
  </td>
</tr>""")
    return "".join(rows)


def _separate_section(label: str, evs: list[Event], tracks: dict, sub: str | None = None) -> str:
    """Compact day-grouped list (grand rounds section, and the embeddable web list)."""
    out = [_section_heading(esc(label), sub if sub is not None else f"{len(evs)} this week, listed by day")]
    by_day = defaultdict(list)
    for e in evs:
        by_day[e.start.date()].append(e)
    for d in sorted(by_day):
        out.append(f"""
<tr><td colspan="2" class="d-ink" style="padding:14px 0 2px 0;font-family:{SERIF};font-size:15px;color:{INK};">{esc(fmt_day(d))}</td></tr>""")
        for ev in sorted(by_day[d], key=lambda e: e.start):
            who = ", ".join(x for x in [ev.speaker] if x)
            where = ev.location
            series = ev.series or hosts_line(ev)
            meta = "; ".join(x for x in [series, where] if x)
            out.append(f"""
<tr>
  <td class="d-muted" valign="top" style="padding:6px 12px 6px 0;font-family:{SANS};font-size:13px;color:{MUTED};white-space:nowrap;width:72px;">{esc('All day' if ev.all_day else fmt_time(ev.start))}</td>
  <td valign="top" style="padding:6px 0 6px 0;font-family:{SANS};font-size:14px;line-height:1.4;">
    <a class="d-title" href="{esc(ev.url)}" style="color:{INK};text-decoration:underline;text-decoration-color:{RULE};">{esc(ev.title)}</a>{(' <span class="d-ink" style="color:' + INK + ';">with ' + esc(who) + '</span>') if who else ''}
    <div class="d-muted" style="color:{MUTED};font-size:12.5px;margin-top:2px;">{esc(meta)}{' ' if meta else ''}&nbsp;<a href="{esc(gcal_link(ev))}" style="color:{MAROON};">Google</a>&nbsp;&nbsp;<a href="{esc(outlook_link(ev))}" style="color:{MAROON};">Outlook</a>{('&nbsp;&nbsp;<a href="' + esc(ev.alt_url) + '" style="color:' + MAROON + ';">CME page</a>') if ev.alt_url else ''}</div>
  </td>
</tr>""")
    return "".join(out)


def _section_heading(text: str, sub: str = "") -> str:
    sub_html = (f'<div class="d-muted" style="font-family:{SANS};font-size:13px;color:{MUTED};margin-top:4px;">'
                f'{sub}</div>') if sub else ""
    return f"""
<tr><td colspan="2" style="padding:30px 0 6px 0;">
  <div class="d-accent" style="font-family:{SERIF};font-size:20px;color:{MAROON};">{text}</div>{sub_html}
</td></tr>
<tr><td colspan="2" class="d-rule" style="border-top:1px solid {RULE};font-size:0;line-height:0;height:1px;">&nbsp;</td></tr>"""


def _summary_sentence(evs: list[Event], tracks: dict, separate=None) -> str:
    extra = ""
    for label, sep in (separate or []):
        if sep:
            extra += f" Plus {len(sep)} {label.lower()}, listed after the seminars."
    if not evs:
        return ("No seminars found for this week." + extra).strip()
    counts = defaultdict(int)
    for e in evs:
        counts[e.track] += 1
    def part(k, n):
        label = tracks.get(k, {}).get("label", k)
        if label.lower().endswith("rounds"):   # "12 grand rounds", not "12 in Grand rounds"
            return f'{n} <span class="t-{esc(k)}" style="color:{tracks[k]["color"]};font-weight:600;">{esc(label.lower())}</span>'
        return f"{n} in {_track_span(k, tracks)}"
    parts = [part(k, n) for k in tracks for n in [counts.get(k, 0)] if n]
    noun = "talk" if len(evs) == 1 else "talks"
    if len(parts) > 1:
        parts_str = ", ".join(parts[:-1]) + " and " + parts[-1]
    else:
        parts_str = parts[0]
    return f"{len(evs)} {noun} this week: {parts_str}.{extra}"


def render_html(*, week_start: date, week_end: date, this_week: list[Event], unclassified: list[Event],
                next_week: list[Event], statuses: list[SourceStatus], tracks: dict, interests: list,
                generated: datetime, separate=(), next_separate_count: int = 0, departments=(),
                web: dict | None = None) -> str:
    """web=None renders the email. web={...} renders the public web page (no personal
    interests or source report, adds an 'updated' note and a calendar-subscribe link)."""
    body = []
    updated = f"{generated:%A}, {generated:%B} {generated.day} at {fmt_time(generated)}"

    # Header
    body.append(f"""
<tr><td colspan="2" style="padding:28px 0 4px 0;font-family:{SANS};font-size:13px;color:{MUTED};" class="d-muted">
  {esc((web or {}).get("title") or "UChicago life science seminars")}
</td></tr>
<tr><td colspan="2" style="padding:0 0 8px 0;">
  <div class="d-accent d-headline" style="font-family:{SERIF};font-size:32px;line-height:1.15;color:{MAROON};">{esc(fmt_range(week_start, week_end))}</div>
</td></tr>
<tr><td colspan="2" class="d-ink" style="padding:0 0 6px 0;font-family:{SANS};font-size:15px;line-height:1.5;color:{INK};">
  {_summary_sentence(this_week, tracks, separate)}
</td></tr>""")
    if web:
        body.append(f"""
<tr><td colspan="2" class="d-muted" style="padding:0 0 4px 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{MUTED};">
  Updated {esc(updated)}. <a href="{esc(web.get("ics_url", "seminars.ics"))}" style="color:{MAROON};">Subscribe in your calendar app</a> to get every listed talk.
</td></tr>""")

    # Picked for you
    all_week = this_week + [e for _, sep in separate for e in sep]
    picks = sorted([e for e in all_week if e.interest_hits], key=lambda e: (-len(e.interest_hits), e.start))
    if interests and (not web or web.get("show_interests")):
        if picks:
            inner = _compact_rows(picks, tracks)
            body.append(f"""
<tr><td colspan="2" style="padding:22px 0 0 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="d-pick" style="background:{PICK_BG};border-radius:4px;">
    <tr><td style="padding:14px 16px 4px 16px;">
      <div class="d-accent" style="font-family:{SERIF};font-size:18px;color:{MAROON};">Picked for you</div>
      <div class="d-muted" style="font-family:{SANS};font-size:12.5px;color:{MUTED};margin-top:2px;">Matching {esc(", ".join(map(str, interests)))}</div>
    </td></tr>
    <tr><td style="padding:4px 16px 12px 16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{inner}</table></td></tr>
  </table>
</td></tr>""")
        else:
            body.append(f"""
<tr><td colspan="2" class="d-muted" style="padding:16px 0 0 0;font-family:{SANS};font-size:13px;color:{MUTED};">
  Nothing this week matches your interests ({esc(", ".join(map(str, interests)))}).
</td></tr>""")

    # Day by day
    by_day = defaultdict(list)
    for e in this_week:
        by_day[e.start.date()].append(e)
    if not this_week:
        msg = ("No more seminars are listed for the rest of this week." if web else
               "The calendars returned no seminars for these dates. If every source in the report below shows 0, "
               "a site may have changed its layout; if only one does, its URL may need updating in config.yaml.")
        body.append(f"""
<tr><td colspan="2" class="d-ink" style="padding:24px 0;font-family:{SANS};font-size:15px;line-height:1.5;color:{INK};">
  {esc(msg)}
</td></tr>""")
    for d in sorted(by_day):
        n = len(by_day[d])
        body.append(_section_heading(esc(fmt_day(d)), f"{n} talk{'s' if n != 1 else ''}"))
        spacer = '<tr><td colspan="2" style="height:10px;font-size:0;line-height:0;">&nbsp;</td></tr>'
        show_int = not web or bool(web.get("show_interests"))
        body.append(spacer.join(_event_block(e, tracks, show_int)
                                for e in sorted(by_day[d], key=lambda e: (e.all_day is False, e.start))))

    # Separate high-volume sections (grand rounds)
    for label, sep in separate:
        if sep:
            body.append(_separate_section(label, sep, tracks))

    # Unclassified
    if unclassified:
        body.append(_section_heading(
            "Also on the calendar, topic not posted",
            "These talks list only a speaker and institution, so they couldn't be checked for bio relevance."))
        body.append(_compact_rows(sorted(unclassified, key=lambda e: e.start), tracks))

    # Next week
    if next_week or next_separate_count:
        sub = f"{len(next_week)} seminars announced so far"
        if next_separate_count:
            sub += f", plus {next_separate_count} grand rounds"
        body.append(_section_heading("Next week", sub))
        body.append(_compact_rows(sorted(next_week, key=lambda e: e.start), tracks))

    if web:
        body.append(f"""
<tr><td colspan="2" style="padding:36px 0 0 0;">
  <div class="d-rule" style="border-top:1px solid {RULE};"></div>
  <p class="d-muted" style="font-family:{SANS};font-size:12px;line-height:1.5;color:{MUTED};margin:14px 0 30px 0;">
    Collected automatically from events.uchicago.edu, cme.uchicago.edu and department websites, and refreshed
    every morning. Times are Chicago time. Hosts sometimes change rooms and times late, so confirm on the event page.
  </p>
</td></tr>""")
        return _page(f"Seminars, {fmt_range(week_start, week_end)}", "".join(body), tracks,
                     dark=web.get("theme", "auto") != "light", new_tab=False)

    # Source report
    rows = []
    for st in statuses:
        ok = not st.errors
        state = "OK" if ok else "Problem"
        detail = "; ".join(st.errors + st.notes)
        rows.append(f"""
<tr>
  <td class="d-ink" valign="top" style="padding:4px 10px 4px 0;font-family:{SANS};font-size:12.5px;color:{INK};"><a href="{esc(st.url)}" style="color:{INK};" class="d-title">{esc(st.name)}</a></td>
  <td class="d-muted" valign="top" style="padding:4px 10px 4px 0;font-family:{SANS};font-size:12.5px;color:{MUTED};text-align:right;white-space:nowrap;">{st.count}</td>
  <td valign="top" style="padding:4px 0;font-family:{SANS};font-size:12.5px;color:{MUTED if ok else MAROON};" class="{'d-muted' if ok else 'd-accent'}">{state}{(': ' + esc(detail)) if detail else ''}</td>
</tr>""")
    dept_html = ""
    if departments:
        cells = "".join(
            f'<tr><td class="d-ink" style="padding:3px 10px 3px 0;font-family:{SANS};font-size:12.5px;color:{INK};">{esc(name)}</td>'
            f'<td class="{"d-muted" if n else "d-accent"}" style="padding:3px 0;font-family:{SANS};font-size:12.5px;'
            f'color:{MUTED if n else MAROON};text-align:right;">{n if n else "none this week"}</td></tr>'
            for name, n in departments)
        dept_html = f"""
  <div class="d-accent" style="font-family:{SERIF};font-size:16px;color:{MAROON};padding:14px 0 6px 0;">Your seminar list this week</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="min-width:60%;">{cells}</table>"""
    body.append(f"""
<tr><td colspan="2" style="padding:36px 0 0 0;">
  <div class="d-rule" style="border-top:1px solid {RULE};"></div>{dept_html}
  <div class="d-accent" style="font-family:{SERIF};font-size:16px;color:{MAROON};padding:14px 0 6px 0;">Sources checked</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td class="d-muted" style="font-family:{SANS};font-size:12px;color:{MUTED};padding-bottom:4px;">Source</td>
      <td class="d-muted" style="font-family:{SANS};font-size:12px;color:{MUTED};padding-bottom:4px;text-align:right;padding-right:10px;">Events read</td>
      <td class="d-muted" style="font-family:{SANS};font-size:12px;color:{MUTED};padding-bottom:4px;">Status</td>
    </tr>
    {''.join(rows)}
  </table>
  <p class="d-muted" style="font-family:{SANS};font-size:12px;line-height:1.5;color:{MUTED};margin:14px 0 30px 0;">
    Generated {esc(f"{generated:%A}, {generated:%B} {generated.day} at {fmt_time(generated)}")}. To change sources, filters or your interests, edit config.yaml.
    Times are Chicago time. Always confirm on the event page; hosts sometimes update rooms and times late.
  </p>
</td></tr>""")

    return _page(f"Seminars, {fmt_range(week_start, week_end)}", "".join(body), tracks)


def _page(title: str, body: str, tracks: dict, dark: bool = True, new_tab: bool = False,
          max_width: int = 660, pad: str = "0 16px") -> str:
    scheme = "light dark" if dark else "light"
    base = '<base target="_blank">\n' if new_tab else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="{scheme}">
<meta name="supported-color-schemes" content="{scheme}">
{base}<title>{esc(title)}</title>
{_style_block(tracks, dark=dark)}
</head>
<body class="d-page" style="margin:0;padding:0;background:{PAPER};color:{INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="d-page" style="background:{PAPER};">
<tr><td align="center" style="padding:{pad};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:{max_width}px;">
{body}
</table>
</td></tr>
</table>
</body>
</html>"""


def render_embed(*, week_start: date, week_end: date, this_week: list[Event], next_week: list[Event],
                 tracks: dict, generated: datetime, web: dict) -> str:
    """Compact day-by-day list for an iframe on another website. Links open in a new tab."""
    updated = f"{generated:%A}, {generated:%B} {generated.day} at {fmt_time(generated)}"
    n = len(this_week)
    body = [_separate_section(f"Seminars this week, {fmt_range(week_start, week_end)}", this_week, tracks,
                              f"{n} talk{'s' if n != 1 else ''}, listed by day" if n else
                              "No more seminars are listed for the rest of this week.")]
    if next_week:
        body.append(_separate_section("Next week", next_week, tracks, f"{len(next_week)} talks announced so far"))
    full = web.get("page_url", "index.html")
    body.append(f"""
<tr><td colspan="2" class="d-muted" style="padding:18px 0 16px 0;font-family:{SANS};font-size:12px;line-height:1.5;color:{MUTED};">
  Updated {esc(updated)}. <a href="{esc(full)}" style="color:{MAROON};">Full listing with abstracts</a>
  &nbsp;&nbsp;<a href="{esc(web.get("ics_url", "seminars.ics"))}" style="color:{MAROON};">Subscribe in your calendar app</a>
</td></tr>""")
    return _page("UChicago seminars this week", "".join(body), tracks,
                 dark=web.get("embed_theme", "light") != "light", new_tab=True, max_width=900, pad="0 12px")


# --------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------
def render_text(*, week_start, week_end, this_week, unclassified, next_week, statuses, tracks, interests,
                generated, separate=(), next_separate_count=0, departments=()) -> str:
    out = [f"UChicago life science seminars, {fmt_range(week_start, week_end)}", ""]
    picks = [e for e in this_week if e.interest_hits]
    if picks:
        out += ["PICKED FOR YOU"]
        for e in sorted(picks, key=lambda e: e.start):
            out.append(f"  {e.start:%a} {fmt_time(e.start)}  {e.title}  [{', '.join(e.interest_hits)}]")
        out.append("")
    by_day = defaultdict(list)
    for e in this_week:
        by_day[e.start.date()].append(e)
    for d in sorted(by_day):
        out += [fmt_day(d).upper(), ""]
        for e in sorted(by_day[d], key=lambda e: e.start):
            label = tracks.get(e.track, {}).get("label", e.track)
            out.append(f"  {('All day' if e.all_day else fmt_time(e.start)):>8}  {e.title}")
            if e.speaker:
                out.append(f"            {e.speaker}")
            if e.location:
                out.append(f"            {e.location}")
            out.append(f"            {label}; {hosts_line(e)}")
            if e.host:
                out.append(f"            Faculty host: {e.host}")
            out.append(f"            {e.url}")
            out.append("")
    for label, sep in separate:
        if sep:
            out += [label.upper()]
            for e in sorted(sep, key=lambda e: e.start):
                who = f" ({e.speaker})" if e.speaker else ""
                out.append(f"  {e.start:%a %d} {fmt_time(e.start):>8}  {e.title}{who}")
                out.append(f"            {e.series or hosts_line(e)}  {e.url}")
            out.append("")
    if unclassified:
        out += ["ALSO ON THE CALENDAR, TOPIC NOT POSTED"]
        out += [f"  {e.start:%a %d} {fmt_time(e.start)}  {e.title}  {e.url}" for e in unclassified]
        out.append("")
    if next_week:
        out += ["NEXT WEEK"]
        out += [f"  {e.start:%a %d} {fmt_time(e.start)}  {e.title}" for e in sorted(next_week, key=lambda e: e.start)]
        out.append("")
    if departments:
        out += ["YOUR SEMINAR LIST THIS WEEK"] + [f"  {n:>3}  {name}" for name, n in departments] + [""]
    out += ["SOURCES"]
    for st in statuses:
        out.append(f"  {st.name}: {st.count} events" + (f" (PROBLEM: {'; '.join(st.errors)})" if st.errors else ""))
    return "\n".join(out)


# --------------------------------------------------------------------------
# iCalendar
# --------------------------------------------------------------------------
def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def render_ics(events: list[Event], generated: datetime) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//UChicago Seminar Digest//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:UChicago seminars"]
    stamp = generated.astimezone(UTC).strftime(fmt)
    for ev in sorted(events, key=lambda e: e.start):
        uid = "".join(c if c.isalnum() else "-" for c in ev.key)[:120] + "@uchicago-seminar-digest"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
        if ev.all_day:
            lines += [f"DTSTART;VALUE=DATE:{ev.start:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{(ev.start + timedelta(days=1)):%Y%m%d}"]
        else:
            lines += [f"DTSTART:{ev.start.astimezone(UTC):{fmt}}", f"DTEND:{end_of(ev).astimezone(UTC):{fmt}}"]
        lines += [f"SUMMARY:{_ics_escape(ev.title)}", f"LOCATION:{_ics_escape(ev.location)}",
                  f"URL:{ev.url}", f"DESCRIPTION:{_ics_escape(ev.url + chr(10) + chr(10) + ev.description[:1500])}",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
