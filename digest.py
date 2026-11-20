#!/usr/bin/env python3
"""Weekly digest of UChicago biology, medicine, bioengineering and bio-relevant chemistry seminars.

Usage:
  python digest.py                     build the digest for the coming Monday-Sunday week
  python digest.py --week-of 2026-11-16
  python digest.py --send              also email it (needs SMTP_* env vars, see README)
  python digest.py --explain           also write out/explain-<date>.txt (why each event was kept/dropped)
  python digest.py --list-groups       print every group name on events.uchicago.edu
  python digest.py --web site          also write a web page for a lab website to site/
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

import classify
import render
import sources

HERE = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("tracks", {})
    return cfg


def week_bounds(today: date, week_of: str | None) -> tuple[date, date]:
    if week_of:
        d = date.fromisoformat(week_of)
        start = d - timedelta(days=d.weekday())
    else:
        # The Monday on or after today: run on a Monday, get that week.
        start = today + timedelta(days=(7 - today.weekday()) % 7)
    return start, start + timedelta(days=6)


def collect(cfg: dict, start: date, end: date, tz, today: date):
    session = sources.make_session()
    events, statuses = [], []

    if cfg.get("livewhale"):
        evs, sts = sources.fetch_livewhale(cfg["livewhale"], start, end, tz, session)
        events += evs
        statuses += sts
    cme = cfg.get("cme") or {}
    if cme.get("enabled", True) and cme:
        evs, st = sources.fetch_cme(cme, start, end, tz, session, detail_until=start + timedelta(days=6))
        events += evs
        statuses.append(st)
    for src in cfg.get("pme") or []:
        evs, st = sources.fetch_pme(src, start, end, tz, session)
        events += evs
        statuses.append(st)
    for src in cfg.get("schedule_tables") or []:
        evs, st = sources.fetch_schedule_table(src, start, end, tz, session, today)
        events += evs
        statuses.append(st)
    for src in cfg.get("news_announcements") or []:
        evs, st = sources.fetch_news_announcements(src, start, end, tz, session)
        events += evs
        statuses.append(st)
    for src in cfg.get("ics_feeds") or []:
        evs, st = sources.fetch_ics(src, start, end, tz, session)
        events += evs
        statuses.append(st)

    extra = cfg.get("extra_seminars_file")
    if extra:
        evs, st = sources.load_extra_seminars(HERE / extra, start, end, tz)
        events += evs
        statuses.append(st)

    # keep only events that start inside the window
    lo = datetime(start.year, start.month, start.day, tzinfo=tz)
    hi = datetime(end.year, end.month, end.day, tzinfo=tz) + timedelta(days=1)
    events = [e for e in events if lo <= e.start < hi]
    return classify.dedupe(events), statuses


def department_counts(cfg: dict, week_events) -> list[tuple[str, int]]:
    """How many of this week's talks each listed department hosts or co-hosts."""
    import re
    out = []
    for d in cfg.get("departments") or []:
        rx = re.compile(d["match"], re.I) if d.get("match") else None
        trx = re.compile(d["title"], re.I) if d.get("title") else None
        n = sum(1 for e in week_events
                # the host, not groups that merely re-post the talk
                if (rx and any(rx.search(x or "") for x in [e.owner, e.source, e.series, *e.groups[:1]]))
                or (trx and trx.search(e.title)))
        out.append((d["name"], n))
    return out


def write_explain(path: Path, events) -> None:
    order = {"keep": 0, "unclassified": 1, "drop": 2}
    lines = []
    for e in sorted(events, key=lambda e: (order.get(e.verdict, 3), e.start)):
        lines.append(f"[{e.verdict.upper():12}] {e.start:%a %m-%d %H:%M}  {e.title}")
        lines.append(f"               track={e.track}  source={e.source}  groups={', '.join(e.groups)}")
        lines.append(f"               reason: {e.reason}")
    path.write_text("\n".join(lines), encoding="utf-8")


def send_email(subject: str, html_body: str, text_body: str, ics: str, ics_name: str) -> None:
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("DIGEST_TO")
    if not host or not to:
        raise SystemExit("Email not sent: set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and DIGEST_TO.")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("DIGEST_FROM") or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    msg.add_attachment(ics.encode("utf-8"), maintype="text", subtype="calendar", filename=ics_name)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=60) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--week-of", help="any date in the target week (YYYY-MM-DD)")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--send", action="store_true", help="email the digest")
    ap.add_argument("--explain", action="store_true", help="write a keep/drop report")
    ap.add_argument("--interests", help="comma-separated interests, overrides config for this run")
    ap.add_argument("--list-groups", action="store_true", help="list all events.uchicago.edu groups")
    ap.add_argument("--web", metavar="DIR",
                    help="also write a web page (index.html, embed.html, seminars.ics) to DIR, "
                         "covering today through Sunday plus next week")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    if args.list_groups:
        base = (cfg.get("livewhale") or {}).get("base_url", "https://events.uchicago.edu")
        print("\n".join(sources.list_livewhale_groups(base)))
        return 0
    if args.interests is not None:
        cfg["interests"] = [s.strip() for s in args.interests.split(",") if s.strip()]

    tz = ZoneInfo(cfg.get("timezone", "America/Chicago"))
    now = datetime.now(tz)
    if args.web and not args.week_of:
        # the web page shows the rest of the current week, refreshed daily
        week_start = now.date()
        week_end = week_start + timedelta(days=6 - week_start.weekday())
    else:
        week_start, week_end = week_bounds(now.date(), args.week_of)
    fetch_end = week_end + timedelta(days=7 if cfg.get("include_next_week", True) else 0)

    print(f"Collecting seminars for {week_start} to {fetch_end}...", file=sys.stderr)
    events, statuses = collect(cfg, week_start, fetch_end, tz, now.date())
    classify.classify(events, cfg)

    in_week = lambda e: e.start.date() <= week_end  # noqa: E731
    sep_tracks = list(cfg.get("separate_sections") or [])
    kept = [e for e in events if e.verdict == "keep"]
    this_week = [e for e in kept if in_week(e) and e.track not in sep_tracks]
    separate = [(cfg["tracks"].get(t, {}).get("label", t), [e for e in kept if in_week(e) and e.track == t])
                for t in sep_tracks]
    unclassified = [e for e in events if e.verdict == "unclassified" and in_week(e)]
    next_week = [e for e in kept if not in_week(e) and e.track not in sep_tracks]
    next_sep = sum(1 for e in kept if not in_week(e) and e.track in sep_tracks)

    ctx = dict(week_start=week_start, week_end=week_end, this_week=this_week, unclassified=unclassified,
               next_week=next_week, statuses=statuses, tracks=cfg["tracks"],
               interests=cfg.get("interests") or [], generated=now, separate=separate,
               next_separate_count=next_sep, departments=department_counts(cfg, this_week + [e for _, sep in separate for e in sep]))
    html_body = render.render_html(**ctx)
    text_body = render.render_text(**ctx)
    week_all = this_week + [e for _, sep in separate for e in sep]
    ics = render.render_ics(week_all + unclassified, now)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"digest-{week_start:%Y-%m-%d}"
    (out / f"{stem}.html").write_text(html_body, encoding="utf-8")
    (out / f"{stem}.txt").write_text(text_body, encoding="utf-8")
    (out / f"{stem}.ics").write_text(ics, encoding="utf-8", newline="")
    (out / "latest.html").write_text(html_body, encoding="utf-8")
    if args.explain:
        write_explain(out / f"explain-{week_start:%Y-%m-%d}.txt", events)

    if args.web:
        wcfg = dict(cfg.get("web") or {})
        site = Path(args.web)
        site.mkdir(parents=True, exist_ok=True)
        next_all = [e for e in kept if not in_week(e)]
        (site / "index.html").write_text(render.render_html(**{**ctx, "web": wcfg}), encoding="utf-8")
        (site / "embed.html").write_text(render.render_embed(
            week_start=week_start, week_end=week_end, this_week=week_all, next_week=next_all,
            tracks=cfg["tracks"], generated=now, web=wcfg), encoding="utf-8")
        (site / "seminars.ics").write_text(render.render_ics(week_all + unclassified + next_all, now),
                                           encoding="utf-8", newline="")
        (site / ".nojekyll").write_text("", encoding="utf-8")
        print(f"Wrote web page to {site}/ (index.html, embed.html, seminars.ics)", file=sys.stderr)

    problems = [s for s in statuses if s.errors]
    n_sep = sum(1 for e in kept if in_week(e) and e.track == "grand_rounds")
    this_week_seminars = len(this_week) + sum(len(sep) for _, sep in separate) - n_sep
    print(f"{this_week_seminars} seminars and {n_sep} grand rounds this week, {len(unclassified)} without a topic, "
          f"{len(next_week)} next week. Wrote {out / (stem + '.html')}", file=sys.stderr)
    for s in problems:
        print(f"  source problem: {s.name}: {'; '.join(s.errors)}", file=sys.stderr)

    if args.send:
        subject = (f"Seminars this week: {render.fmt_range(week_start, week_end)} "
                   f"({this_week_seminars} seminars, {n_sep} grand rounds)")
        send_email(subject, html_body, text_body, ics, f"{stem}.ics")
        print("Email sent.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
