"""De-duplicate events from all sources and decide what goes in the digest."""
from __future__ import annotations

import re
from collections import defaultdict

from sources import FILTER_RANK, Event

# Words that make a talk bio-relevant. Each entry is a regex matched at a
# word start, case-insensitive.
BIO_TERMS = [
    r"bio\w*", r"cells?\b", r"cellular", r"protein\w*", r"genes?\b", r"genet\w*", r"genom\w*",
    r"transcript\w*", r"rnas?\b", r"m?rna\b", r"dna\b", r"crispr", r"chromatin", r"epigen\w*",
    r"immun\w*", r"microb\w*", r"bacteri\w*", r"vir(us|uses|al|ology)\b", r"pathogen\w*",
    r"infect\w*", r"neur\w*", r"brains?\b", r"synap\w*", r"cancers?\b", r"tumou?r\w*",
    r"oncolog\w*", r"clinic\w*", r"medic\w*", r"diseases?\b", r"patients?\b", r"health\b",
    r"epidemi\w*", r"evolution\w*", r"ecolog\w*", r"organisms?\b", r"organoids?\b",
    r"enzym\w*", r"metaboli\w*", r"tissues?\b", r"drugs?\b", r"pharma\w*", r"therap\w*",
    r"vaccin\w*", r"physiolog\w*", r"patholog\w*", r"membranes?\b", r"lipids?\b",
    r"peptides?\b", r"antibod\w*", r"stem cells?", r"single[- ]cell", r"cardi(ac|o\w*)",
    r"surg(ery|ical|eons?)\b", r"glycan\w*", r"glyco\w*", r"nucleic", r"nucleotide\w*",
    r"ribosom\w*", r"kinases?\b", r"receptors?\b", r"mitochondri\w*", r"chromosom\w*",
    r"embryo\w*", r"regenerat\w*", r"aging\b", r"cryo-?em", r"origins? of life",
    r"living (systems?|matter|cells?)", r"life sciences?", r"mice\b", r"mouse\b", r"zebrafish",
    r"drosophila", r"yeast", r"plants?\b", r"photosynth\w*", r"insects?\b", r"animals?\b",
    r"species\b", r"amino acids?", r"antibiotic\w*", r"tardigrad\w*", r"morpholog\w*",
]
BIO_RE = re.compile(r"\b(?:" + "|".join(BIO_TERMS) + r")", re.I)

MEDICINE_RE = re.compile(
    r"\b(grand rounds|clinic\w*|patients?|surg\w*|medicine|medical|physicians?|hospital|"
    r"pediatric\w*|oncolog\w*|cardiol\w*|radiol\w*|nursing|trials?|public health|"
    r"epidemiol\w*|bioethic\w*|ethics|residen(ts?|cy)|cme)\b", re.I)

TALK_TYPES = {"Lectures-Seminars", "Conference-Symposium", "Colloquiums", "Visiting Speakers",
              "Academic Conferences"}
TALK_RE = re.compile(
    r"\b(seminar|colloqui|lecture|symposi|talk|grand rounds|keynote|forum|defen[cs]e|"
    r"journal club|workshop|webinar|speaker|presents|work[- ]in[- ]progress|\bWIP\b)", re.I)
DEFENSE_RE = re.compile(r"\b(thesis|dissertation)\b.*\bdefen[cs]e|\bdefen[cs]e\b.*\b(thesis|dissertation|ph\.?d)", re.I)

SPEAKER_RE = re.compile(
    r"\b(?i:guest\s+)?(?i:speakers?)\s*:\s*"
    r"((?:Dr\.?\s+)?[A-Z][\w.'\-]*(?:\s+(?:[A-Z][\w.'\-]*|and|&|de|van|von|da|del|la|le))*?)"
    r"(?=\s*(?:,|\(|\bPh\.?D\b|\bM\.?D\b|\bMPH\b|\bProfessor\b|\bDepartment\b|\bUniversity\b|$|\s[a-z]))")


def speaker_from_description(ev: Event) -> str:
    m = SPEAKER_RE.search(ev.description)
    if not m:
        return ""
    name = m.group(1).strip()
    if len(name) < 4 or name.upper() in ("TBA", "TBD"):
        return ""
    surname = name.split()[-1]
    return "" if re.search(rf"\b{re.escape(surname)}\b", ev.title, re.I) else name


# --------------------------------------------------------------------------
# De-duplication
# --------------------------------------------------------------------------
def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


GENERIC = {"grand", "rounds", "department", "the", "and", "seminar", "series", "conference", "noon", "conf",
           "1cme", "cme", "dop"}


def _same_talk(a: Event, b: Event) -> bool:
    ta, tb = _tokens(a.title), _tokens(b.title)
    if not ta or not tb:
        return False
    if len(ta & tb) / len(ta | tb) >= 0.6:
        return True
    # one title is contained in the other's title or abstract (e.g. talk title vs. "Medicine Grand Rounds")
    for x, y in ((a, b), (b, a)):
        tx = _tokens(x.title) - GENERIC
        if len(tx) >= 3 and len(tx & _tokens(y.title + " " + y.description)) / len(tx) >= 0.8:
            return True
    # grand rounds of the same department at the same time
    sa, sb = _tokens(a.series or a.title) - GENERIC, _tokens(b.series or b.title) - GENERIC
    both_gr = all(re.search("grand rounds", (e.series or e.title), re.I) for e in (a, b))
    return both_gr and bool(sa & sb) and (a.kind == "cme") != (b.kind == "cme")


def _absorb(keep: Event, other: Event) -> None:
    if other.kind == "cme" and keep.kind != "cme":
        # Same grand rounds on the central calendar and CME: CME has the clean talk title,
        # series name and speaker; the calendar entry has the room and abstract.
        keep.title = other.title
        keep.series = other.series
        keep.speaker = other.speaker or keep.speaker
        keep.alt_url = other.url
        keep.track_hints = other.track_hints + [h for h in keep.track_hints if h not in other.track_hints]
    # A per-group title requirement only lifts if another configured group also lists the
    # talk without one; the campus-wide scan copy doesn't count.
    if keep.filter_mode == "campus" and other.filter_mode != "campus":
        keep.must_match = list(other.must_match)
    elif other.filter_mode != "campus" and not other.must_match:
        keep.must_match = []
    for g in other.groups:
        if g not in keep.groups:
            keep.groups.append(g)
    for h in other.track_hints:
        if h not in keep.track_hints:
            keep.track_hints.append(h)
    for t in other.event_types:
        if t not in keep.event_types:
            keep.event_types.append(t)
    if FILTER_RANK[other.filter_mode] > FILTER_RANK[keep.filter_mode]:
        keep.filter_mode = other.filter_mode
    if len(other.description) > len(keep.description):
        keep.description = other.description
    keep.host = keep.host or other.host
    # Groups often re-post a talk as their own event; the oldest listing is the original.
    if other.lw_id and other.owner and (not keep.lw_id or other.lw_id < keep.lw_id or not keep.owner):
        keep.owner, keep.lw_id = other.owner, other.lw_id
        keep.owner_mode = other.owner_mode or keep.owner_mode
    elif not keep.owner_mode:
        keep.owner_mode = other.owner_mode
    if keep.all_day and other.all_day and other.end and (not keep.end or other.end > keep.end):
        keep.end = other.end
    keep.location = keep.location or other.location


CREDENTIALS = re.compile(r"^(ph\.?d|m\.?d|mph|ms|msc|rn|do|dds|facog|faap|facp|jr|sr|ii|iii)\.?$", re.I)


def _surname(name: str) -> str:
    """'Yee Ling Wu, PhD' -> 'Wu'; 'Dr. Jane Q. Doe, MD, MPH' -> 'Doe'."""
    words = [w.strip(".,;") for w in name.split(",")[0].split()]
    words = [w for w in words if w and not CREDENTIALS.match(w) and w.lower() not in ("dr", "prof")]
    return words[-1] if words else ""


def dedupe(events: list[Event]) -> list[Event]:
    # 1) identical keys (same LiveWhale event cross-posted to several groups)
    by_key: dict[str, Event] = {}
    for ev in events:
        if ev.key in by_key:
            _absorb(by_key[ev.key], ev)
        else:
            by_key[ev.key] = ev
    merged = list(by_key.values())

    # a multi-day event can come back as a timed first day plus all-day later days
    by_id = defaultdict(list)
    for ev in merged:
        if ev.kind == "livewhale" and ev.lw_id:
            by_id[ev.lw_id].append(ev)
    gone: set[int] = set()
    for copies in by_id.values():
        timed = sorted([e for e in copies if not e.all_day], key=lambda e: e.start)
        if timed:
            for e in copies:
                if e.all_day:
                    _absorb(timed[0], e)
                    gone.add(id(e))
    merged = [e for e in merged if id(e) not in gone]

    # 2) same talk listed by different sources
    by_day = defaultdict(list)
    for ev in merged:
        by_day[ev.start.date()].append(ev)
    drop: set[int] = set()
    for day_events in by_day.values():
        # schedule-table rows: match on speaker surname appearing in another listing
        for t in [e for e in day_events if e.kind == "table"]:
            speaker = t.speaker or t.title.split(":", 1)[-1].split("(")[0].strip()
            surname = _surname(speaker)
            if len(surname) < 2:
                continue
            for o in day_events:
                if o is t or o.kind == "table" or id(o) in drop:
                    continue
                # case-sensitive: names are capitalized, and short ones ("He") must not match words
                if re.search(rf"\b{re.escape(surname)}\b", o.title + " " + o.description + " " + o.speaker):
                    _absorb(o, t)
                    drop.add(id(t))
                    break
        # everything else: same start time and the same talk
        rest = [e for e in day_events if id(e) not in drop]
        for i, a in enumerate(rest):
            if id(a) in drop:
                continue
            for b in rest[i + 1:]:
                if id(b) in drop:
                    continue
                if abs((a.start - b.start).total_seconds()) > 15 * 60 or not _same_talk(a, b):
                    continue
                if b.kind == "cme" and a.kind != "cme":
                    keep, other = a, b
                elif a.kind == "cme" and b.kind != "cme":
                    keep, other = b, a
                else:
                    keep, other = (a, b) if len(a.description) >= len(b.description) else (b, a)
                _absorb(keep, other)
                drop.add(id(other))
                if other is a:
                    break
    return [e for e in merged if id(e) not in drop]


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def _compile(patterns):
    return [re.compile(p, re.I) for p in (patterns or [])]


def _bio_hits(text: str) -> set[str]:
    return {m.group(0).lower() for m in BIO_RE.finditer(text)}


def _choose_track(ev: Event, rules=(), group_tracks=None) -> str:
    for rx, track in rules:
        if rx.search(ev.title) or rx.search(ev.series):
            return track
    # File cross-posted talks under the group that actually posted them.
    owner_track = (group_tracks or {}).get(ev.owner)
    if owner_track and owner_track != "auto":
        return owner_track
    hints = [h for h in ev.track_hints if h and h != "auto"]
    if hints:
        return hints[0]
    return "medicine" if MEDICINE_RE.search(ev.title + " " + ev.description[:400]) else "biology"


def classify(events: list[Event], cfg: dict) -> None:
    f = cfg.get("filters") or {}
    keep_re = _compile(f.get("keep_title"))
    excl_re = _compile(f.get("exclude_title"))
    include_defenses = cfg.get("include_thesis_defenses", True)
    show_unclassified = cfg.get("show_unclassified", True)
    interests = [i for i in (cfg.get("interests") or []) if str(i).strip()]
    interest_res = [(i, re.compile(r"\b" + re.escape(str(i)), re.I)) for i in interests]
    enabled_tracks = set((cfg.get("tracks") or {}).keys())
    rules = [(re.compile(r["pattern"], re.I), r["track"]) for r in (cfg.get("track_rules") or [])]
    group_tracks = {g["name"]: g.get("track", "auto") for g in (cfg.get("livewhale") or {}).get("groups", [])}

    for ev in events:
        ev.track = _choose_track(ev, rules, group_tracks)
        ev.is_defense = bool(DEFENSE_RE.search(ev.title))
        title = ev.title

        if ev.canceled:
            ev.verdict, ev.reason = "drop", "canceled"
            continue
        kept_by_rule = ev.kind in ("cme", "news") or any(r.search(title) for r in keep_re)
        hit = next((r.pattern for r in excl_re if r.search(title)), None)
        if ev.must_match and not any(re.search(p, title, re.I) for p in ev.must_match):
            ev.verdict, ev.reason = "drop", f"title lacks /{ev.must_match[0]}/ required for this group"
            continue
        if hit and not kept_by_rule:
            ev.verdict, ev.reason = "drop", f"title matches exclude rule /{hit}/"
            continue
        if ev.is_defense and not include_defenses:
            ev.verdict, ev.reason = "drop", "thesis defense (disabled in config)"
            continue

        talk_like = (
            ev.kind in ("table", "ics", "cme", "news")
            or bool(set(ev.event_types) & TALK_TYPES)
            or bool(TALK_RE.search(title))
        )
        if not talk_like:
            ev.verdict, ev.reason = "drop", f"not a talk (types: {', '.join(ev.event_types) or 'none'})"
            continue

        if ev.kind == "livewhale" and ev.owner_mode and FILTER_RANK[ev.owner_mode] < FILTER_RANK[ev.filter_mode]:
            # Groups often re-post other groups' events as their own copies. Judge the talk by
            # the group that originally posted it, so re-posting doesn't make a talk relevant.
            ev.filter_mode = ev.owner_mode
        if ev.filter_mode == "all":
            ev.verdict, ev.reason = "keep", "talk from a bio/medicine group"
        elif ev.filter_mode == "bio":
            title_hits = _bio_hits(title)
            desc_hits = _bio_hits(ev.description)
            if title_hits:
                ev.verdict, ev.reason = "keep", f"bio terms in title: {', '.join(sorted(title_hits))}"
            elif len(ev.description) < 40:
                ev.verdict = "unclassified" if show_unclassified else "drop"
                ev.reason = "no abstract posted, topic unknown"
            elif len(desc_hits) >= 2 or (len(desc_hits) == 1 and len(ev.description) < 250):
                ev.verdict, ev.reason = "keep", f"bio terms in abstract: {', '.join(sorted(desc_hits))}"
            else:
                ev.verdict, ev.reason = "drop", "abstract doesn't look bio-related"
        else:  # "title" groups and the campus scan: stricter, needs bio words in the title
            title_hits = _bio_hits(title)
            who = "re-posted talk" if ev.filter_mode == "title" else "campus talk"
            if title_hits:
                ev.verdict, ev.reason = "keep", f"{who} with bio title terms: {', '.join(sorted(title_hits))}"
            else:
                ev.verdict, ev.reason = "drop", f"{who}, title not bio-related"
                continue

        if ev.track not in enabled_tracks:
            ev.verdict, ev.reason = "drop", f"track '{ev.track}' not enabled"
            continue

        ev.speaker = ev.speaker or speaker_from_description(ev)
        haystack = " ".join([ev.title, ev.description, " ".join(ev.groups), ev.host])
        ev.interest_hits = [name for name, rx in interest_res if rx.search(haystack)]
