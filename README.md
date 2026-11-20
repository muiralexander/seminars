# UChicago life science seminar digest

A weekly digest of UChicago biology, medicine, cancer, bioengineering and
bio-relevant chemistry seminars, plus every UCMC department's grand rounds.
It runs each Monday morning and emails you the week's talks, with links to
add any of them to Google Calendar or Outlook and an .ics file of the whole
week.

## Where the seminars come from

- **UChicago events calendar** (events.uchicago.edu). Most BSD departments,
  committees and institutes publish here, including the Comprehensive Cancer
  Center, Ben May, Committee on Cancer Biology, Human Genetics, Microbiology,
  Neuroscience, IBD, NITMB, the Program in Computational Biology (PCB
  Seminar and PCB Population Genetics Seminar) and Chemistry. About 55 groups are read
  individually, and the whole campus calendar is also scanned for bio talks
  hosted elsewhere.
- **UChicago CME** (cme.uchicago.edu). Every accredited grand rounds is a
  "Regularly Scheduled Series" here, so this is the one place that lists all
  of them: Medicine, Surgery, Neurology, Neurosurgery, Pediatrics, OB/GYN,
  Radiology, Psychiatry, Anesthesia, Pathology, Heme/Onc, Cardiology,
  Urology, ENT and more. Series are found by name on each run, so the
  January rename (CY26 to CY27) is handled automatically. One-off CME
  courses on cancer topics (for example the Multidisciplinary Breast Cancer
  Symposium, or the cancer center's symposia) are included too; see
  `cme.course_match`.
- **PME website**, because Molecular Engineering's talks aren't on the
  central calendar. Only bio-relevant talks are kept.
- **Schedule tables** for series that post a season schedule instead of
  calendar entries: the BMB Academic Seminar Series, MGCB Thursdays and the
  IBD Interdisciplinary Seminar.
- **News announcements**. Ben May announces its department seminars and the
  Fitch Lecture only as "Save the date" news posts on its website, so those
  posts are read for the speaker, topic, time and room.
- **Hand-entered schedules** in `extra_seminars.yaml`, for series that
  announce talks only by email (see below).
- **Google Calendars and other .ics feeds**. Ecology & Evolution runs its
  seminar series, Darwin's Weekly and special lectures from public Google
  Calendars embedded on its website. You can add any other feed, such as a
  lab's journal club calendar.

Talks listed in several places are merged into one entry. When a group
re-posts another group's talk as its own copy, the talk is judged by the
group that originally posted it, so a physics lecture re-posted by a BSD
committee is still treated as a physics lecture. Office hours,
admissions sessions, admin drop-ins, resident teaching conferences, patient
webinars and community events are left out.

## Set it up to run every Monday (GitHub Actions)

1. Create a **private** GitHub repository and upload these files, keeping
   the `.github/workflows` folder.
2. Get SMTP credentials for the account that will send the email. With
   Gmail, turn on 2-step verification, then create an app password at
   myaccount.google.com/apppasswords. UChicago's Microsoft 365 accounts
   generally don't allow password-based SMTP sending, so a personal Gmail
   account is the simplest sender. It can still deliver to your
   uchicago.edu address.
3. In the repository, open **Settings, Secrets and variables, Actions** and
   add these secrets:

   | Secret | Example |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | `you@gmail.com` |
   | `SMTP_PASSWORD` | the 16-character app password |
   | `DIGEST_TO` | `you@uchicago.edu` (comma-separate for several) |
   | `DIGEST_FROM` | optional, defaults to `SMTP_USER` |

4. Open the **Actions** tab, choose "Weekly seminar digest", and click
   **Run workflow** to test it (tick "Send the email digest" to get an
   email on a test run). After that it runs every morning at about 5 to 6 am
   Chicago time: it refreshes the web page daily and sends the email on
   Mondays. GitHub sometimes starts scheduled jobs late.

Each run also saves the digest in `archive/` and as a downloadable artifact.
If no email secrets are set, it still builds the digest; download it from
the run's page.

## Put it on your lab website

The workflow also publishes a web version to GitHub Pages and refreshes it
every morning, so new talks appear within a day of being posted. It
produces three files:

- `index.html`: the full listing (rest of this week and next week, with
  abstracts). Your personal interests and the source report are left off.
- `embed.html`: a compact day-by-day list for embedding in another site.
  It's always light-themed, and its links open in a new tab.
- `seminars.ics`: a calendar feed lab members can subscribe to.

**1. Turn on GitHub Pages.** On a free GitHub account, Pages only works
from a **public** repository (Settings, General, Danger Zone, Change
visibility). GitHub Pro or Team can publish from a private one. Making the
repo public is safe: your email password lives in GitHub's secrets, not in
the files. What becomes visible is `config.yaml` (including your
interests list) and `extra_seminars.yaml`. Then go to **Settings, Pages**,
set **Source** to **GitHub Actions**, and run the workflow once. Your page
will be at `https://YOUR-USERNAME.github.io/YOUR-REPO/`.

**2. Add it to the lab site.** The simplest option is a "Seminars" link to
that address. To show the list inside a page, embed it:

```html
<iframe src="https://YOUR-USERNAME.github.io/YOUR-REPO/embed.html"
        title="UChicago seminars this week"
        style="width:100%; height:900px; border:0;" loading="lazy"></iframe>
```

- **WordPress** (including voices.uchicago.edu sites): add a **Custom HTML**
  block and paste the code. If the frame disappears after saving, your
  account isn't allowed to add frames. Ask the site administrator, or use a
  link instead.
- **Google Sites:** Insert, Embed, By URL, and paste the `embed.html`
  address.
- **Squarespace / Wix:** use an Embed or Code block with the code above.
- **Drupal and other department-managed sites:** frames are often blocked
  by the site's text format. Ask the site's web administrator to allow it,
  or use a link.

**3. Calendar subscription (optional).** Share
`https://YOUR-USERNAME.github.io/YOUR-REPO/seminars.ics`. In Google
Calendar use Other calendars, From URL. In Outlook use Add calendar,
Subscribe from web. Calendar apps refresh subscribed feeds on their own
schedule, which can take several hours or longer.

Settings for the web page are under `web:` in `config.yaml` (title,
light/dark theme, and whether to show your interests).

## Or run it on your own computer

```
pip install -r requirements.txt
python digest.py                 # writes out/digest-<date>.html, .txt and .ics
python digest.py --send          # also emails it (set the SMTP_* and DIGEST_TO environment variables)
python digest.py --web site      # also writes the web page to site/ (open site/index.html)
```

To schedule it on a Mac or Linux machine, add a cron entry with `crontab -e`.
The computer has to be on and awake at that time:

```
0 7 * * 1  cd /path/to/uchicago-seminar-digest && /usr/bin/python3 digest.py --send
```

## Make it yours

Everything is in `config.yaml`:

- **`interests`**: keywords, speaker or host names. Matching talks go in a
  "Picked for you" box at the top of the digest.
- **`livewhale.groups`**: add or remove calendar groups. Run
  `python digest.py --list-groups` to see all 400+ exact names.
- **`cme`**: by default this includes all UCMC grand rounds series and
  leaves out Endeavor Health (NorthShore) series, which the same CME office
  accredits. Use `exclude_series` to drop series you don't want, or
  `include_affiliates: true` to add Endeavor's.
- **`separate_sections`**: by default every talk, grand rounds included,
  appears the same way in one day-by-day schedule. Set it to
  `[grand_rounds]` to move grand rounds into their own compact section.
- **`departments`**: the items in the "Your seminar list this week" table
  at the bottom of each digest (set up to match your list of departments,
  committees and series). A count of 0 for several weeks
  in a row is a sign that a department has started posting seminars
  somewhere new.
- **`filters`**: title rules for what to drop or always keep.
- A group's **`filter`** setting: `all` keeps every talk it posts, `bio`
  needs a bio-related title or abstract, and `title` needs a bio-related
  title. `title` is for groups that mostly re-post other groups' events
  (the BSD graduate affairs office and the Cancer Biology and Immunology
  committees). `keep_title` lets a group's own series through regardless.

After changing filters, run `python digest.py --explain`. It writes
`out/explain-<date>.txt`, which lists every event read and why it was kept
or dropped.

Other options: `--week-of 2026-11-30` builds any week, and
`--interests "T cell,microbiome"` tries interests without editing the
config.

## Seminar series that aren't published online

As of November 2026, a few series don't post their 2026-27 talks anywhere
the digest can read: not on the UChicago events calendar, their own
websites, or CME. These are the **Committee on Cancer Biology** seminar
series, the **Committee on Immunology** seminar series (Mondays at noon,
BSLC 109) and the **Rowley seminar series** (Wednesdays at noon).

Two ways to get them in:

1. **Paste the schedule into `extra_seminars.yaml`** when the quarter's
   schedule email arrives. The file explains the format and already has the
   Immunology seminar set up. If a talk later also shows up on a calendar,
   the two listings merge automatically.
2. **Ask the series coordinator to post talks to their group on
   events.uchicago.edu.** The Cancer Biology and Immunology websites already
   display their group's calendar, which is why those pages look empty now.
   The Committee on Cancer Biology did post its seminar series this way in
   2024. Once the talks are posted there, the digest picks them up with no
   changes needed.

The Department of Pathology's seminar series (Pathobiology of Disease) is
listed through CME and included automatically. The table on the Pathology
website stopped being updated in 2023.

## Upkeep

The bottom of every digest has a **Sources checked** table showing how many
events each source returned and any errors. Things to watch for:

- **A schedule-table source shows 0 entries or an error.** These pages
  often move at the start of an academic year. The IBD URL in particular
  changes every year; update `schedule_tables` in `config.yaml`.
- **A temporary error** (for example HTTP 503) usually clears by the
  next week.
- **Every source shows 0.** A site has probably been redesigned, and the
  code in `sources.py` needs updating.

Known limits:

- **Organismal Biology & Anatomy and Pharmacological & Physiological
  Sciences** had no seminar listings anywhere I could find for 2026-27. OBA's
  events page only links an old 2019 Evolutionary Morphology schedule, and
  PPS has no website or calendar group of its own. Their talks will appear
  automatically if they are posted to the UChicago events calendar under
  BSD or a co-hosting group. If you find where they do post, add it as a
  schedule table, .ics feed or calendar group.

- Chemistry and some PME talks are posted with only a speaker's name, so
  their topic can't be checked. They're listed under "topic not posted"
  rather than dropped.
- CME sessions sometimes have no room listed, so check the session page.
- Hosts sometimes change rooms and times late, so confirm on the event
  page before you go.

## Files

- `digest.py`: entry point, scheduling window, email
- `sources.py`: readers for each website
- `classify.py`: merging duplicates, filtering, tracks, interests
- `render.py`: HTML email, plain text and .ics output
- `config.yaml`: settings
- `extra_seminars.yaml`: hand-entered schedules
