# Job Hunter — job_match.py

Pulls fresh postings from LinkedIn, Indeed, jobs.ps and We Work Remotely, scores each one
against your resume with an LLM (routed through OpenRouter), and writes a
sorted, filtered shortlist per site.

```
resume + constraints ──▶ sources/<site>.py scrape ──▶ deterministic pre-filter ──▶
LLM scoring (cached) ──▶ threshold filter ──▶ output/jobs_scored_<site>.csv
                                                     ├──▶ output/shortlist_<site>.md
                                                     ├──▶ output/run_summary.md (all sites)
                                                     └──▶ output/history.csv (all sites)
```

---

## 1. What it does, step by step

1. **Scrapes.** Runs each site in `SOURCES` (or `--sites`) in turn. Each
   site has its own module under `sources/` (`linkedin.py`, `indeed.py`,
   `jobsps.py`, `weworkremotely.py`), imported only when that site is enabled. LinkedIn and
   Indeed are queried per term × location via
   [JobSpy](https://github.com/speedyapply/JobSpy); jobs.ps via its RSS
   feeds; We Work Remotely via its category RSS feeds (full descriptions
   included, so no page fetches). Each site searches its own locations
   (`SITE_LOCATIONS`). The results are merged.
2. **Deduplicates.** Drops repeats by job URL, and again by
   `title + company` (the same posting often appears on both boards).
   Reference codes at the end of a title (`| REF#289637`, `(Req 123)`,
   `Job ID: …`, `#4521`) are ignored for this, since some employers post one
   role per country with a different code each time. A third pass merges
   postings from the same company with an identical description (ignoring
   case and spacing) even when the titles were reworded; descriptions under
   500 characters (e.g. jobs.ps feed summaries) aren't compared, since short
   text can match by coincidence. The kept posting lists
   the other copies' links in an `also_posted` column (and "Also posted as" on
   the shortlist).
3. **Reads each LinkedIn job page.** The script downloads each LinkedIn
   posting's public (logged-out) page itself instead of letting JobSpy do it
   — still one request per posting — and keeps the description plus
   `applicants`, `employment_type` (Contract/Full-time/…), `seniority_level`,
   `days_old` and `easy_apply`. It also sets `likely_onsite` (see §4). Postings
   already blocked by `TITLE_BLOCKERS` are never fetched. Indeed rows keep
   JobSpy's data (`employment_type` and `days_old` come from it; Indeed has no
   applicant count or Easy Apply). The On-site/Remote/Hybrid badge and
   "Promoted by hirer" are **not** on the public page — only when logged in.
4. **Pre-filters, for free.** Before any API call, drops postings that:
   - match `TITLE_BLOCKERS` (junior/intern titles)
   - have no description (e.g. the LinkedIn/jobs.ps page couldn't be fetched)
   - aren't confirmed remote (the `is_remote` backstop, see §4)
   - are saturated: Easy Apply with "Over 200" applicants — only when `DROP_SATURATED_EASY_APPLY` is on, which it isn't by default (LinkedIn only; otherwise they're scored like other crowded postings and need a higher score instead, see `CROWDED_APPLICANTS`)
   - match a pattern in `HARD_BLOCKERS` — onsite/hybrid, W2-only, geo-locked
     remote, clearance, citizens-only, relocation required
   - are older than `MAX_AGE_DAYS`
5. **Scores with an LLM.** Everything that survives gets sent to the model
   with your resume and constraints, and comes back with an `overall` score,
   four sub-scores, a verdict, and named matches/gaps/blockers. Postings
   flagged `likely_onsite` carry a warning in the prompt; the model then caps
   `logistics_fit` at 50 and the verdict at `maybe` unless the text clearly
   says fully remote.
6. **Caches.** Every score is cached by a hash of `(resume text, provider,
   model, job URL, title, company, likely_onsite)`. Re-running the script only pays for postings not
   already in the cache — editing your resume invalidates it on purpose,
   since old scores no longer apply to new resume text.
7. **Filters and sorts.** Sorts everything by `overall` score, descending,
   and writes one pair of files per site searched this run:
   `jobs_scored_<site>.csv` gets every row plus a `passes_threshold` column;
   `shortlist_<site>.md` gets only the rows that clear both `MIN_OVERALL_SCORE`
   and `MIN_SKILL_MATCH_PERCENT` *and* whose verdict is in
   `SHORTLIST_VERDICTS` (apply + maybe; never skip), up to `TOP_N_REPORT`.
8. **Logs history.** Every row from this run is also appended to
   `history.csv` with a `run_timestamp` column. There is one history file
   for all sites (filter on its `site` column). Unlike the per-site files above,
   this one is never overwritten — it accumulates across every run, forever.

---

## 2. One-time install

```bash
cd ~/job-hunter
python3 -m venv .venv
source .venv/bin/activate
pip install -U python-jobspy openai pandas python-dotenv
```

Only if your resume is a PDF or `.docx` file:

```bash
pip install pypdf python-docx
```

## 3. One-time setup

**API key.** Create `.env` in the project folder (copy `.env.example`):

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-haiku-4.5
```

`OPENROUTER_MODEL` in `.env` is optional — if you leave it out, the script
uses `MODEL_NAME` from `config.py` instead. Setting it in `.env` overrides
`config.py` for whichever machine/session that `.env` lives on, which is
handy if you run the same code with different models in different places.

**Scoring provider: OpenRouter vs. Gemini directly.** By default every
posting is scored through OpenRouter (`config.LLM_PROVIDER = "openrouter"`),
which can hit any model it serves — including Gemini
(`MODEL_NAME = "google/gemini-2.5-flash"`) — but every call is billed at
OpenRouter's rate. Setting `LLM_PROVIDER=gemini` in `.env` (or
`config.LLM_PROVIDER = "gemini"`) instead calls Google's Gemini API
directly, which has a free tier for `gemini-2.5-flash` /
`gemini-2.5-flash-lite` — worth using for a high-volume run:

```
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

`GEMINI_MODEL` in `.env` is optional the same way `OPENROUTER_MODEL` is —
falls back to `config.GEMINI_MODEL_NAME`. Only `pip install google-genai`
is needed for this path; the score cache keys on provider + model, so
switching providers never serves a stale score from the other one.

Never commit `.env` — it holds live keys:

```bash
echo ".env" >> .gitignore
```

**Resume.** Put your real resume at `resume.md` (or `.txt`/`.pdf`/`.docx`)
in the project folder. Markdown or plain text extracts cleanest; a PDF with
columns or tables can scramble on extraction, which quietly degrades every
score — if in doubt, paste the PDF's text into `resume.md` once by hand.

**Constraints.** Your hard requirements — location, work authorization,
contract type, timezone, and the role types you don't want. This is what the
model uses to compute `logistics_fit`, and it's the difference between a
shortlist you can act on and one full of roles that would reject you at the
first screening question.

These live in **`CONSTRAINTS` in `config.py`** — that is the single source of
truth. A separate `constraints.txt` is supported via `--constraints` but is
not used by default, and keeping one around risks running against stale text
without noticing. Edit `config.py` instead.

---

## 4. config.py — every variable, explained

Edit this file for anything you expect to reuse across runs. CLI flags
(§5) exist only for one-off overrides of a single run.

### Scraper & search
| Variable | Meaning |
|---|---|
| `SEARCH_TERMS` | List of search queries. Each one is a separate scrape, run in sequence. More terms = more coverage but a longer run. |
| `LOCATIONS` | List of countries or regions, each searched separately for every term. Countries JobSpy knows (e.g. `"United States"`, `"Saudi Arabia"`) search LinkedIn **and** Indeed. Anything else — regions like `"MENA"`, `"Worldwide"`, or countries Indeed doesn't cover like `"Palestine"` — automatically searches LinkedIn only, with a one-line note in the run output. Use `"MENA"`, not `"Middle East and North Africa"`: LinkedIn returns nothing for the full phrase. |
| `IS_REMOTE` | Whether to ask each board to filter to remote-only postings. **Not a guarantee** — LinkedIn's own remote filter (`f_WT=2`) has been observed letting onsite postings through, especially promoted/sponsored ones. When `IS_REMOTE`/`--remote` is on, the pre-filter backstops this using JobSpy's own per-posting `is_remote` field (keyword/attribute detection from the actual scraped text) — see `TITLE_BLOCKERS` below. |
| `SITE_REMOTE_ONLY` | Per-site override of `IS_REMOTE`, e.g. `{"jobsps": False}`. For a site set to `False`, on-site/hybrid postings are allowed: the site isn't asked for remote-only results, the "not confirmed remote" pre-filter is off, and `WORKPLACE_BLOCKER_LABELS` patterns are skipped. `CONSTRAINTS` has a matching line saying on-site roles in the Gaza Strip are acceptable, so the LLM doesn't mark them down either. |
| `SITE_EXTRA_CONSTRAINTS` | Per-site text appended to `CONSTRAINTS` when that site's postings are scored, e.g. `{"jobsps": "..."}` to also target non-developer roles on a local board only. Keep the real text in `config_local.py`. Constraints are part of the score cache key, so editing them re-scores the affected postings. |
| `SOURCES` | Which sites to search: any of `"linkedin"`, `"indeed"`, `"jobsps"`, `"weworkremotely"` (one module each under `sources/`). `--sites` overrides it for one run. To add a site, write `sources/<name>.py` with a `fetch(args) -> DataFrame` function and register it in `sources/__init__.py`'s `REGISTRY`. |
| `HOURS_OLD` | JobSpy-side filter: only fetch postings from the last N hours. This is the main age control. |
| `RESULTS_PER_BOARD` | How many results to request per board, per search term, per location. Total postings before dedupe ≈ `len(SEARCH_TERMS) × len(LOCATIONS) × boards searched × RESULTS_PER_BOARD`. Scrape time grows the same way, so when you add a location, consider trimming terms or lowering this. |
| `SITE_LOCATIONS` | Locations per site: `{"linkedin": [...], "indeed": [...], "jobsps": [...]}`. A site not listed uses `LOCATIONS`. LinkedIn/Indeed take country/region names as above. **jobs.ps** takes location slugs, the last part of the location page URL (`https://www.jobs.ps/locations/gaza-jobs` → `"gaza-jobs"`; also `ramallah-jobs`, `nablus-jobs`, `hebron-jobs`, `jerusalem-jobs`). Note that `gaza-jobs` covers the whole Gaza Strip (قطاع غزة). jobs.ps reads those location feeds, then keeps only postings whose category is in `JOBSPS_CATEGORIES` (empty list = every category). An empty jobs.ps location list reads the `JOBSPS_CATEGORIES` feeds directly instead, across all locations. Each feed only lists its newest ~15 postings (about 4 days of Gaza postings at current volume) and can't be paged, so run at least that often. If none of a feed's postings were in it at the previous run, the run prints an **overflowed** warning (also shown in the shortlist's run metrics): postings in that gap were probably missed, so check the location page on jobs.ps by hand. Last-seen postings per feed are kept in `.jobcache/jobsps_feeds.json`. |
| `WWR_CATEGORIES` | We Work Remotely category feeds to read (slug from `https://weworkremotely.com/categories/<slug>`). Default: full-stack and back-end programming. For this site, `SITE_LOCATIONS["weworkremotely"]` filters on the posting's region (default `["Anywhere in the World"]`, which drops "North America Only" etc.), and postings older than `HOURS_OLD` are dropped before anything else. |
| `JOBSPS_CATEGORIES` | jobs.ps category slugs (`https://www.jobs.ps/categories`). With jobs.ps locations set, a filter on each posting's category; without them, the feeds to read. **Feed fallback:** jobs.ps job pages are behind a Cloudflare browser challenge (403 to scripts) while the RSS feeds stay open. A posting whose page can't be fetched is still scored from the feed's title and ~300-character snippet: `feed_only` is `True` in the CSV, and the shortlist marks it **Feed summary only**. Its category is unknown, so this filter can't drop it. After `JOBSPS_DETAIL_MAX_FAILURES` failures in a row, the remaining pages aren't tried. |
| `LOCATION_RESULTS_OVERRIDE` | Dict of `{location: results}` overriding `RESULTS_PER_BOARD` for specific entries in `LOCATIONS` — e.g. `{"United States": 5}` to scrape a low-apply-rate location at reduced volume without touching the shared default. Locations not listed here just use `RESULTS_PER_BOARD` (or `--results`). |

### Filtering thresholds
| Variable | Meaning |
|---|---|
| `MAX_AGE_DAYS` | Deterministic, runs before scoring. A backstop on top of `HOURS_OLD` for rows with a stale `date_posted` that slip through. |
| `CROWDED_APPLICANTS` / `CROWDED_MIN_OVERALL_SCORE` | A LinkedIn posting with **more** than `CROWDED_APPLICANTS` applicants (100) is "crowded": it is still scored, but needs `overall >= CROWDED_MIN_OVERALL_SCORE` (72) instead of `MIN_OVERALL_SCORE` to be shortlisted, and is marked **Crowded** there. Counts come from LinkedIn's public job page: "91 applicants" → 91, "Over 200 applicants" → 200, "Be among the first 25 applicants" → 0. They count clicks on Apply, not finished applications, and the public page can show a higher bucket than the logged-in view ("Over 200" vs "Over 100"), so crowded postings aren't dropped outright. Indeed publishes no count. |
| `DROP_SATURATED_EASY_APPLY` / `SATURATED_APPLICANTS` | Off (`False`) by default: it dropped good matches unscored, so saturated postings are scored as crowded instead and marked **Easy Apply** in the shortlist. Easy Apply is read from the Apply button's tracking name: `apply-link-onsite` or the newer `apply-link-simple_onsite` (Easy Apply), `apply-link-offsite` (company site). `True` drops **Easy Apply** postings with at least `SATURATED_APPLICANTS` (200, i.e. "Over 200") applicants before scoring. On Easy Apply the count is mostly real one-click applications, so these are genuinely flooded; on "Apply on company site" the count is clicks, so those are scored as crowded instead. The public page tops out at "Over 200", so values above 200 never match. Dropped postings are listed under "Dropped before scoring" in the shortlist. |
| `LINKEDIN_DETAIL_DELAY_SECONDS` | Pause between LinkedIn job-page requests (plus a little random jitter). Adds roughly this many seconds per LinkedIn posting to a run. |
| `LINKEDIN_DETAIL_MAX_FAILURES` | Stop fetching LinkedIn pages after this many failures in a row (LinkedIn is throttling). Unfetched postings are skipped as `no description` rather than scored blind. |
| *(not a config variable)* `likely_onsite` | A **flag, not a filter.** Set for LinkedIn postings whose location is city-level ("Cairo, Egypt", "San Francisco, CA") *and* whose text has no explicit fully-remote statement ("fully remote", "remote-first", "work from anywhere", …). "Remote/Hybrid" or "remote or on-site" doesn't count. Stand-in for the On-site/Hybrid badge, which is only visible when logged in. Flagged postings get a warning in the LLM prompt and a **Check workplace badge** line in `shortlist_linkedin.md`. |
| `HARD_BLOCKERS` | Regex patterns that disqualify a posting **before** any LLM call, so they cost nothing. Each entry is `(label, regex)` or `(label, regex, exception_regex)`; matching is case-insensitive against title + description, and an entry with an `exception_regex` that also matches does **not** block. The `label` is what appears in the `blockers` column. Because a blocked posting gets no LLM judgment at all, a false positive here is a job you never see — so keep patterns specific, and use `PREFILTER_DRY_RUN` to check new ones. |
| `TITLE_BLOCKERS` | Same shape as `HARD_BLOCKERS`, but matched against the **title only**, never the description — for words like "junior" or "graduate" that are common (and fine) inside a job's body text but a strong signal when they're in the title itself. |
| `WORKPLACE_BLOCKER_LABELS` | The `HARD_BLOCKERS` labels that are about workplace (onsite/hybrid/return to office). Skipped for sites where `SITE_REMOTE_ONLY` is `False`; every other blocker still applies there. |
| *(not a config variable)* `is_remote` backstop | When `IS_REMOTE`/`--remote` is on, any posting whose `is_remote` field is explicitly `False` is blocked as `"not confirmed remote"`. For LinkedIn the script computes it from title + description + location using JobSpy's keyword list ("remote", "work from home", "wfh"); for Indeed it's JobSpy's own value. Catches onsite postings that slipped past LinkedIn's/Indeed's own remote filter (seen in practice with LinkedIn's "Promoted by hirer" listings). A missing/unknown value (JobSpy couldn't tell either way) is never blocked on this alone. |
| `PREFILTER_DRY_RUN` | `True` scores every posting as normal but records what the pre-filter *would* have blocked in a new `prefilter_flag` column, so you can compare the regex verdict against the LLM's on the same run. Costs full price — use it for a run or two after editing `HARD_BLOCKERS`, then set back to `False`. |
| `MIN_OVERALL_SCORE` | Postings below this `overall` score (0–100, from the LLM) are excluded from the shortlists. Still appear in the CSV. Currently 67 — low enough that solid "maybe" postings (typically ~72) make the shortlist. |
| `SHORTLIST_VERDICTS` | Which LLM verdicts can appear in the shortlists: `["apply", "maybe"]`. A "skip" is never shortlisted even if its score clears the bar. |
| `MIN_SKILL_MATCH_PERCENT` | Same, applied to the LLM's `stack_fit` sub-score. |
| `SITE_MIN_OVERALL_SCORE` / `SITE_MIN_SKILL_MATCH_PERCENT` | Per-site overrides of the two thresholds above, e.g. `{"jobsps": 0}`. Sites not listed use the global value. Each shortlist and the run summary print the thresholds actually used for that site. |
| `DUMP_SKIPPED_DESCRIPTIONS` | `True` writes every LLM-verdicted `skip` posting's full description to `output/skipped_descriptions/<row>.md`, alongside the LLM's reason/blockers — useful for checking a blocker regex against the real posting text instead of the LLM's paraphrase. `False` by default (one file per skip). |

### LLM engine
| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `"openrouter"` (default) or `"gemini"` — which API scores postings. See "Scoring provider" in §3 above for the cost/setup trade-off. Overridden by `LLM_PROVIDER` in `.env` if set there. |
| `MODEL_NAME` | Used when `LLM_PROVIDER == "openrouter"`. Any model slug OpenRouter serves — check [openrouter.ai/models](https://openrouter.ai/models) for current, exact names before switching, since these strings change and a stale one fails the call. Overridden by `OPENROUTER_MODEL` in `.env` if set there. |
| `GEMINI_MODEL_NAME` | Used when `LLM_PROVIDER == "gemini"`. Any slug Google's API serves (e.g. `gemini-2.5-flash`). Overridden by `GEMINI_MODEL` in `.env` if set there. |
| `MODEL_TEMPERATURE` | Lower = more consistent scoring across similar postings. `0.1` is a reasonable default for a scoring task; there's little reason to raise it. Applies to both providers. |
| `MAX_RETRIES` | How many times to retry a failed API call (rate limits, transient errors) before giving up on that posting. |
| `RETRY_DELAY_SECONDS` | Base delay for exponential backoff between retries — actual delays are `RETRY_DELAY_SECONDS × 2^attempt` (e.g. 3s, 6s, 12s). |

### Candidate profile
| Variable | Meaning |
|---|---|
| `config_local.py` | Not a variable: a git-ignored file loaded at the end of `config.py`. Anything defined there overrides `config.py`, so keep your real `CANDIDATE_PROFILE` and `CONSTRAINTS` in it and they're never committed. `config.py` itself only has EXAMPLE values (a run warns if it's using them). |
| `CANDIDATE_PROFILE` | **Fallback only**, used when `--resume` isn't passed. This is a short summary, not a substitute for a real resume — prefer `resume.md` for anything but a quick test; scores are noticeably blunter without the full resume. |
| `CONSTRAINTS` | **The live constraints** — used on every run unless you pass `--constraints PATH`. Edit this, not a separate file. Drives `logistics_fit` and the `blockers` list. |

### Output & run behavior
| Variable | Meaning |
|---|---|
| `OUTPUT_DIR` | Where `jobs_scored_<site>.csv`, `shortlist_<site>.md` and `history.csv` are written. |
| `CACHE_PATH` | Where the score cache lives. Safe to delete if you want to force a full re-score. |
| `TOP_N_REPORT` | How many postings (that clear the thresholds) go into each `shortlist_<site>.md`. |
| `WORKERS` | How many scoring calls run in parallel. Higher is faster but more likely to hit rate limits — 4–8 is a reasonable range. |
| `SEARCH_DELAY_SECONDS` | Pause between search terms during scraping, to reduce 429s from the job boards (this is separate from LLM retries). |

### History log
| Variable | Meaning |
|---|---|
| `ENABLE_HISTORY_LOG` | Whether to append every run's results to `history.csv`. Set `False` to disable if you don't want it. |
| `HISTORY_FILENAME` | Filename for the history log, written inside `OUTPUT_DIR`. |

---

## 5. Command-line usage

Activate the venv first, in every new terminal session — every command
below assumes it's already active:

```bash
cd ~/job-hunter
source .venv/bin/activate
```

Run with no flags at all and every value comes from `config.py`:

```bash
python job_match.py
```

Every flag below overrides its matching `config.py` value for that run only
— nothing in `config.py` is changed by passing a flag.

| Flag | Overrides | Default (from config.py) |
|---|---|---|
| `--resume PATH` | resume file (`.md`/`.txt`/`.pdf`/`.docx`) | none → falls back to `CANDIDATE_PROFILE` |
| `--constraints PATH` | constraints file, for a one-off override | none → uses `config.CONSTRAINTS` |
| `--terms "T1" "T2" ...` | `SEARCH_TERMS` | `config.SEARCH_TERMS` |
| `--locations "L1" "L2" ...` | `SITE_LOCATIONS`, for every site in the run — pair with `--sites` | each site's `SITE_LOCATIONS` entry, else `LOCATIONS` |
| `--remote` / `--no-remote` | `SITE_REMOTE_ONLY` / `IS_REMOTE`, for every site in the run | each site's `SITE_REMOTE_ONLY` entry, else `config.IS_REMOTE` |
| `--hours N` | `HOURS_OLD` | `config.HOURS_OLD` |
| `--results N` | `RESULTS_PER_BOARD` | `config.RESULTS_PER_BOARD` |
| `--top N` | `TOP_N_REPORT` | `config.TOP_N_REPORT` |
| `--workers N` | `WORKERS` | `config.WORKERS` |
| `--delay SECONDS` | `SEARCH_DELAY_SECONDS` | `config.SEARCH_DELAY_SECONDS` |
| `--sites linkedin indeed jobsps` | `SOURCES` | `config.SOURCES` |
| `--proxies user:pass@host:port ...` | (no config equivalent) | none |
| `--provider openrouter\|gemini` | `LLM_PROVIDER` | `.env`'s `LLM_PROVIDER`, else `config.LLM_PROVIDER` |

Examples (venv still active from above):

```bash
# quick smoke test, few results, explicit resume
python job_match.py --resume resume.md --results 5

# one-off search for a different role, everything else from config.py
python job_match.py --terms "backend engineer" "node.js developer" --results 20

# search only the MENA region, one term, small test
python job_match.py --sites linkedin --locations MENA --terms "next.js developer" --results 5

# jobs.ps in Ramallah instead of the configured Gaza feed
python job_match.py --sites jobsps --locations ramallah-jobs

# search only jobs.ps this run (LinkedIn/Indeed output files are left as they were)
python job_match.py --sites jobsps

# LinkedIn + Indeed, no jobs.ps
python job_match.py --sites linkedin indeed

# force a fresh score on everything (bypass cache) by clearing it first
rm .jobcache/scores.json
python job_match.py

# score this run with Gemini directly instead of OpenRouter — needs
# GEMINI_API_KEY in .env either way; doesn't touch config.py/.env's default
python job_match.py --provider gemini
```

Note: `MAX_AGE_DAYS`, `CROWDED_APPLICANTS`, `MIN_OVERALL_SCORE`,
`MIN_SKILL_MATCH_PERCENT`, `MODEL_NAME`, `MODEL_TEMPERATURE`, `MAX_RETRIES`,
`RETRY_DELAY_SECONDS`, `OUTPUT_DIR`, and `CACHE_PATH`
have **no CLI flag** — edit `config.py` directly to change these. They're
either not something you'd want to flip per-run, or (for the paths) would
be confusing to change mid-experiment.

---

## 6. Output files

Each site searched in a run gets its own `jobs_scored_<site>.csv` and
`shortlist_<site>.md` (`<site>` is `linkedin`, `indeed`, `jobsps` or `weworkremotely`). A run
only rewrites the files for the sites it searched: `--sites jobsps` leaves
`shortlist_linkedin.md` and `shortlist_indeed.md` alone. A site that ran but
found nothing still gets its files rewritten (empty), so they never show an
older run's postings as current. Postings with the same title and company
on two sites are merged only when both sites run together.

**`output/jobs_scored_<site>.csv`** — every unique posting that site
returned this run, one row each, sorted by `overall` descending. Pre-filtered postings appear here too,
as `verdict: skip` with `overall: 0` and the blocker label in `blockers`.
Columns include `overall`, `passes_threshold` (bool), `verdict`
(`apply`/`maybe`/`skip`), `title`, `company`, `site`, `location`,
`search_location` (which entry in `LOCATIONS` found it), `employment_type`,
`seniority_level`, `date_posted`, `days_old`, `applicants` (number) and
`applicants_text` (LinkedIn's wording), `easy_apply`, `is_remote`,
`likely_onsite`, `reason`, `blockers`, `matched`, `gaps`, the four sub-scores,
and `job_url`. A `prefilter_flag` column is added only when
`PREFILTER_DRY_RUN` is on.

**`output/shortlist_<site>.md`** — only that site's rows where `passes_threshold` is true, up
to `TOP_N_REPORT` of them, formatted as a readable report with the reason,
matched skills, gaps, and any blockers for each. Each entry's first line
reads like `linkedin · Cairo, Egypt · 2 days old · 45 applicants · Contract
· Easy Apply`, and `likely_onsite` postings get a **Check workplace badge**
line — open those and look at the On-site/Remote/Hybrid badge before applying.
After the shortlist comes **Rated apply/maybe but under a threshold**: postings
the LLM liked that a score threshold held back (e.g. a crowded posting under
`CROWDED_MIN_OVERALL_SCORE`), each with the threshold it missed.
Then **Dropped before scoring**: every posting the free
pre-filter removed, grouped by reason, with title, company and link, so a
wrong drop is quick to spot. "not confirmed remote" and "older than N days"
are nearly always right and very noisy, so they show only a count (the rows
are in the CSV).
The file ends with a **Run metrics** section for that site only: a short
paragraph describing the run, a **Coverage** line for LinkedIn/Indeed (see
below), then a table of each step (scrape, dedupe,
pre-filter, LLM scoring, verdict filter, score thresholds, shortlist) with how many of
the site's postings went in and came out, plus its time and details — searches
run, pages fetched/failed, how many were served from cache vs sent to the LLM,
and the apply/maybe/skip split. Scoring runs once for all sites together, so
its time is the whole run's, as is the total time in the last row. The terminal prints each step's time as a
`[time]` line as it finishes.

**Coverage** (LinkedIn/Indeed): each search fetches at most
`RESULTS_PER_BOARD` postings (or its `LOCATION_RESULTS_OVERRIDE`). A search
that comes back full was probably cut off, so more postings exist that this
run never saw. These searches, and any that failed outright, are listed in a
`[warn]` line in the terminal, in each shortlist's run metrics and in
`run_summary.md`. If the same searches keep hitting the cap, raise the cap for
them.

**`output/run_summary.md`** — rewritten every run: a short paragraph on the
whole run, then one row per site searched
(scraped, unique, pre-filtered, scored, from cache, LLM calls, apply, maybe,
skip, failed, apply/maybe, passed thresholds, shortlisted) with an all-boards total, then
the time of every step in the run. A **Coverage** section above the table
lists capped or failed searches per site, when there are any.

### Checking for missed jobs — `check_missed.py`

The real test of whether the script loses jobs: browse the boards yourself as
usual, collect links to postings you'd apply to, and check them:

```bash
python check_missed.py https://www.linkedin.com/jobs/view/4312345678 https://www.jobs.ps/jobs/...
python check_missed.py < links.txt    # one link per line
```

For each link it reads `output/history.csv` and says one of:
**SHORTLISTED**; **SCORED, NOT SHORTLISTED** (with score, verdict and the
LLM's reason); **DROPPED BEFORE SCORING** (with the pre-filter's reason); or
**NEVER FOUND** (no search returned it: add a matching search term or
location, or raise the cap). LinkedIn and Indeed links match by job id, so any link form works:
LinkedIn `?currentJobId=` from search or recommendation pages, Indeed
`?vjk=` from search results, any Indeed country site. jobs.ps and We Work
Remotely match by URL. No
network calls; history must be on (`ENABLE_HISTORY_LOG`).

**`output/history.csv`** — append-only log across every run, with a
`run_timestamp` column added as the first column, shared by all sites
(filter on `site`). The per-site CSV and shortlist files are rewritten each
time their site is searched; this file
never is — it just grows. A posting that appears in three separate runs
shows up as three rows here, once per `run_timestamp`, so you can trace
a specific posting's score across time or filter to a specific run.
Grows without bound; nothing prunes it automatically. Disable by setting
`ENABLE_HISTORY_LOG = False` in `config.py`.

**`.jobcache/scores.json`** — the score cache. Delete it to force a full
re-score; otherwise leave it alone.

---

## 7. Viewing the output files

### `shortlist_<site>.md` — via `glow`

[`glow`](https://github.com/charmbracelet/glow) is a terminal Markdown
renderer — headers, bold, and tables all render properly instead of showing
raw `#` and `**` characters, which makes the shortlists much easier to
actually read.

`glow` is a standalone system binary (installed via `apt`, not `pip`), so
it does **not** live inside `.venv` and doesn't need `source
.venv/bin/activate` first — unlike `python job_match.py`, which does. You
can run `glow` from any terminal, venv active or not.

**Install (Debian/Ubuntu):**

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.charm.sh/apt/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" | sudo tee /etc/apt/sources.list.d/charm.list
sudo apt update && sudo apt install glow
```

**View the shortlist:**

```bash
glow output/shortlist_linkedin.md   # or _indeed / _jobsps
```

Unlike a report script that names each output file with a timestamp,
each `shortlist_<site>.md` is a fixed filename that gets overwritten every run —
so there's no "find the latest file" step needed; this command always shows
the current shortlist.

**Paginated view**, if the shortlist is long enough to scroll off-screen:

```bash
glow -p output/shortlist_linkedin.md
```

Press `q` to exit.

**Browse mode** — run `glow` with a directory instead of a file and it opens
a TUI file picker scoped to that folder, useful once `output/` also has
`README.md`-style clutter you want to skip past:

```bash
glow output/
```

If you'd rather not install anything, `cat output/shortlist_<site>.md` or opening
it in any Markdown-aware editor works too — `glow` is a convenience, not a
requirement.

### `jobs_scored_<site>.csv` and `history.csv` — via `column` or VisiData

`glow` doesn't help here — these are CSVs, not Markdown. Dumped straight to
a terminal with `cat`, the columns don't line up and long text fields
(`reason`, `blockers`, `gaps`) make every row wrap unreadably. Two options,
depending on how much you want to interact with the data:

**Quick look, no install** — `column` and `less` ship with Ubuntu already:

```bash
column -s, -t output/jobs_scored_linkedin.csv | less -S
```

`-S` in `less` disables line-wrapping so wide rows scroll sideways with the
arrow keys instead of wrapping into a mess. Press `q` to exit. This is a
static view — fine for a quick check, but there's no sorting or filtering.

**Interactive exploration** — [VisiData](https://www.visidata.org/) (`vd`)
opens a CSV as a full terminal spreadsheet: sort any column, filter rows,
search, and it handles quoted fields with embedded commas (which `column`
does not) correctly. Since it's a Python package, install it into the
project's venv alongside everything else:

```bash
source .venv/bin/activate
pip install visidata
```

Then, with the venv active:

```bash
vd output/jobs_scored_linkedin.csv
```

Useful commands once inside: `` ` `` (backtick) sorts by the current
column, `` \ `` filters rows matching a value in the current column, arrow
keys move around, and `q` quits. `Ctrl+H` opens the full command reference.

`history.csv` opens the same way — `vd output/history.csv` — and since it
has a `run_timestamp` column, filtering on that is the fastest way to look
at just one day's run instead of the whole accumulated log.

---

## 8. Troubleshooting

**`ModuleNotFoundError: No module named 'X'`**
The venv isn't active, or the package installed into a different Python.
Check `which python` — it should point inside `.venv/bin/python`. If not,
run `source .venv/bin/activate` and re-install.

**`OPENROUTER_API_KEY is not set`**
Either `.env` is missing/misplaced (it must be in the same folder you run
the script from), or the key line inside it is malformed. It should read
`OPENROUTER_API_KEY=sk-or-...` — no quotes, no `export`.

**LinkedIn requests failing / lots of 429s**
LinkedIn rate-limits hard; each LinkedIn posting costs one job-page request
on top of the search. If the run prints `failures in a row — LinkedIn is
likely throttling` or many rows come back `no description (LinkedIn page
fetch failed)`, raise `LINKEDIN_DETAIL_DELAY_SECONDS`, lower
`RESULTS_PER_BOARD`, raise `SEARCH_DELAY_SECONDS`, or pass `--proxies`.
Indeed is much more tolerant.

**A LinkedIn field is suddenly empty for every row** (`applicants`,
`employment_type`, `easy_apply`, …)
LinkedIn changed its public page markup. The selectors are in
`parse_guest_posting()` in `sources/linkedin.py`. Re-run `probe_linkedin_guest.py`
to save fresh pages to `output/guest_probe/` and compare.

**Every posting comes back `verdict: error` with `--provider gemini`**
Check `output/jobs_scored_<site>.csv`'s `reason` column — a `429 RESOURCE_EXHAUSTED`
means Gemini's free tier, not a bug. Google's per-account free-tier limits
for `gemini-2.5-flash` have been documented as low as 5 requests/minute and
20/day on some projects (well below the ~10 RPM/250 RPD Google's docs once
listed) — a single run of 50+ postings can exceed the *daily* cap outright,
which no amount of retrying fixes until it resets. The pipeline already
serializes Gemini calls (`config.GEMINI_WORKERS = 1`) and waits exactly as
long as each 429 response says to, so it won't make the problem worse, but
it can't lift a account-level daily quota. Options: try
`GEMINI_MODEL=gemini-2.5-flash-lite` in `.env` (a different per-model quota,
sometimes much higher); check your actual limits at
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit);
enable billing on the linked Google Cloud project for higher limits; or run
with `--provider openrouter` (or no flag, if that's your `.env` default) for
full-size runs and save Gemini for small ones.

**"No jobs returned"**
Usually `HOURS_OLD` is too narrow for the term, or the term itself is too
niche for that location. Widen `HOURS_OLD`, broaden the term, or drop
`--remote` to test.

**`AttributeError` mentioning `LinkedIn` or `_get_location` at startup**
`job_match.py` patches one JobSpy function so that LinkedIn results from
countries JobSpy doesn't recognize (Algeria, Jordan, Palestine, and
others) don't crash the whole search. A JobSpy upgrade
(`pip install -U python-jobspy`) could rename that function. If this error
appears after an upgrade, the patch needs adjusting to the new JobSpy code;
until then, `pip install "python-jobspy==<previous version>"` restores
the working setup.

**Scores look off / everything scores low**
Check `config.CONSTRAINTS` first — vague constraints give the model too
little to judge `logistics_fit` against, and it defaults to conservative.
Watch for **conditional** wording too: a clause like "open to X *if* the
employer can do Y" fails whenever a posting doesn't state Y, which most
don't — so it behaves as a flat rejection of X. State what's acceptable
by default and let the blockers be the exceptions. Also confirm `resume.md`
extracted cleanly (open it and read it) rather than the `CANDIDATE_PROFILE`
fallback being used by accident (a `[warn]` line prints on the fallback).

---

## 9. Final folder layout

```
~/job-hunter/
├── .venv/
├── .env                    ← your real key, git-ignored
├── .env.example             ← template, safe to share/commit
├── .jobcache/scores.json    ← score cache, safe to delete
├── output/
│   ├── jobs_scored_<site>.csv   ← one per site (linkedin / indeed / jobsps / weworkremotely)
│   ├── shortlist_<site>.md
│   └── history.csv           ← append-only, all sites, every run, never overwritten
├── job_match.py             ← CLI, pre-filter, scoring, output
├── check_missed.py          ← "did the script see this job?" for links you found yourself
├── sources/                 ← one module per job site
│   ├── __init__.py          ← REGISTRY of site name -> module
│   ├── common.py            ← shared helpers (retrying fetch, title blockers, ...)
│   ├── _jobspy.py           ← JobSpy search loop shared by linkedin/indeed
│   ├── linkedin.py
│   ├── indeed.py
│   ├── jobsps.py
│   └── weworkremotely.py
├── config.py                ← edit this for day-to-day changes
├── config_local.py          ← your real profile + constraints, git-ignored
├── resume.md
├── (constraints live in config.py — no constraints.txt needed)
└── README.md
```
