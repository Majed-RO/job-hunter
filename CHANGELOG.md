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
