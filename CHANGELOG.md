# Changelog

What changed in each version of job-hunter, newest first. The version you
have is printed at the start of every run (`[config] job-hunter 1.0.0 ...`)
and at the top of `output/run_summary.md`.

Version numbers mean:

- **1.0.x**: fixes only. Update freely.
- **1.x.0**: new features. Your settings keep working.
- **x.0.0**: something you set up must change. The notes say what to redo.

To get notified of new versions, click **Watch → Custom → Releases** on the
GitHub page. How to update without losing your settings: see "Updating" in the
[README](README.md#updating).

## Unreleased

**LinkedIn: location gate before scoring**
- New free pre-filter for LinkedIn postings (`LOCATION_GATE_SITES`):
  - "likely on-site": listed in a city and the text never says remote (Al
    Salah Holding, Dubai, was shortlisted on stack alone);
  - "location outside allowlist": tied to a place outside `LOCATION_HOME` /
    `LOCATION_ALLOWED` (Kraken in Brazil, Block Labs in Croatia, Cohere in
    New York).
- Kept whatever the location: text saying the role is open from anywhere
  (`LOCATION_GLOBAL_REMOTE`), and, for a location that is a whole country,
  text saying fully remote / remote-first (`LOCATION_REMOTE_STATEMENT`),
  which the LLM then judges.
- Postings not written in English are dropped as "not in English": they are
  local jobs (`LOCATION_GATE_MIN_ENGLISH_SHARE`). Arabic postings are kept
  (`LOCATION_GATE_KEEP_LANGUAGES`).
- Backtest on the 28 LinkedIn postings ever shortlisted: 14 kept (all 5
  BairesDev "from anywhere", G2i, Torentify, Aethel, Deel EMEA, Gulf
  postings), 13 dropped (Kraken, Block Labs, Cohere, Al Salah, Stefanini,
  Newfold, ...), 1 page gone.
- Dropped postings are listed under "Dropped before scoring" in each
  shortlist and kept in `history.csv`, as with every pre-filter rule.

**LinkedIn: the scorer sees each posting's real location**
- LinkedIn postings now take their location from their own job page. JobSpy
  replaced a country-only location ("Brazil", "Croatia") with the search's
  country, which is blank for the Worldwide search: 19 of the 28 LinkedIn
  postings ever shortlisted were scored without a location. The `location`
  column in the CSVs and `history.csv` is fixed the same way.
- The prompt also gets a `Remote:` line (the posting's is_remote flag).
- The prompt template, location and remote flag are now part of the score
  cache key, so the next run re-scores every posting once (about $0.001 each).

**Test runs: `--dry-run`**
- Writes the shortlists, CSVs and run summary to `output/dry_run/` instead of
  `output/`, and doesn't append to `history.csv`, so a test run leaves the
  real shortlist (and the shortlist recheck) alone. The score cache is still
  used and updated.

**Indeed and We Work Remotely: more postings**
- Indeed: each remote-only search now runs a date-filtered query as well as
  the remote-filtered one and merges them. Since 1.0.0 only the
  remote-filtered query ran, and most of its results were older than
  `HOURS_OLD`: a run kept 2-7 Indeed postings (about 60 before); the first
  run with both kept 105. The Indeed scrape still takes about 2 minutes.
- We Work Remotely: postings up to 30 days old are read (new setting
  `SITE_MAX_AGE_DAYS`), since this small board's postings stay open for
  weeks. Postings already scored come from the cache. Also reads the parent
  "remote-programming-jobs" feed.
- `stack_fit` threshold for Indeed and We Work Remotely lowered from 70 to 65
  (`SITE_MIN_SKILL_MATCH_PERCENT`).
- Indeed also searches Kuwait, Egypt and Morocco (`SITE_LOCATIONS["indeed"]`).
  LinkedIn doesn't, since those markets are small and LinkedIn searches are
  slow.
- Wide search terms: outside LinkedIn, a posting confirmed remote is scored
  even if it names none of `WIDE_REQUIRED_KEYWORDS` (about 6 a run on Indeed).

**LinkedIn: closed postings**
- Postings whose LinkedIn page says "No longer accepting applications" are
  dropped before scoring, listed under "Dropped before scoring". Before, they
  were scored and could be shortlisted, and their applicant count was misread
  as 0 (closed pages show "Be among the first 25 applicants").
- New `recheck_shortlist.py`: run it before reading `shortlist_linkedin.md`
  to move postings that closed since the run to the end of the list. LinkedIn
  often closes popular postings within a day. A "Re-check log" at the bottom
  of the shortlist records what each re-check moved.

## 1.0.0 — 2026-10-08

First public version.

**Searching**
- Searches LinkedIn, Indeed, We Work Remotely and jobs.ps (Palestine) for
  postings from the last few days, per search term and location.
- Broad "wide" search terms, kept only when the posting mentions one of your
  keywords.
- Reads each LinkedIn job page for the full description, applicant count,
  employment type, post age and Easy Apply.
- Merges duplicates: the same link, the same title and company (ignoring
  reference codes like REF#123), or the same company with an identical
  description. The other copies' links are kept.

**Filtering and scoring**
- A free pre-filter skips clear non-starters before any AI call: junior or
  intern titles, security clearance, citizens-only, US payroll (W2), on-site or
  hybrid, region-locked remote, and postings older than a few days.
- An AI model (Claude Haiku 5.5 through OpenRouter by default, or Google
  Gemini) scores every remaining posting against your resume and
  requirements: an overall score, four sub-scores, a verdict (apply, maybe or
  skip), what matches, what's missing and any blockers.
- Postings with many applicants are scored and marked **Crowded** instead of
  dropped.
- Scores are cached, so a posting is only paid for once.

**Reports**
- One shortlist per job site, best first, then the postings the AI liked that a
  score limit held back, then everything the pre-filter dropped and why.
- A run summary: postings through each step, time per step, and searches that
  hit their result limit.
- `history.csv` keeps every run's results; `check_missed.py` tells you whether
  the script saw a job you found yourself, and what happened to it.

**Setup**
- Step-by-step quick start in English and Arabic, for Windows and
  macOS/Linux.
- Personal settings live in `config_local.py`, which is never uploaded.
- Works with the current JobSpy (1.3.0) and pandas (3.x), and with older
  JobSpy 1.1.x.
- MIT license.
