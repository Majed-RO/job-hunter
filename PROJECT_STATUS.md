# Job Hunter — project status / session handoff

Last updated: 2026-09-25 (item 6: LinkedIn job-page fields built; see below). Read this
plus `README.md` to pick up the project.
`README.md` documents how the tool works; this file records **where things
stand and what's unresolved**.

Project root: `~/job-hunter`

---

## What this is

A Python pipeline that scrapes LinkedIn + Indeed via JobSpy, scores each
posting against the user's resume with an LLM (routed through OpenRouter), and
writes a filtered shortlist. See `README.md` for full usage.

Files: `job_match.py` (pipeline), `config.py` (all tunables),
`.env` (`OPENROUTER_API_KEY`), `resume.md`, `constraints.txt`,
`test_regions.py` (checks which LinkedIn region strings resolve),
`probe_linkedin_guest.py` (saves sample LinkedIn public job pages to
`output/guest_probe/` — for checking what fields the page exposes),
`output/` (results), `.jobcache/scores.json` (score cache).

---

## Decisions already made (don't re-litigate)

- **OpenRouter, not a direct Anthropic key.** Default model
  `anthropic/claude-haiku-4.5`. `OPENROUTER_MODEL` in `.env` overrides
  `config.MODEL_NAME` — the user noted this is redundant for a single machine
  and may drop the `.env` line.
- **`.env` file, not `export`.** Loaded via `python-dotenv`.
- **All tunables in `config.py`**, CLI flags are one-off overrides only.
- **One module per job site** under `sources/` (`linkedin.py`, `indeed.py`,
  `jobsps.py`), each exposing `fetch(args) -> DataFrame`, lazily imported
  via `sources/__init__.py`'s `REGISTRY`. `config.SOURCES` / `--sites`
  choose which run. Outputs are per site (`shortlist_<site>.md`,
  `jobs_scored_<site>.csv`); `history.csv` and the score cache are shared.
- **History log, not timestamped per-run files.** `jobs_scored_<site>.csv` and
  `shortlist_<site>.md` are overwritten each time that site runs; `output/history.csv` is
  append-only with a `run_timestamp` column.
- **Locations as a flat list** (`LOCATIONS`), with automatic per-board
  routing: JobSpy-known countries hit LinkedIn + Indeed; regions
  (`"MENA"`), `"Worldwide"`, and countries Indeed lacks (`"Palestine"`) are
  LinkedIn-only. Live value: `["United States", "Worldwide", "MENA"]`
  (Saudi Arabia was considered but is not currently in the list — note it
  would add Indeed coverage, which `MENA` and `Worldwide` cannot).
- **Constraints live in `config.CONSTRAINTS` only.** `constraints.txt` was
  deleted on 2026-09-24: it still held the *original* draft while
  `config.py` held the rewrite, so passing `--constraints constraints.txt`
  would have silently scored against stale text. `--constraints` still
  works for a deliberate one-off override, and now exits with a clear
  message if the path doesn't exist.
- **`"MENA"` is the correct LinkedIn region string.** Verified by test:
  `"MENA"` and `"Middle East"` both resolve; `"Middle East and North
  Africa"` returns zero. `"Middle East"` pulls in Cyprus and drops North
  Africa, so `"MENA"` is preferred.
- **Viewing results:** `glow output/shortlist_<site>.md` for the report;
  `column -s, -t output/jobs_scored_<site>.csv | less -S` or VisiData (`vd`) for
  the CSVs.

## Patches / non-obvious code

- **JobSpy LinkedIn location patch** in `job_match.py` (just after the
  imports). JobSpy maps each LinkedIn result's country against a fixed
  internal list and raises `ValueError` on anything missing from it
  (Algeria, Jordan, Palestine, Lebanon, Iraq, Tunisia), which discards the
  **entire** LinkedIn search for that term. The patch keeps the country
  string as LinkedIn gave it. Essential for MENA searches. A JobSpy upgrade
  could break it — it fails loudly with `AttributeError` at startup if so.
- **Fixed bug (2026-09-24):** `write_outputs()` called `append_history()`
  twice, duplicating every row in `history.csv`. If `history.csv` predates
  this fix, dedupe it:
  `pd.read_csv(...).drop_duplicates().to_csv(..., index=False)`.

## Cache gotcha

`.jobcache/scores.json` is keyed on **resume text + model + job URL** —
**not** on constraints or blocker patterns. After editing `constraints.txt`
or `HARD_BLOCKERS`, run `rm .jobcache/scores.json` or previously-scored
postings keep stale scores.

---

## Current state of results

Two runs so far. Pooled apply-rate by location:

| location | postings | apply | rate |
|---|---|---|---|
| MENA | 29 | 3 | 10% |
| United States | 97 | 3 | 3% |
| Worldwide | 54 | 1 | 2% (but 10 `maybe`) |

Run 2 (18 postings): `apply` 2, `maybe` 4, `skip` 12. Median scores among
skips: `overall` 15, `stack_fit` 75, `logistics_fit` 5, `seniority_fit` 35.

**Diagnosis: rejections are logistics-driven, not skill-driven.** `stack_fit`
holds at 75 among rejects while `logistics_fit` sits at 5–15. The search
terms are working; the roles are unreachable (onsite/hybrid, W2/US payroll,
geo-locked remote). Tightening search terms is therefore *not* the highest
-leverage fix — location mix is.

**`HARD_BLOCKERS` underperformed.** 22 patterns were added and tested
(21/21 true positives, 0/14 false positives on a synthetic corpus), but in
run 2 they fired ~0 times. Reason: that batch's rejections were
*inference-based* — "no mention of contractor/B2B option", "posting implies
onsite" — and regex cannot detect the absence of a statement. Regex still
helps for postings that state their blockers outright (run 1's did).

---

## Open items

1. **Junior/intern leakage — FIXED 2026-09-24, needs a validation run.**
   `seniority_fit` median fell to 35; one posting was an internship. Added
   `config.TITLE_BLOCKERS` (new list, separate from `HARD_BLOCKERS`) with
   `\b(intern|internship|junior|jr\.?|entry[- ]level|graduate|apprentice)\b`,
   matched against the **title only** — matching it against the full
   description too (like `HARD_BLOCKERS` does) would false-positive on
   phrases like "Bachelor's or graduate degree" or "mentors junior devs" in
   the body text. `prefilter()` in `job_match.py` now checks
   `TITLE_BLOCKERS` against title before `HARD_BLOCKERS` against title+body.
2. **Rebalance `LOCATIONS` away from the US — DONE 2026-09-24.** The user chose
   "lower its share, keep it" over dropping the US outright. Added
   `config.LOCATION_RESULTS_OVERRIDE = {"United States": 5}` (new dict,
   `job_match.py`'s `fetch_jobs()` looks up each location in it and falls
   back to `RESULTS_PER_BOARD`/`--results` otherwise) so US scrapes at 5
   instead of the default 15, without touching the shared setting. Added
   `"United Arab Emirates"` and `"Qatar"` to `LOCATIONS` (both get Indeed
   coverage, unlike `MENA`). **Needs a validation run** to see the new mix's
   apply rate.
3. **Unresolved W2 anomaly — tooling added 2026-09-24, not yet diagnosed.**
   One run-2 posting the LLM described as "Fulltime W2 employment required;
   San Francisco onsite" was *not* caught by the W2 regex. Either the
   posting's literal text differs from the model's paraphrase, or the `W2`
   exception rule (which excuses postings also mentioning
   `c2c|1099|b2b|independent contractor`) misfired. Added
   `config.DUMP_SKIPPED_DESCRIPTIONS` (`False` by default) — when `True`,
   `job_match.py` writes every LLM-verdicted `skip` posting's full
   description (plus its reason/blockers) to
   `output/skipped_descriptions/<row>.md`. Set it to `True` for one run,
   find the W2 posting's file, and check whether "W2" literally appears in
   the text before revising the regex.
4. **Full-time wording — FIXED 2026-09-24, needs a validation run.**
   The old clause was an unverifiable conditional ("open to full-time
   remote employment *if the employer can engage someone outside their
   country*"). Postings never state that, so it behaved as a flat
   rejection of full-time roles — run 2 logged "candidate requires
   contractor/B2B engagements" as a blocker. Replaced with wording that
   makes full-time acceptable by default and treats only explicit local
   employment / US payroll-W2 / on-site requirements as blockers.
   **Not yet validated:** the next run should show full-time remote roles
   no longer rejected on engagement type alone. The cache was cleared for
   this, so the next run re-scores from scratch. A general lesson for this
   file: avoid conditional constraint wording.
5. **Search-term rework — done, no action needed.** `SEARCH_TERMS` is now
   the tighter list ("Next.js developer", "senior Next.js engineer",
   "TypeScript full stack engineer", "Next.js TypeScript Prisma",
   "React Node.js contractor"). Run-2 `stack_fit` held at 75 among
   rejects, so terms are working. Revisit only if `stack_fit` drops.
6. **LinkedIn job-page fields — BUILT 2026-09-25, needs a validation run.**
   Onsite postings kept reaching results (e.g. Living Stones Group, Cairo:
   job 4468599322 — badge "On-site", "Promoted by hirer", but its description
   says "Remote/Hybrid"). `probe_linkedin_guest.py` fetched 9 real public job
   pages (saved in `output/guest_probe/`) and showed the public page has
   applicant count, employment type, seniority, "N days ago" and Easy Apply
   vs company-site apply — but **not** the On-site/Remote/Hybrid badge or
   "Promoted by hirer" (logged-in only). Built: `enrich()` in `sources/linkedin.py` (was `enrich_linkedin()` in
   `job_match.py`) fetches each LinkedIn page itself (JobSpy's LinkedIn
   description fetch is now off; same request count), adds `applicants`,
   `employment_type`, `seniority_level`, `days_old`, `easy_apply`,
   `likely_onsite`; `MAX_APPLICANTS` now actually filters (LinkedIn
   only); `likely_onsite` (city-level location + no explicit fully-remote
   wording) warns the LLM and marks the shortlist entry.
   Decisions (2026-09-25): no logged-in scraping (linkedin-jobs-scraper
   needs your LinkedIn session → account risk); check the badge on the
   shortlist by eye instead. Indeed stays on JobSpy in the same script — the
   Claude Indeed connector was tested and rejected for the pipeline (chat
   only, 10 results/search, no date filter, no stable job IDs).
   **First run (2026-09-25):** 142/142 LinkedIn pages fetched, no
   throttling. Of 166 postings: 111 "not confirmed remote" (real office jobs
   — LinkedIn's Remote filter barely holds for UAE/Qatar/MENA/Worldwide
   searches; `likely_onsite` agreed on nearly all), 32 `> 70 applicants`
   (13 of them 81–180, incl. Proxify contract MERN, Pearson Staff), 9
   regex blockers, 14 scored → 1 apply, 6 maybe (five at 72). The user then set
   `MAX_APPLICANTS = 100`, `MIN_OVERALL_SCORE = 67` (was 75) and added
   `SHORTLIST_VERDICTS = ["apply", "maybe"]` so maybes that clear the score
   are shortlisted and skips never are.
   **Still to validate:** whether shortlisted `likely_onsite` jobs really
   are onsite/hybrid when opened, and whether unflagged ones ever are.

---

## Known limitations

- **LinkedIn rate limiting is the real bottleneck**, not LLM scoring.
  Each LinkedIn posting costs one job-page request (now made by
  `sources/linkedin.py` `enrich()`, `LINKEDIN_DETAIL_DELAY_SECONDS` apart). Scrape volume ≈
  `len(SEARCH_TERMS) × len(LOCATIONS) × boards × RESULTS_PER_BOARD`, so
  adding a location multiplies run time.
- **No workplace badge without logging in.** `likely_onsite` is a heuristic;
  a posting that lists a country-level location but is actually onsite, or
  whose text says "fully remote" while the badge says On-site, gets through.
- **LinkedIn page markup can change.** If a new column is empty for every
  row, update the selectors in `parse_guest_posting()` (re-run
  `probe_linkedin_guest.py` to get fresh sample pages).
- **`history.csv` grows without bound.** Nothing prunes it.
- **No "already seen / already applied" tracking.** Each run is a fresh
  snapshot; `history.csv` is an archive, not a dedupe mechanism against
  past runs. A `seen_jobs.json` + an `--applied` marker was discussed but
  not built.
