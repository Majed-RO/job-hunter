"""
Configuration settings for the Job Hunter pipeline.
Modify values here to customize search scope, LLM thresholds, and target criteria.

Every CLI flag in job_match.py falls back to a value here, so you can just edit
this file and run `python job_match.py` with no flags at all. Passing a flag
(e.g. --results 10) overrides the matching value below for that run only.
"""

# ==========================================
# 1. SCRAPER & SEARCH PARAMETERS
# ==========================================
SEARCH_TERMS = [
    "Next.js developer",
    "senior Next.js engineer",
    "TypeScript full stack engineer",
    "Next.js TypeScript Prisma",
    "React Node.js contractor",
]

# COUNTRY_INDEED = "USA"

# Each location is searched separately, for every term in SEARCH_TERMS.
# - Countries JobSpy knows (e.g. "United States", "Saudi Arabia") search
#   LinkedIn AND Indeed.
# - Anything else — regions like "MENA", or countries Indeed doesn't cover
#   like "Palestine" — searches LinkedIn only. This is automatic; no extra
#   setting needed. A one-line note prints for these during the run.
# Use "MENA", not "Middle East and North Africa" — LinkedIn returns nothing
# for the full phrase.
LOCATIONS = [
    "United States",
    "Worldwide",
    "United Arab Emirates",
    "Qatar",
    "Saudi Arabia"
]

# Locations to search, per site. A site not listed here uses LOCATIONS above.
# --locations overrides this for every site in a run, so pair it with --sites
# (e.g. `--sites jobsps --locations ramallah-jobs`).
# - linkedin / indeed: country or region names, as described for LOCATIONS.
#   Indeed skips anything that isn't a country it supports (MENA, Worldwide).
# - jobsps: jobs.ps location slugs, the last part of the location page URL
#   (https://www.jobs.ps/locations/gaza-jobs -> "gaza-jobs"). Others:
#   ramallah-jobs, nablus-jobs, hebron-jobs, jerusalem-jobs. "gaza-jobs" is
#   the whole Gaza Strip (قطاع غزة). Postings from these feeds are then kept
#   only if their category is in JOBSPS_CATEGORIES. An empty list here reads
#   the JOBSPS_CATEGORIES feeds directly instead, from all locations.
SITE_LOCATIONS = {
    "linkedin": LOCATIONS,
    "indeed": LOCATIONS,
    "jobsps": ["gaza-jobs"],
    # We Work Remotely: kept when the posting's region contains one of these
    # (case-insensitive). Other regions seen: "North America Only",
    # "Europe Only", "Americas Only". Empty list = every region.
    "weworkremotely": ["Anywhere in the World"],
}

# Per-location override of RESULTS_PER_BOARD. Run 2 showed United States at
# 97 postings -> 3 applies (3%) vs MENA's 29 -> 3 (10%), so US is kept in the
# mix (still finds applies) but scraped at a smaller volume than everything
# else, freeing budget for UAE/Qatar (which, unlike MENA, get Indeed
# coverage too). Locations not listed here use RESULTS_PER_BOARD as-is.
LOCATION_RESULTS_OVERRIDE = {
    "United States": 5,
}

IS_REMOTE = True

# Per-site override of IS_REMOTE. False = on-site/hybrid postings are allowed
# for that site: it isn't asked for remote-only results, the "not confirmed
# remote" pre-filter doesn't apply, and neither do the WORKPLACE_BLOCKER_LABELS
# patterns below. --remote/--no-remote overrides this for every site in a run.
# jobs.ps is searched in Gaza (SITE_LOCATIONS), where on-site work is fine.
SITE_REMOTE_ONLY = {
    "jobsps": False,
}

# Per-site text appended to CONSTRAINTS when scoring that site's postings, e.g.
# to widen the target roles on a local board only. Changing it re-scores that
# site's postings (constraints are part of the score cache key). EXAMPLE:
#   SITE_EXTRA_CONSTRAINTS = {"jobsps": "On this board the candidate is ALSO "
#                             "targeting <other role types>."}
# Put the real text in config_local.py.
SITE_EXTRA_CONSTRAINTS: dict[str, str] = {}

# Which job sites to search. Valid names: "linkedin", "indeed", "jobsps", "weworkremotely"
# (one module each under sources/). --sites overrides this for one run, e.g.
# `python job_match.py --sites jobsps`. Each site gets its own
# output/shortlist_<site>.md and jobs_scored_<site>.csv; history.csv is shared.
SOURCES = ["linkedin", "indeed", "jobsps", "weworkremotely"]

# --- jobs.ps (Palestine local job board) ---
# Settings for the "jobsps" source above. jobs.ps has no search
# API and isn't JobSpy-supported, so it's pulled from its public RSS feeds
# (one per category) plus one page fetch per posting for what the feed
# doesn't carry: full description, workplace type (office/remote/field), and
# deadline. Full list of category slugs: https://www.jobs.ps/categories
# (the URL's last path segment, e.g. ".../categories/it-jobs" -> "it-jobs").
# With SITE_LOCATIONS["jobsps"] set, this is a filter on each posting's
# category; empty list = keep every category in those locations.

# Jobs.ps full list of categories (slugs) as of 2026-09-24:
""" الذكاء الاصطناعي وعلم البيانات : AI-Big-Data-jobs
التنمية الاقتصادية والتعاون الدولي : development-economic-jobs
المنح والتمويل : Grants-and-Funding-jobs
التسويق الرقمي: تحسين محركات البحث، البريد الإلكتروني، المحتوى : Digital-Marketing-jobs
الإدارة والأعمال : business-administration-jobs
الثقافة والفنون : culture-arts-jobs
التعليم والتدريب : education-training-jobs
الهندسة : engineering-jobs
المحاسبة، الإقتصاد والعلوم المالية : accounting-finance-jobs
اللغات والترجمة : languages-and-translation-jobs
التصميم الجرافيكي والحركي : graphic-design-jobs
الطب، التمريض، الصيدلة، والصحة العامة : healthcare-jobs
الفندقة والسياحة : hospitality-tourism-jobs
تكنولوجيا المعلومات وهندسة البرمجيات : it-jobs
القانون والمحاماة : legal-jobs
العمليات والدعم اللوجستي : operations-jobs
الصحافة والإعلام : press-media-jobs
العلاقات العامة : public-relation-jobs
التسويق والمبيعات : sales-marketing-jobs
العلوم الاجتماعية والدراسات المجتمعية : social-science-jobs
الموارد البشرية : human-resources-jobs
خدمة العملاء والدعم الفني : customer-service-and-support-jobs
الإنشاءات والعقارات : construction-and-real-estate-jobs
العلوم والبحوث : science-and-research-jobs
المشتريات والتوريد : procurement-and-purchasing-jobs
التصنيع والإنتاج : manufacturing-and-production-jobs
النقل والخدمات اللوجستية : transportation-and-logistics-jobs
الزراعة والبيئة : agriculture-and-environment-jobs
الأمن والسلامة : security-and-safety-jobs
التنظيف والصيانة : cleaning-and-maintenance-jobs
حقوق الإنسان والمساواة بين الجنسين والحماية : human-rights-gender-protection-jobs
مجالات متنوعة : others-jobs """

JOBSPS_CATEGORIES = [
  "it-jobs",
  "AI-Big-Data-jobs",
  "Digital-Marketing-jobs",
  "business-administration-jobs",
  "engineering-jobs",
  "customer-service-and-support-jobs",
  "science-and-research-jobs",
  "operations-jobs",
  "development-economic-jobs",
  "education-training-jobs",
]
JOBSPS_MAX_PER_FEED = 30         # cap on each RSS feed read, belt-and-suspenders
JOBSPS_DETAIL_DELAY_SECONDS = 1.0
JOBSPS_DETAIL_MAX_FAILURES = 5

# --- We Work Remotely (remote-only board) ---
# RSS category feeds to read: the slug in the category page URL,
# https://weworkremotely.com/categories/<slug>. Others:
# "remote-front-end-programming-jobs", "remote-devops-sysadmin-jobs".
# Each feed already has full descriptions: one request per category, no
# page fetches. Region filter: SITE_LOCATIONS["weworkremotely"].
WWR_CATEGORIES = [
    "remote-full-stack-programming-jobs",
    "remote-back-end-programming-jobs",
    "remote-front-end-programming-jobs"
]

# Tuned 2026-09-24 for a "few times a week" run cadence (every 2-3 days):
# HOURS_OLD gives a day of overlap past that gap so a run that slips a day
# late still doesn't miss anything, without HOURS_OLD being so wide that most
# postings a run sees were already scored (and cached) by the previous one.
# For a daily cadence this should drop to ~30-36; for occasional/on-demand
# runs, widen to 120-168.
HOURS_OLD = 96   # only fetch postings from the last N hours (JobSpy-side filter) — 4 days

RESULTS_PER_BOARD = 15  # results requested per board, per search term

# ==========================================
# 2. FILTERING THRESHOLDS
# ==========================================
# --- Pre-filter (deterministic, runs before any LLM call) ---
# Tuned alongside HOURS_OLD above: a real backstop now (~1 day of slack past
# HOURS_OLD=96h/4 days for date-parsing edge cases), not the old 30-day value
# that never actually fired since HOURS_OLD already filtered tighter upstream.
MAX_AGE_DAYS = 5         # belt-and-suspenders on top of HOURS_OLD; catches rows
                         # with a stale date_posted that slip through
MAX_APPLICANTS = 100     # skip LinkedIn postings with MORE than this many
                         # applicants. Read from LinkedIn's public job page
                         # ("91 applicants", "Over 200 applicants" -> 200).
                         # Indeed publishes no applicant count, so Indeed rows
                         # are never filtered by this. Guest-page counts can run
                         # higher than the logged-in view ("Over 200" vs "Over
                         # 100" for the same job), so this is a rough cut.
 
# Blocked postings are never sent to the LLM — they cost nothing, but they also
# get no nuanced judgment, so patterns here must be high-precision.
# Each entry: (label, regex) or (label, regex, exception_regex).
# Regexes run case-insensitively against title + description. When
# exception_regex is given and also matches, the posting is NOT blocked.
# The label is what lands in the blockers column, so keep it short.
HARD_BLOCKERS = [
    # --- clearance / citizenship ---
    ("security clearance", r"\bsecurity clearance\b"),
    ("clearance (TS/SCI)", r"\b(ts/sci|top secret)\b"),
    ("US citizens only",
     r"\b(?:must be (?:a |an )?(?:us|u\.s\.) citizen|citizens? only|us citizenship required)\b"),
 
    # --- onsite / hybrid (the biggest source of wasted calls) ---
    ("onsite required",
     r"\bon-?site\b[^.]{0,20}\b(?:required|mandatory|only|presence|expected|location|position|role)\b"),
    ("onsite required",
     r"\b(?:required|must be able|expected)\s+to\s+(?:be|work|report|come)\s+(?:in\s+)?on-?site\b"),
    ("onsite required",
     r"\bmust\s+(?:be\s+)?(?:able\s+to\s+)?(?:work|report|commute)\s+(?:from\s+)?(?:our|the)\s+office\b"),
    ("N days/week onsite",
     r"\b\d\s*(?:-|\s)?days?\s*(?:per|a|/)\s*week\b[^.]{0,60}?\b(?:on-?site|in[- ]office|in the office)\b"),
    ("N days/week onsite",
     r"\b(?:on-?site|in[- ]office|in the office)\b[^.]{0,60}?\b\d\s*(?:-|\s)?days?\s*(?:per|a|/)\s*week\b"),
    ("onsite in named city",
     r"\bon-?site\s+(?:in|at)\s+[a-z]+(?:\s+[a-z]+)?,\s*[a-z]{2}\b"),
    ("hybrid role",
     r"\bhybrid\s+(?:role|position|job|work|working|schedule|model|setup|arrangement|remote|environment)\b"),
    ("hybrid role",
     r"\b(?:role|position|this job|work model|schedule)\s+is\s+hybrid\b"),
    ("hybrid role", r"\bhybrid\b[^.]{0,30}\b\d\s*days?\b"),
    ("return to office", r"\breturn[- ]to[- ]office\b"),
    ("relocation required",
     r"\brelocation\b[^.]{0,40}\brequired\b|\bwilling(?:ness)?\s+to\s+relocate\b|\bmust\s+relocate\b",
     r"\bno relocation\b|\brelocation\s+(?:is\s+)?not\s+required\b|\brelocation\s+assistance\b"),
 
    # --- US payroll structures ---
    # "W2" alone is a strong signal, but "W2 or C2C" / "W2 or 1099" postings are
    # open to contractors, so those are excused.
    ("W2 / US payroll", r"\bw-?2\b",
     r"\b(?:c2c|corp[- ]to[- ]corp|1099|b2b|independent contractor)\b"),
    ("no C2C / agencies", r"\bno\s+(?:c2c|corp[- ]to[- ]corp|third[- ]part(?:y|ies))\b"),
    ("US work authorization",
     r"\b(?:authorized|authorization|eligible|eligibility)\s+to\s+work\s+in\s+(?:the\s+)?(?:us|u\.s\.|usa|united states)\b"),
 
    # --- geo-locked remote ---
    ("remote, US only", r"\bremote\s*\(\s*(?:us|usa|united states)\b"),
    ("region-locked",
     r"\b(?:us|u\.s\.|usa|united states|canada|uk|united kingdom|eu|european union|europe|latam|latin america|brazil|india|philippines|mexico|australia)[- ]based\s+(?:candidates|applicants|only)\b"),
    ("region-locked",
     r"\bremote\s+(?:only\s+)?(?:in|within|from)\s+(?:the\s+)?(?:us|usa|united states|canada|uk|united kingdom|eu|european union|europe|latam|latin america|brazil|india|mexico|philippines|australia)\b"),
    ("region-locked",
     r"\b(?:latam|latin america|brazil|india|philippines|mexico|us|usa|united states|canada|europe)\s+(?:professionals|candidates|applicants|residents)\s+only\b"),
    ("region-locked",
     r"\bonly\s+(?:accepting\s+)?(?:candidates|applicants)\s+(?:from|located in|based in|residing in)\b"),
]
 
# HARD_BLOCKERS labels about workplace (onsite/hybrid). They are skipped for
# sites with SITE_REMOTE_ONLY False; every other label still applies there.
WORKPLACE_BLOCKER_LABELS = [
    "onsite required", "N days/week onsite", "onsite in named city",
    "hybrid role", "return to office",
]

# Same shape as HARD_BLOCKERS, but matched against the posting TITLE only
# (never the description) — "graduate" or "junior" inside a description
# ("Bachelor's or graduate degree", "mentors junior devs") is common and
# would false-positive if matched against the full text. Title-only keeps
# these high-precision.
TITLE_BLOCKERS = [
    ("junior/intern level",
     r"\b(intern|internship|junior|jr\.?|entry[- ]level|graduate|apprentice)\b"),
]

# Safety valve. True = score everything as before, but record what the
# pre-filter WOULD have blocked in a prefilter_flag column, so you can compare
# the regex verdict against the LLM's on the same run. Costs full price; use it
# for a run or two after changing HARD_BLOCKERS, then set back to False.
PREFILTER_DRY_RUN = False
 
# --- Post-scoring filter (applied to the LLM's output) ---
MIN_OVERALL_SCORE = 67         # postings below this "overall" score are excluded from shortlist.md
MIN_SKILL_MATCH_PERCENT = 75   # same, applied to the LLM's "stack_fit" score
# Per-site overrides of the two scores above; sites not listed use them as-is.
# jobs.ps also targets admin/coordination roles (SITE_EXTRA_CONSTRAINTS), whose
# "stack" is office/coordination skills, so stack_fit is a weaker signal there.
SITE_MIN_OVERALL_SCORE: dict[str, int] = {}
SITE_MIN_SKILL_MATCH_PERCENT: dict[str, int] = {
    "jobsps": 50,
}
# Which LLM verdicts may appear in shortlist.md (still subject to both scores
# above). "skip" is never shortlisted, even if its score clears the bar.
SHORTLIST_VERDICTS = ["apply", "maybe"]
 

# ==========================================
# 3. LLM ENGINE CONFIGURATION
# ==========================================
# Which API scores each posting. "openrouter" (default) routes through
# OpenRouter and can hit any model it serves, including Gemini
# ("google/gemini-2.5-flash") — but every call is billed at OpenRouter's
# rate. "gemini" calls Google's API directly with GEMINI_API_KEY, which has
# a free tier for flash/flash-lite models — worth it for high-volume runs.
# LLM_PROVIDER in .env overrides this if both are set.
LLM_PROVIDER = "openrouter"   # "openrouter" | "gemini"

# Used when LLM_PROVIDER == "openrouter". Any slug from
# https://openrouter.ai/models (check that page for current, exact slugs
# before switching models). Examples: "anthropic/claude-haiku-4.5",
# "google/gemini-2.5-flash", "openai/gpt-4.1-mini".
# OPENROUTER_MODEL in .env overrides this if both are set.
MODEL_NAME = "anthropic/claude-haiku-4.5"

# Used when LLM_PROVIDER == "gemini". Any slug Google's API serves, e.g.
# "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro".
# GEMINI_MODEL in .env overrides this if both are set.
GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"

MODEL_TEMPERATURE = 0.1
MAX_RETRIES = 3             # retry count for API errors / rate limits
RETRY_DELAY_SECONDS = 3     # base delay for exponential backoff (3s, 6s, 12s, ...)

# ==========================================
# 4. CANDIDATE PROFILE & TARGET MATCHING
# ==========================================
# Fallback only — used when --resume is not passed. Prefer keeping your full
# resume in resume.md; that gives the LLM far more to match against than this
# summary alone.
CANDIDATE_PROFILE = """
EXAMPLE — put your real profile in config_local.py (git-ignored).
Candidate Profile:
- Role/Title: Senior Full-Stack Web Developer
- Core Tech Stack: Next.js, React, TypeScript, Node.js, PostgreSQL
"""

# The live constraints — used on every run unless --constraints PATH is passed.
# Avoid conditional wording ("open to X if the employer can do Y"): postings
# rarely state Y, so the condition fails by default and behaves as a flat
# rejection of X. State what is acceptable, and list blockers as exceptions.
# EXAMPLE values — put your real constraints in config_local.py (git-ignored).
CONSTRAINTS = """
EXAMPLE — put your real constraints in config_local.py (git-ignored).
Location: based in <country>, UTC+<N>. Cannot relocate; no work
authorization for <regions>, and cannot be visa-sponsored.
Work setup: fully remote only. No onsite, no hybrid, no "remote but must
be located in X" roles.
Engagement: contractor / B2B or full-time remote employment.
Timezone: <hours you can overlap>.
Seniority: mid-senior. Not seeking junior or intern-level roles.
Not interested in: <role types to avoid>.
"""

# ==========================================
# 5. OUTPUT & RUN BEHAVIOR
# ==========================================
OUTPUT_DIR = "output"
CACHE_PATH = ".jobcache/scores.json"
TOP_N_REPORT = 25            # how many postings go into shortlist.md
WORKERS = 6                  # parallel LLM scoring calls (used for LLM_PROVIDER="openrouter")

# Used instead of WORKERS when LLM_PROVIDER == "gemini". Google's free tier
# for gemini-2.5-flash enforces a per-minute request cap (observed as low as
# 5 RPM on this account, well below the ~10 RPM sometimes documented) — firing
# WORKERS-many requests at once just means most of them 429 immediately. 1
# keeps every call serialized; raise it only if `[config] LLM_PROVIDER=gemini`
# runs show no 429s in output/jobs_scored.csv's `reason` column.
GEMINI_WORKERS = 1

SEARCH_DELAY_SECONDS = 5     # delay between search terms, reduces 429s

# LinkedIn job pages are fetched by job_match.py itself (not JobSpy) so it can
# read applicant count, employment type, post age and Easy Apply alongside the
# description — one request per posting, same as before. Postings already
# blocked by title (TITLE_BLOCKERS) are never fetched.
LINKEDIN_DETAIL_DELAY_SECONDS = 1.0   # pause between job-page requests (+ small random jitter)
LINKEDIN_DETAIL_MAX_FAILURES = 5      # stop fetching after this many failures IN A ROW
                                      # (LinkedIn is throttling); remaining postings are
                                      # skipped as "no description" rather than scored blind

# Dump full descriptions of postings the LLM verdicts as "skip" to
# output/skipped_descriptions/<index>.md, so blocker regexes can be tested
# against the real text instead of the LLM's paraphrase (see PROJECT_STATUS.md
# open item 3 — the W2 anomaly couldn't be diagnosed because descriptions
# weren't saved anywhere). False by default since it writes one file per skip.
DUMP_SKIPPED_DESCRIPTIONS = False

# ==========================================
# 6. HISTORY LOG
# ==========================================
# jobs_scored.csv and shortlist.md are overwritten every run (current-state
# snapshots). history.csv is append-only: every run's rows are added to it
# with a run_timestamp column, so nothing is ever lost. It grows forever —
# nothing prunes it automatically.
ENABLE_HISTORY_LOG = True
HISTORY_FILENAME = "history.csv"   # written inside OUTPUT_DIR

# ==========================================
# 7. PERSONAL OVERRIDES
# ==========================================
# config_local.py (git-ignored) can redefine any setting above — keep your
# real CANDIDATE_PROFILE and CONSTRAINTS there so they're never committed.
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
