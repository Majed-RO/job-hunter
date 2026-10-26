#!/usr/bin/env python3
"""
job_match.py — pull fresh jobs from LinkedIn + Indeed + jobs.ps, score each one
against your resume with an LLM, and write a sorted shortlist per site.

Each site's scraper lives in its own module under sources/ (linkedin.py,
indeed.py, jobsps.py); only the sites enabled for the run are imported.

All tunable values (search terms, thresholds, model, output paths, ...) live
in config.py. Edit that file for day-to-day changes; CLI flags below exist
only to override a value for a single run.

Install:
    pip install -U python-jobspy openai pandas python-dotenv
    # optional, only if your resume is a PDF or .docx:
    pip install pypdf python-docx
    # optional, only if LLM_PROVIDER=gemini:
    pip install google-genai

Setup:
    Create a .env file (see .env.example) with:
        OPENROUTER_API_KEY=sk-or-...
        OPENROUTER_MODEL=anthropic/claude-haiku-5.5   # optional, overrides config.MODEL_NAME

    To score with Gemini's API directly instead of via OpenRouter (its free
    tier is worth it for high-volume runs), also set:
        LLM_PROVIDER=gemini                            # overrides config.LLM_PROVIDER
        GEMINI_API_KEY=...
        GEMINI_MODEL=gemini-2.5-flash                  # optional, overrides config.GEMINI_MODEL_NAME

Run:
    python job_match.py                       # uses everything from config.py
    python job_match.py --results 10          # one-off override
    python job_match.py --sites jobsps        # search only jobs.ps this run
    python job_match.py --sites linkedin indeed
    python job_match.py --provider gemini     # score this run with Gemini instead
                                               # (needs GEMINI_API_KEY in .env either way)
    python job_match.py --dry-run             # test run: outputs go to output/dry_run/,
                                               # history.csv is left alone

Outputs (paths set by config.OUTPUT_DIR / config.CACHE_PATH):
    output/jobs_scored_<site>.csv  full table for one site, best first, with a pass/fail column
    output/shortlist_<site>.md     that site's postings that clear config's thresholds, best first
                                   (both rewritten only for the sites searched in a run, so a
                                   --sites jobsps run leaves the LinkedIn/Indeed files alone)
    output/run_summary.md          this run's per-site counts at each step, plus step timings
    output/history.csv             every run's rows from all sites, appended with a run_timestamp
                                   column — never overwritten
    .jobcache/scores.json    score cache, so re-runs only pay for new postings
"""

from __future__ import annotations

__version__ = "1.0.0"  # see CHANGELOG.md; bump it there and here together

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

import config
from sources import REGISTRY, load_source
from sources.common import (RUN_STATS, STANDARD_COLUMNS, days_since, is_city_level, title_blocked,
                            utf8_console)

load_dotenv()  # reads .env in the current directory into os.environ, if present


def resolve_provider_and_model(provider_override: str | None = None) -> tuple[str, str]:
    """Pick the LLM provider + model. Precedence: --provider flag > LLM_PROVIDER
    in .env > config.LLM_PROVIDER. The model always follows the resolved
    provider (OPENROUTER_MODEL/GEMINI_MODEL in .env still work per-provider)."""
    provider = (provider_override or os.getenv("LLM_PROVIDER")
                or getattr(config, "LLM_PROVIDER", "openrouter")).strip().lower()
    if provider not in ("openrouter", "gemini"):
        sys.exit(f"Unknown provider {provider!r}; expected 'openrouter' or 'gemini'.")
    model = (
        (os.getenv("GEMINI_MODEL") or config.GEMINI_MODEL_NAME)
        if provider == "gemini"
        else (os.getenv("OPENROUTER_MODEL") or config.MODEL_NAME)
    )
    return provider, model


# Resolved once at import time from .env/config.py; main() re-resolves and
# overwrites these if --provider is passed, since CLI flags win last.
PROVIDER, MODEL = resolve_provider_and_model()
# Same pattern for the effective remote-only setting per site this run
# (--remote/--no-remote, else config.SITE_REMOTE_ONLY, else config.IS_REMOTE):
# prefilter() needs it but doesn't receive `args`.
SITE_REMOTE_ONLY: dict[str, bool] = {}
# --dry-run: main() points OUT_DIR at output/dry_run/ and append_history()
# skips history.csv, so a test run leaves the real outputs alone.
DRY_RUN = False


def remote_only(site) -> bool:
    return SITE_REMOTE_ONLY.get(str(site).lower(), config.IS_REMOTE)
CACHE_PATH = Path(config.CACHE_PATH)
OUT_DIR = Path(config.OUTPUT_DIR)
MAX_DESC_CHARS = 6000  # trim descriptions so token cost stays predictable

# Cheap pre-filter: kill obvious non-starters before spending an API call.
# Patterns live in config.HARD_BLOCKERS so they can be tuned without touching
# this file. Compiled once here; each entry is (label, regex[, exception_regex]).
# Labels in config.WORKPLACE_BLOCKER_LABELS (onsite/hybrid) only apply to
# sites searched remote-only.
_WORKPLACE_LABELS = set(getattr(config, "WORKPLACE_BLOCKER_LABELS", []))
_COMPILED_BLOCKERS = [
    (entry[0], re.compile(entry[1], re.I), re.compile(entry[2], re.I) if len(entry) > 2 else None)
    for entry in config.HARD_BLOCKERS
]
# Location gate (see config.LOCATION_GATE_SITES): one combined regex per list.
_LOC_GATE_SITES = {s.lower() for s in getattr(config, "LOCATION_GATE_SITES", [])}


def _any_of(patterns: list[str]) -> re.Pattern | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I) if patterns else None


_LOC_HOME = _any_of(getattr(config, "LOCATION_HOME", []))
_LOC_ALLOWED = _any_of(getattr(config, "LOCATION_HOME", []) + getattr(config, "LOCATION_ALLOWED", []))
_LOC_GLOBAL = _any_of(getattr(config, "LOCATION_GLOBAL_REMOTE", []))
_LOC_REMOTE_STATEMENT = _any_of(getattr(config, "LOCATION_REMOTE_STATEMENT", []))
_KEEP_LANGUAGES = _any_of(getattr(config, "LOCATION_GATE_KEEP_LANGUAGES", []))
_REMOTE_WORDS = _any_of(getattr(config, "LOCATION_REMOTE_WORDS", []))
NON_ENGLISH_LABEL = "not in English"
LIKELY_ONSITE_LABEL = "likely on-site (city location, never says remote)"
LOCATION_LABEL = "location outside allowlist"
COUNTRY_REMOTE_LABEL = "remote within one country"

SYSTEM_PROMPT = """You screen job postings for a specific candidate. You are blunt and calibrated: most postings are a mediocre fit and should score accordingly. Reserve 85+ for postings where the candidate is clearly in the top slice of applicants.

Return ONLY a JSON object, no prose, no markdown fences, with exactly these keys:
{
  "eligibility": "pass" | "fail" | "unclear",
  "eligibility_reason": "one sentence, max 20 words: what in the posting decides it",
  "overall": int 0-100,
  "stack_fit": int 0-100,
  "seniority_fit": int 0-100,
  "domain_fit": int 0-100,
  "logistics_fit": int 0-100,
  "verdict": "apply" | "maybe" | "skip",
  "reason": "one sentence, max 25 words",
  "matched": ["at most 5 concrete skills the posting asks for that the resume proves"],
  "gaps": ["at most 3 requirements the resume does not cover"],
  "blockers": ["hard disqualifiers: citizenship/clearance/visa sponsorship-required/timezone/onsite. Empty list if none."]
}

Decide eligibility FIRST, before judging fit: can this candidate be hired for this role
from where they live, under their constraints? Use the Location, Remote and Workplace
check lines as well as the description.
- "fail": something rules the candidate out: on-site or hybrid work outside the
  candidate's home area; a requirement to live in, be located in, or be authorized to
  work in a country or region the candidate isn't in (e.g. "remote within the US",
  "must reside in Canada", "EU work permit"); visa sponsorship needed; US payroll/W2
  only; citizenship or clearance. Also fail when the text itself ties the role to the
  listed country: "based in <country>", "remote within <country>", a local employment
  contract, or benefits/pay ranges stated for that country only. Also fail when the
  role is listed in a country outside the candidate's region and the text never says it
  is remote.
- "pass": the posting shows it is open to someone where the candidate lives: remote from
  anywhere/worldwide, a region that includes the candidate's location (e.g. MENA,
  Middle East, EMEA without an EU-only condition, also when named in the title), or the
  candidate's own country.
- "unclear": the text says remote, but not where candidates may live, and nothing above
  rules the candidate out. A listed country, a USD salary, a background check or the
  lack of a statement about other countries is NOT a reason to fail: that is "unclear".
  Wording like "Remote/Hybrid" or "remote or on-site"
  does not show the role is fully remote; if the Workplace check line warns it may be
  on-site, eligibility is at best "unclear".
eligibility_reason quotes or names what decided it ("Location: Brazil, no remote
statement", "'100% remote from anywhere'").

If eligibility is "fail": verdict "skip", logistics_fit below 20, overall at most 30.
If "unclear": score fit normally; the missing location statement alone is not a reason
to mark the posting down or skip it, since the candidate will check the apply page.

logistics_fit scores location, work authorization, timezone overlap and contract type
against the candidate's constraints."""

USER_TEMPLATE = """<candidate_resume>
{resume}
</candidate_resume>

<candidate_constraints>
{constraints}
</candidate_constraints>

<job_posting>
Source: {site}
Title: {title}
Company: {company}
Location: {location}
Remote: {remote}
Job type: {job_type}
Posted: {date_posted}
Compensation: {comp}
Workplace check: {workplace_check}

{description}
</job_posting>

Score this posting for this candidate. JSON only."""


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_resume(path: str | None) -> str:
    if not path:
        return config.CANDIDATE_PROFILE
    p = Path(path)
    if not p.exists():
        sys.exit(f"Resume not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages)
    if suffix == ".docx":
        import docx  # python-docx

        return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    return p.read_text(encoding="utf-8", errors="ignore")


def load_constraints(path: str | None) -> str:
    """Free-text file describing what you will and won't take.

    Falls back to config.CONSTRAINTS when no --constraints file is passed.
    """
    if not path:
        return config.CONSTRAINTS
    p = Path(path)
    if not p.exists():
        sys.exit(f"Constraints file not found: {p}\n"
                 "Omit --constraints to use config.CONSTRAINTS instead.")
    return p.read_text(encoding="utf-8", errors="ignore")


# --------------------------------------------------------------------------- #
# Scrape
# --------------------------------------------------------------------------- #
# Each site's scraping lives in its own module under sources/ (see
# sources/__init__.py); only the sites enabled for this run are imported.
def fetch_jobs(args) -> pd.DataFrame:
    frames = []
    for name in args.sites:
        with step_timer(f"scrape {name}"):
            df = load_source(name).fetch(args)
        RUN_STATS.setdefault("rows_by_source", {})[name] = len(df)
        if len(df):
            frames.append(df.dropna(axis=1, how="all"))

    if not frames:
        RUN_STATS.update(rows_scraped=0, unique_postings=0)
        return pd.DataFrame(columns=list(STANDARD_COLUMNS))

    # Drop columns that are entirely empty in a given source's results before
    # merging (silences a pandas FutureWarning; the columns this script uses
    # are re-added just below if missing).
    jobs = pd.concat(frames, ignore_index=True)
    for col in STANDARD_COLUMNS:
        if col not in jobs.columns:
            jobs[col] = None

    jobs["dedupe_key"] = (
        jobs["title"].fillna("").map(dedupe_title)
        + "|"
        + jobs["company"].fillna("").str.lower().str.strip()
    )
    # Same company + identical full description = the same role even when the
    # title was reworded. Short text (jobs.ps feed snippets, stubs) can match
    # by coincidence, so it's left out of this check.
    desc = jobs["description"].map(lambda d: " ".join(d.lower().split()) if isinstance(d, str) else "")
    jobs["dedupe_desc_key"] = (jobs["company"].fillna("").str.lower().str.strip() + "|" + desc).where(
        desc.str.len() >= MIN_DEDUPE_DESC_CHARS)
    before = len(jobs)
    # A posting is "wide only" when every search that found it used a wide
    # term; those get the keyword pre-filter (see prefilter()).
    wide_terms = set(getattr(config, "WIDE_SEARCH_TERMS", []))
    found_by_wide = jobs["search_term"].isin(wide_terms) if "search_term" in jobs else pd.Series(False, index=jobs.index)
    jobs["wide_only"] = found_by_wide.groupby(jobs["job_url"]).transform("all")
    jobs = jobs.drop_duplicates(subset=["job_url"])
    jobs["also_posted"] = None
    # The same role posted several times (e.g. once per country) is scored
    # once; the copies' links are kept on it so none is lost.
    jobs = merge_duplicates(jobs, "dedupe_key")
    jobs = merge_duplicates(jobs, "dedupe_desc_key")
    print(f"[scrape] {before} rows → {len(jobs)} unique postings")
    RUN_STATS.update(rows_scraped=before, unique_postings=len(jobs),
                     unique_by_source=jobs["site"].astype(str).str.lower().value_counts().to_dict())
    return jobs.reset_index(drop=True)


MIN_DEDUPE_DESC_CHARS = 500  # shorter descriptions aren't compared for duplicates


def merge_duplicates(jobs: pd.DataFrame, key: str) -> pd.DataFrame:
    """Keep the first row per non-empty `key`; the dropped rows' links (and
    their own also_posted links) are appended to the kept row's also_posted."""
    keyed = jobs[key].notna()
    dup = keyed & jobs.duplicated(subset=[key])
    if not dup.any():
        return jobs
    jobs = jobs.copy()
    first = jobs[keyed & ~dup].reset_index().set_index(key)["index"]
    for idx in jobs.index[dup]:
        links = [jobs.at[idx, "job_url"], jobs.at[idx, "also_posted"]]
        keep = first[jobs.at[idx, key]]
        jobs.at[keep, "wide_only"] = bool(jobs.at[keep, "wide_only"] and jobs.at[idx, "wide_only"])
        jobs.at[keep, "also_posted"] = "; ".join(
            str(x) for x in [jobs.at[keep, "also_posted"], *links] if isinstance(x, str) and x)
    return jobs[~dup]


# Reference codes employers append to otherwise identical titles, e.g.
# "... Remote Work | REF#289637" vs "| REF#289638" for the same role posted
# per country. Stripped (at the end of the title only) before comparing.
_TITLE_REF_RE = re.compile(
    r"[\s|\-–—(\[]*(?:\b(?:ref|req|requisition|job\s*id|id)\b\s*[#:.]?\s*|#)"
    r"[a-z]*[-_]?\d[\w-]*[\s)\]]*$", re.I)


def dedupe_title(title: str) -> str:
    """Title in the form used to spot the same role posted twice."""
    return " ".join(_TITLE_REF_RE.sub("", str(title)).lower().split())


_WIDE_KEYWORDS = [re.compile(p, re.I) for p in getattr(config, "WIDE_REQUIRED_KEYWORDS", [])]
_WIDE_LABEL = "wide search: no stack keyword (React/Next/TS/Node/JS)"
CLOSED_LABEL = "no longer accepting applications"


def prefilter(row) -> str | None:
    """Return a short label if this posting should skip LLM scoring, else None."""
    title = row.get("title") or ""
    desc = row.get("description")
    desc = desc if isinstance(desc, str) else ""
    text = f"{title} {desc}"

    blocked = title_blocked(title)
    if blocked:
        return blocked

    # LinkedIn page says "No longer accepting applications".
    if _flag(row.get("closed")):
        return CLOSED_LABEL

    # Page fetch failed (or was cut short by throttling): scoring a title with
    # no description would just be a guess.
    if not desc.strip():
        return f"no description ({row.get('site')} page fetch failed)"

    # Wide-term results (e.g. "Web Developer") are mostly other stacks; keep
    # only those that mention the candidate's stack. Narrow-term hits skip this.
    # Outside LinkedIn, postings confirmed remote are scored anyway: only ~6 an
    # Indeed run, and one named no framework but fit (Sensia Consulting,
    # freelance remote AI web tool, 2026-10-14). The rest are dropped as
    # "not confirmed remote" below.
    wide = row.get("wide_only")
    remote_confirmed = row.get("site") != "linkedin" and _flag(row.get("is_remote"))
    if pd.notna(wide) and wide and not remote_confirmed:
        if not any(p.search(text) for p in _WIDE_KEYWORDS):
            return _WIDE_LABEL

    # LinkedIn's own "remote only" search filter (f_WT=2) is a request-side
    # hint, not a guarantee — promoted/sponsored listings have been observed
    # to bypass it and show up onsite anyway despite is_remote=True. JobSpy
    # separately computes its own is_remote per posting (keyword match for
    # LinkedIn; structured attributes + keywords for Indeed) from the actual
    # scraped title/description/location, which is the more trustworthy
    # signal — use it as a backstop the same way MAX_AGE_DAYS backstops
    # HOURS_OLD. Only fires when this posting's site is searched remote-only
    # and is_remote is explicitly False (not NaN/None: "couldn't tell").
    # Not applied to LinkedIn: its public job page has no workplace badge, so
    # a genuinely remote posting often never says "remote" in its text (Modern
    # Family Law, 2026-10-06) and was dropped. LinkedIn rows rely on the
    # remote search filter, the explicit onsite/hybrid blockers below, and the
    # likely_onsite warning that goes to the scorer and the shortlist.
    is_remote_flag = row.get("is_remote")
    site_remote_only = remote_only(row.get("site"))
    if (site_remote_only and row.get("site") != "linkedin"
            and pd.notna(is_remote_flag) and not is_remote_flag):
        return "not confirmed remote (no remote/WFH signal found)"

    # Applicant count from the LinkedIn page (Indeed rows have none -> never fire).
    # Easy Apply + "Over 200" applicants: flooded (see config). Other crowded
    # postings are scored and need a higher score instead (is_crowded).
    applicants = pd.to_numeric(row.get("applicants"), errors="coerce")
    if (getattr(config, "DROP_SATURATED_EASY_APPLY", False) and _flag(row.get("easy_apply"))
            and pd.notna(applicants) and applicants >= config.SATURATED_APPLICANTS):
        return f"saturated Easy Apply ({row.get('applicants_text') or int(applicants)})"

    for label, pattern, exception in _COMPILED_BLOCKERS:
        if label in _WORKPLACE_LABELS and not site_remote_only:
            continue
        if pattern.search(text) and not (exception and exception.search(text)):
            return label

    blocked = location_blocked(row, text)
    if blocked:
        return blocked

    # Age: LinkedIn's own "N days ago" when we have it, else JobSpy's date_posted.
    age_days = row.get("days_old")
    if age_days is None or pd.isna(age_days):
        age_days = days_since(row.get("date_posted"))
    max_age = max_age_days(row.get("site"))
    if age_days is not None and age_days > max_age:
        return f"older than {max_age} days"

    return None


_ENGLISH_WORDS = {"the", "and", "to", "of", "in", "you", "with", "for", "is", "are", "we", "our",
                  "will", "your", "be", "as", "on", "or", "this", "that"}


def english_share(description) -> float:
    """Share of words in the text that are common English words: about 0.2
    for English, under 0.04 for French/Spanish/Portuguese/Croatian."""
    words = re.findall(r"[a-z']+", str(description or "").lower())
    return sum(w in _ENGLISH_WORDS for w in words) / len(words) if words else 0.0


def location_blocked(row, text: str) -> str | None:
    """Label if a remote-only site's posting is tied to a place the candidate
    can't work from (config.LOCATION_*), else None. Text saying the role is
    open from anywhere, or a location in LOCATION_HOME, always passes."""
    site = str(row.get("site")).lower()
    if site not in _LOC_GATE_SITES or not remote_only(site):
        return None
    location = _str_or(row.get("location"), "").strip()
    # Not in English (and not a kept language like Arabic): a local posting.
    desc = row.get("description")
    if (english_share(desc) < getattr(config, "LOCATION_GATE_MIN_ENGLISH_SHARE", 0)
            and not (_KEEP_LANGUAGES and _KEEP_LANGUAGES.search(str(desc or "")))):
        return NON_ENGLISH_LABEL
    # Descriptions are markdown with escaped punctuation: "Remote\-first" (G2i).
    text = text.replace("\\", "")
    if (_LOC_GLOBAL and _LOC_GLOBAL.search(text)) or (_LOC_HOME and _LOC_HOME.search(location)):
        return None
    # City-level location and the text never says remote/WFH (is_remote is
    # False, not unknown): LinkedIn's hidden badge is most likely On-site. Al
    # Salah Holding (Dubai, 2026-10-15) was scored "maybe" on stack alone.
    is_remote = row.get("is_remote")
    if (_flag(row.get("likely_onsite")) and is_remote is not None and pd.notna(is_remote) and not is_remote
            and not (_REMOTE_WORDS and _REMOTE_WORDS.search(text))):
        return LIKELY_ONSITE_LABEL
    if location and _LOC_ALLOWED and not _LOC_ALLOWED.search(location):
        # A whole country ("United States", not "Dallas, TX") on a remote
        # LinkedIn posting means remote within that country; roles open more
        # widely are listed under a region or "Worldwide". Text saying "fully
        # remote" goes to the LLM anyway and is listed apart (is_country_remote).
        if not is_city_level(location):
            return None if is_country_remote(row) else f"{COUNTRY_REMOTE_LABEL} ({location})"
        return f"{LOCATION_LABEL} ({location})"
    return None


def is_country_remote(row) -> bool:
    """A gate site's posting listed under a whole country outside
    LOCATION_HOME/LOCATION_ALLOWED ("United States", not "Dallas, TX") whose
    text says "fully remote" / "remote-first" but not from where. On LinkedIn
    that mostly means remote within that country (Topstep, 2026-10-18, asked
    for US work authorization), but some hire globally without saying so
    (G2i). These are scored, so the LLM fails the ones whose text ties them to
    the country, and the rest are listed apart from the shortlist."""
    site = str(row.get("site")).lower()
    if site not in _LOC_GATE_SITES or not remote_only(site):
        return False
    location = _str_or(row.get("location"), "").strip()
    if not location or is_city_level(location) or (_LOC_ALLOWED and _LOC_ALLOWED.search(location)):
        return False
    text = f"{row.get('title') or ''} {_str_or(row.get('description'), '')}".replace("\\", "")
    return bool(_LOC_REMOTE_STATEMENT and _LOC_REMOTE_STATEMENT.search(text)
                and not (_LOC_GLOBAL and _LOC_GLOBAL.search(text)))


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #
def cache_load() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def cache_save(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=0))


def constraints_for(site, base: str) -> str:
    """base constraints plus this site's config.SITE_EXTRA_CONSTRAINTS entry, if any."""
    extra = getattr(config, "SITE_EXTRA_CONSTRAINTS", {}).get(str(site).lower())
    return f"{base.rstrip()}\n\n{extra.strip()}\n" if extra else base


def max_age_days(site) -> int:
    """MAX_AGE_DAYS for one site: its SITE_MAX_AGE_DAYS entry, else the global."""
    return getattr(config, "SITE_MAX_AGE_DAYS", {}).get(str(site).lower(), config.MAX_AGE_DAYS)


def min_scores(site) -> tuple[int, int]:
    """(min overall, min stack_fit) for one site: its SITE_MIN_* entry, else the global."""
    site = str(site).lower()
    return (getattr(config, "SITE_MIN_OVERALL_SCORE", {}).get(site, config.MIN_OVERALL_SCORE),
            getattr(config, "SITE_MIN_SKILL_MATCH_PERCENT", {}).get(site, config.MIN_SKILL_MATCH_PERCENT))


def is_crowded(row) -> bool:
    """More LinkedIn applicants than config.CROWDED_APPLICANTS."""
    limit = getattr(config, "CROWDED_APPLICANTS", None)
    applicants = pd.to_numeric(row.get("applicants"), errors="coerce")
    return limit is not None and pd.notna(applicants) and applicants > limit


def thresholds_text(site) -> str:
    overall, stack = min_scores(site)
    text = f"overall >= {overall}, stack_fit >= {stack}"
    if str(site).lower() == "linkedin" and getattr(config, "CROWDED_APPLICANTS", None) is not None:
        text += (f"; overall >= {config.CROWDED_MIN_OVERALL_SCORE} when over "
                 f"{config.CROWDED_APPLICANTS} applicants")
    return text


def cache_key(resume: str, constraints: str, row) -> str:
    # PROVIDER is part of the key too: the same model NAME can mean different
    # things (or the two providers can drift), and a stale cross-provider hit
    # would silently reuse a score from the other API.
    # likely_onsite changes the prompt (see workplace_check), so it's part of
    # the key: a posting whose flag flips gets re-scored instead of reusing a
    # score made without the warning. Constraints (with any per-site extra)
    # are in it so editing them re-scores the postings they apply to.
    # A jobs.ps feed-only posting (page blocked) gets its own key, so once its
    # page can be fetched it's re-scored from the full description.
    # The prompts and reasoning effort change the answer too, so they're in it:
    # editing any of them re-scores instead of serving the old scores. So are
    # location and the remote flag: a posting first scored with no location
    # (see sources/linkedin.py) is re-scored once its location is known.
    blob = (f"{resume}|{constraints}|{PROVIDER}|{MODEL}|{SYSTEM_PROMPT}|{USER_TEMPLATE}|"
            f"effort={getattr(config, 'MODEL_REASONING_EFFORT', None)}|{row.get('job_url')}|{row.get('title')}|"
            f"{row.get('company')}|onsite={_flag(row.get('likely_onsite'))}|"
            f"location={_str_or(row.get('location'), '')}|remote={remote_text(row)}"
            + ("|feed_only" if _flag(row.get("feed_only")) else ""))
    return hashlib.sha1(blob.encode()).hexdigest()


def _flag(value) -> bool:
    """Truthy flag; None/NaN/False -> False."""
    try:
        return bool(value) and not pd.isna(value)
    except (TypeError, ValueError):
        return False


def _str_or(value, fallback: str) -> str:
    return value if isinstance(value, str) and value.strip() else fallback


def remote_text(row) -> str:
    """The posting's is_remote flag for the prompt. LinkedIn: whether the
    title, text or location says remote/WFH; Indeed: JobSpy's flag; We Work
    Remotely: always; jobs.ps: the page's workplace type."""
    flag = row.get("is_remote")
    if flag is None or pd.isna(flag):
        return "not known"
    return "yes" if flag else "no remote signal in the posting"


def workplace_check(row) -> str:
    if not _flag(row.get("likely_onsite")):
        return "no warning"
    return (f"WARNING: LinkedIn lists a city-level location ({row.get('li_location')}) and "
            "the text has no explicit fully-remote statement; LinkedIn's hidden workplace "
            "badge may say On-site or Hybrid.")


def comp_string(row) -> str:
    lo, hi, interval = row.get("min_amount"), row.get("max_amount"), row.get("interval")
    if pd.isna(lo) and pd.isna(hi):
        return "not stated"
    parts = [str(int(v)) for v in (lo, hi) if not pd.isna(v)]
    return f"{'–'.join(parts)} {interval or ''}".strip()


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def make_llm_client():
    """Build the client for config/`.env`-selected PROVIDER ("openrouter" or "gemini")."""
    if PROVIDER == "gemini":
        try:
            from google import genai
        except ImportError:
            sys.exit("LLM_PROVIDER=gemini requires the google-genai package: "
                     "pip install google-genai")
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            sys.exit("GEMINI_API_KEY is not set (required when LLM_PROVIDER=gemini).")
        return genai.Client(api_key=api_key)
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        default_headers={"HTTP-Referer": "https://localhost", "X-Title": "job-match"},
    )


def _call_openrouter(client: OpenAI, prompt: str) -> str:
    effort = getattr(config, "MODEL_REASONING_EFFORT", None)
    msg = client.chat.completions.create(
        model=MODEL,
        max_tokens=getattr(config, "MODEL_MAX_TOKENS", 4000),  # thinking counts toward it
        temperature=config.MODEL_TEMPERATURE,
        response_format={"type": "json_object"},  # not all OpenRouter models honor this; parse_json() has a fallback
        extra_body={"reasoning": {"effort": effort}} if effort else None,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return msg.choices[0].message.content


def _call_gemini(client, prompt: str) -> str:
    from google.genai import types  # local import: only needed for this provider

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",  # Gemini honors this reliably; parse_json() still has a fallback
            temperature=config.MODEL_TEMPERATURE,
            max_output_tokens=700,
        ),
    )
    return response.text


def score_one(client, resume: str, constraints: str, row) -> dict:
    prompt = USER_TEMPLATE.format(
        resume=resume,
        constraints=constraints,
        site=row.get("site"),
        title=row.get("title"),
        company=row.get("company"),
        location=_str_or(row.get("location"), "not stated"),
        remote=remote_text(row),
        job_type=_str_or(row.get("employment_type"), _str_or(row.get("job_type"), "not stated")),
        date_posted=row.get("date_posted"),
        comp=comp_string(row),
        workplace_check=workplace_check(row),
        description=(row.get("description") or "")[:MAX_DESC_CHARS],
    )
    call = _call_gemini if PROVIDER == "gemini" else _call_openrouter
    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            return parse_json(call(client, prompt))
        except Exception as exc:
            last_error = exc
            time.sleep(_retry_delay_seconds(exc, attempt))
    return {"overall": -1, "verdict": "error", "reason": f"scoring failed: {last_error}",
            "matched": [], "gaps": [], "blockers": []}


_RETRY_AFTER_RE = re.compile(r"retry in ([\d.]+)s", re.I)


ELIGIBILITY_VALUES = ("pass", "fail", "unclear")


def apply_eligibility(result: dict) -> dict:
    """The LLM's eligibility verdict, normalized (anything else counts as
    "unclear"). A "fail" scores 0 and is skipped whatever fit scores the LLM
    gave, so it is never shortlisted. Applied on read, not stored in the
    cache, so changing this rule doesn't need a re-score."""
    if "eligibility" not in result:  # pre-filtered, or a failed call
        return result
    result = dict(result)
    value = str(result.get("eligibility") or "").strip().lower()
    result["eligibility"] = value if value in ELIGIBILITY_VALUES else "unclear"
    if result["eligibility"] == "fail":
        result.update(overall=0, verdict="skip")
    return result


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    """How long to wait before the next attempt.

    A 429 from Gemini's free tier states exactly how long to wait (e.g.
    "Please retry in 58.8s") — that number is what actually clears the quota
    window, and it's frequently much longer than a short exponential backoff
    would produce, so honor it directly instead of guessing. Falls back to
    the usual RETRY_DELAY_SECONDS * 2^attempt backoff for anything else.
    """
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        return float(match.group(1)) + 1  # small buffer past the API's own deadline
    return config.RETRY_DELAY_SECONDS * (2 ** attempt)


def _dump_skipped_descriptions(jobs: pd.DataFrame, results: dict[int, dict]) -> None:
    """Write full descriptions of LLM-verdicted "skip" postings to disk.

    Only the LLM's own skips (not the free pre-filter's, which already have a
    known blocker label) — this is for diagnosing cases like a W2 posting the
    regex missed, where the LLM's paraphrase of a blocker
    didn't match the regex meant to catch it, and descriptions weren't saved
    anywhere to check the regex against the real text. Gated behind
    config.DUMP_SKIPPED_DESCRIPTIONS since it writes one file per skip.
    """
    dump_dir = OUT_DIR / "skipped_descriptions"
    n = 0
    for idx, result in results.items():
        if result.get("verdict") != "skip" or result.get("reason") == "filtered out before scoring":
            continue
        row = jobs.loc[idx]
        dump_dir.mkdir(parents=True, exist_ok=True)
        blockers = result.get("blockers")
        blockers_str = "; ".join(blockers) if isinstance(blockers, list) else (blockers or "")
        text = (
            f"# {row.get('title')} — {row.get('company')}\n\n"
            f"job_url: {row.get('job_url')}\n"
            f"llm reason: {result.get('reason')}\n"
            f"llm blockers: {blockers_str}\n\n"
            f"---\n\n{row.get('description') or ''}\n"
        )
        (dump_dir / f"{idx}.md").write_text(text, encoding="utf-8")
        n += 1
    if n:
        print(f"[score] dumped {n} skipped posting description(s) to {dump_dir}/")


def score_all(jobs: pd.DataFrame, resume: str, constraints: str, workers: int) -> pd.DataFrame:
    client = make_llm_client()
    cache = cache_load()
    results: dict[int, dict] = {}
    todo = []

    dry_run = config.PREFILTER_DRY_RUN
    flags: dict[int, str] = {}
    n_blocked = 0
    via: dict[int, str] = {}  # idx -> "prefiltered" / "from_cache" / "llm_calls", for per-site metrics

    for idx, row in jobs.iterrows():
        blocked = prefilter(row)
        if blocked:
            n_blocked += 1
            flags[idx] = blocked
            if not dry_run:
                via[idx] = "prefiltered"
                results[idx] = {"overall": 0, "stack_fit": 0, "seniority_fit": 0,
                                "domain_fit": 0, "logistics_fit": 0, "verdict": "skip",
                                "reason": "filtered out before scoring",
                                "matched": [], "gaps": [], "blockers": [blocked]}
                continue
        row_constraints = constraints_for(row.get("site"), constraints)
        key = cache_key(resume, row_constraints, row)
        if key in cache:
            results[idx] = cache[key]
            via[idx] = "from_cache"
        else:
            todo.append((idx, key, row_constraints, row))
            via[idx] = "llm_calls"

    if dry_run:
        print(f"[score] PREFILTER_DRY_RUN: {n_blocked} posting(s) flagged but still "
              f"being scored — compare prefilter_flag vs verdict in the CSV")
    print(f"[score] {len(results)} from cache/filter, {len(todo)} to score with {MODEL}")
    RUN_STATS.update(prefiltered=0 if dry_run else n_blocked,
                     from_cache=len(results) - (0 if dry_run else n_blocked),
                     llm_calls=len(todo))
    by_site = RUN_STATS.setdefault("scoring_by_source", {})
    sites = jobs["site"].astype(str).str.lower()
    for idx, how in via.items():
        counts = by_site.setdefault(sites[idx], {"flagged": 0})
        counts[how] = counts.get(how, 0) + 1
    for idx in flags:
        by_site.setdefault(sites[idx], {"flagged": 0})["flagged"] += 1

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(score_one, client, resume, row_constraints, row): (idx, key)
                for idx, key, row_constraints, row in todo
            }
            for n, future in enumerate(as_completed(futures), 1):
                idx, key = futures[future]
                result = future.result()
                results[idx] = result
                if result.get("overall", -1) >= 0:
                    cache[key] = result
                if n % 10 == 0 or n == len(todo):
                    print(f"  scored {n}/{len(todo)}", flush=True)
        cache_save(cache)

    results = {idx: apply_eligibility(result) for idx, result in results.items()}

    if getattr(config, "DUMP_SKIPPED_DESCRIPTIONS", False):
        _dump_skipped_descriptions(jobs, results)

    scored = pd.DataFrame.from_dict(results, orient="index")
    for col in ("overall", "stack_fit", "seniority_fit", "domain_fit", "logistics_fit"):
        if col not in scored.columns:
            scored[col] = 0
    for col in ("matched", "gaps", "blockers"):
        scored[col] = scored[col].apply(lambda v: "; ".join(v) if isinstance(v, list) else "")

    merged = jobs.join(scored)
    if dry_run:
        merged["prefilter_flag"] = pd.Series(flags)
    return merged.sort_values("overall", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def append_history(df: pd.DataFrame, cols: list[str]) -> Path | None:
    """Append this run's rows to output/history.csv, never overwriting it.

    Adds a run_timestamp column so every run is distinguishable. Writes the
    header only if the file doesn't exist yet. This file grows without bound
    — nothing here prunes it. Returns the path written to, or None if
    disabled via config.ENABLE_HISTORY_LOG or on a --dry-run.
    """
    if not config.ENABLE_HISTORY_LOG or DRY_RUN:
        return None
    history_path = OUT_DIR / config.HISTORY_FILENAME
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    hist = df.copy()
    hist.insert(0, "run_timestamp", run_ts)
    hist_cols = ["run_timestamp"] + cols

    # If the column set changed since the file was created (e.g. a new column
    # was added to the script), a blind append would misalign every column.
    # Merge old and new rows under the combined header instead; old rows get
    # empty values for columns that didn't exist yet.
    if history_path.exists():
        existing_cols = pd.read_csv(history_path, nrows=0).columns.tolist()
        if existing_cols != hist_cols:
            old = pd.read_csv(history_path)
            merged = pd.concat([old, hist[hist_cols]], ignore_index=True)
            # Current columns first, in the script's order, so the next run's
            # header matches and takes the fast append path below.
            all_cols = hist_cols + [c for c in old.columns if c not in hist_cols]
            merged[all_cols].to_csv(history_path, index=False,
                                    quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
            return history_path

    write_header = not history_path.exists()
    hist[hist_cols].to_csv(history_path, mode="a", index=False, header=write_header,
                           quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
    return history_path


def shortlist_meta(r) -> str:
    """'linkedin · Cairo, Egypt · 2 days old · 45 applicants · Contract · Easy Apply'"""
    parts = [str(r.get("site")), _str_or(r.get("li_location"), str(r.get("location")))]
    days = r.get("days_old")
    if days is not None and not pd.isna(days):
        parts.append("today" if int(days) == 0 else f"{int(days)} day{'s' if int(days) != 1 else ''} old")
    elif r.get("date_posted") is not None and not pd.isna(r.get("date_posted")):
        parts.append(str(r.get("date_posted")))
    if _str_or(r.get("applicants_text"), ""):
        parts.append(r["applicants_text"])
    if _str_or(r.get("employment_type"), ""):
        parts.append(r["employment_type"])
    easy = r.get("easy_apply")
    if easy is not None and not pd.isna(easy):
        parts.append("Easy Apply" if easy else "Apply on company site")
    return " · ".join(parts)


def fmt_duration(seconds: float) -> str:
    """3 -> '3s', 252 -> '4m 12s', 3725 -> '1h 2m 5s'."""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s" if m else f"{s}s"


class step_timer:
    """`with step_timer("scrape"):` prints '[time] scrape: 4m 12s' when the step
    ends and records it in RUN_STATS["timings"]."""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.start = time.monotonic()
        return self

    def __exit__(self, *exc):
        elapsed = time.monotonic() - self.start
        RUN_STATS.setdefault("timings", {})[self.name] = elapsed
        print(f"[time] {self.name}: {fmt_duration(elapsed)}", flush=True)
        return False


def _run_header() -> str:
    return (f"Run finished {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
            f"job-hunter {__version__} · provider {PROVIDER} · model {MODEL}")


def _scrape_details(site: str) -> str:
    return RUN_STATS.get("source_notes", {}).get(site, "")


def _fmt_time(name: str) -> str:
    elapsed = RUN_STATS.get("timings", {}).get(name)
    return fmt_duration(elapsed) if elapsed is not None else "—"


def site_funnel(site: str, df: pd.DataFrame, top: int, verdicts: list[str]) -> dict:
    """How many of one site's postings made it through each step this run.
    df is that site's rows of the scored table (with passes_threshold)."""
    s = RUN_STATS
    scoring = s.get("scoring_by_source", {}).get(site, {})
    by_verdict = df["verdict"].astype(str).str.lower().value_counts().to_dict() if len(df) else {}
    unique = s.get("unique_by_source", {}).get(site, len(df))
    prefiltered = scoring.get("prefiltered", 0)
    passed = int(df["passes_threshold"].sum()) if len(df) else 0
    country_remote = int((df["passes_threshold"] & df["country_remote"]).sum()) if len(df) else 0
    return {
        "scraped": s.get("rows_by_source", {}).get(site, 0),
        "unique": unique,
        "prefiltered": prefiltered,
        "flagged": scoring.get("flagged", 0),
        "to_score": unique - prefiltered,
        "from_cache": scoring.get("from_cache", 0),
        "llm_calls": scoring.get("llm_calls", 0),
        "apply": by_verdict.get("apply", 0),
        "maybe": by_verdict.get("maybe", 0),
        "skip": by_verdict.get("skip", 0) - prefiltered,  # the LLM's skips; pre-filtered counted above
        "ineligible": int((df["eligibility"] == "fail").sum()) if "eligibility" in df else 0,
        "error": by_verdict.get("error", 0),
        "verdict_ok": sum(by_verdict.get(v, 0) for v in verdicts),
        "passed": passed,
        "shortlisted": min(passed - country_remote, top),
        "country_remote": min(country_remote, top),
    }


def _total_time() -> str:
    total = RUN_STATS.get("timings", {}).get("total_so_far")
    return fmt_duration(total) if total is not None else "?"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def site_paragraph(site: str, f: dict, top: int, verdicts: list[str]) -> str:
    """One plain-English paragraph on what happened to one site's postings."""
    if not f["scraped"]:
        return (f"{site} returned no postings this run ({_scrape_details(site) or 'no details'}), "
                f"so nothing was scored or shortlisted.")
    text = (f"{site} returned {_plural(f['scraped'], 'posting')} in {_fmt_time(f'scrape {site}')}; "
            f"{f['scraped'] - f['unique']} were duplicates, leaving {f['unique']} unique. ")
    if f["prefiltered"]:
        text += (f"The free pre-filter removed {f['prefiltered']}, so {f['to_score']} "
                 f"went to scoring ")
    else:
        text += f"All {f['to_score']} went to scoring "
    text += (f"({f['from_cache']} from cache, {_plural(f['llm_calls'], 'new LLM call')}). "
             f"The LLM rated {f['apply']} apply, {f['maybe']} maybe and {f['skip']} skip"
             + (f" ({f['ineligible']} of the skips failed the eligibility check)" if f["ineligible"] else "")
             + (f", and {f['error']} failed to score" if f["error"] else "") + ". ")
    if not f["verdict_ok"]:
        text += f"With no {'/'.join(verdicts)} verdicts, nothing was shortlisted."
    else:
        text += (f"Of the {f['verdict_ok']} rated {'/'.join(verdicts)}, {f['passed']} "
                 f"cleared the score thresholds")
        if f["country_remote"]:
            text += (f"; {f['country_remote']} of those are remote but listed in one country, "
                     f"so they're listed apart, and {f['shortlisted']} shortlisted.")
        else:
            text += (f", and the top {top} of those were shortlisted." if f["passed"] > top
                     else f", so {f['shortlisted']} {'is' if f['shortlisted'] == 1 else 'are'} shortlisted.")
    return text


def coverage_text(site: str) -> str | None:
    """One line on searches that were cut off or failed for this site, or None
    if every search came back complete (sources without searches: None)."""
    c = RUN_STATS.get("coverage", {}).get(site)
    if not c or not c["searches"]:
        return None
    parts = []
    if c["capped"]:
        parts.append(f"{len(c['capped'])} of {c['searches']} searches hit the results cap, so more "
                     f"postings probably exist ({'; '.join(c['capped'])}). Raise RESULTS_PER_BOARD "
                     "or LOCATION_RESULTS_OVERRIDE for those to see them")
    if c["failed"]:
        parts.append(f"{len(c['failed'])} of {c['searches']} searches failed, so their postings are "
                     f"missing from this run ({'; '.join(c['failed'])})")
    return ". ".join(parts) + "." if parts else f"all {c['searches']} searches came back complete."


# Pre-filter labels that are nearly always right and very noisy: the dropped
# section shows only a count for these instead of every posting.
_COLLAPSED_DROP_LABELS = ("not confirmed remote", "older than", CLOSED_LABEL, NON_ENGLISH_LABEL)


def dropped_lines(df: pd.DataFrame) -> list[str]:
    """Markdown list of one site's postings the pre-filter dropped, grouped by
    label, so a wrong drop can be spotted without opening the CSV."""
    dropped = df[df["reason"] == "filtered out before scoring"]
    if not len(dropped):
        return []
    # Labels with a per-row detail in parentheses, e.g. "saturated Easy Apply
    # (Over 200 applicants)", are grouped without it. Fixed labels that have
    # their own parentheses, e.g. "clearance (TS/SCI)", are kept whole.
    fixed = ({_WIDE_LABEL, LIKELY_ONSITE_LABEL} | {e[0] for e in config.HARD_BLOCKERS}
             | {e[0] for e in getattr(config, "TITLE_BLOCKERS", [])})
    blockers = dropped["blockers"].astype(str)
    groups = blockers.where(blockers.isin(fixed),
                            blockers.str.replace(r"\s*\(.*\)$", "", regex=True))
    lines = ["---", "", f"## Dropped before scoring ({len(dropped)})", "",
             "Removed by the free pre-filter, never sent to the LLM. Skim for anything "
             "that shouldn't have been dropped.", ""]
    for label, rows in dropped.groupby(groups, sort=False):
        if label.startswith(_COLLAPSED_DROP_LABELS):
            lines.append(f"- **{label}**: {len(rows)} (listed in the CSV)")
            continue
        lines.append(f"- **{label}** ({len(rows)})")
        for _, r in rows.iterrows():
            detail = r["blockers"][len(label):].strip()
            lines.append(f"  - [{r['title']}]({r['job_url']}) — "
                         f"{_str_or(r.get('company'), 'company not listed')}"
                         + (f" {detail}" if detail else ""))
    return lines + [""]


def country_remote_lines(df: pd.DataFrame, top: int) -> list[str]:
    """Markdown list of postings that cleared every threshold but are remote
    and listed under one country with nothing saying they hire elsewhere (see
    is_country_remote): kept out of the shortlist, listed here to check."""
    rows = df[df["passes_threshold"] & df["country_remote"]].head(top)
    if not len(rows):
        return []
    lines = ["---", "", f"## Remote, but listed in one country ({len(rows)})", "",
             "Cleared the thresholds, but the posting is listed under a single country and only "
             "says \"fully remote\" or similar. On LinkedIn that usually means remote within that "
             "country (US work authorization on the apply page); some companies hire globally "
             "anyway. Check the apply page or the company's careers page before applying.", ""]
    for _, r in rows.iterrows():
        lines.append(f"- **{r['overall']}** {str(r['verdict']).upper()} · [{r['title']}]({r['job_url']}) — "
                     f"{_str_or(r.get('company'), 'company not listed')} · {shortlist_meta(r)}")
        lines.append(f"  - {r['reason']}")
        if r.get("eligibility_reason"):
            lines.append(f"  - Eligibility {r.get('eligibility')}: {_str_or(r.get('eligibility_reason'), '')}")
    return lines + [""]


def near_miss_lines(df: pd.DataFrame, site: str, verdicts: list[str]) -> list[str]:
    """Markdown list of one site's postings the LLM rated apply/maybe that
    missed a score threshold, with which one, so a good match held back by a
    threshold (e.g. the crowded one) is still seen."""
    missed = df[df["verdict"].astype(str).str.lower().isin(verdicts) & ~df["passes_threshold"]]
    if not len(missed):
        return []
    min_overall, min_stack = min_scores(site)
    lines = ["---", "", f"## Rated {'/'.join(verdicts)} but under a threshold ({len(missed)})", "",
             "Not shortlisted because a score was below its threshold. Skim these too.", ""]
    for _, r in missed.iterrows():
        why = []
        if r["overall"] < min_overall:
            why.append(f"overall {r['overall']} < {min_overall}")
        elif is_crowded(r) and r["overall"] < config.CROWDED_MIN_OVERALL_SCORE:
            why.append(f"crowded ({_str_or(r.get('applicants_text'), str(r.get('applicants')))}): "
                       f"overall {r['overall']} < {config.CROWDED_MIN_OVERALL_SCORE}")
        if r["stack_fit"] < min_stack:
            why.append(f"stack_fit {r['stack_fit']} < {min_stack}")
        lines.append(f"- **{r['overall']}** {str(r['verdict']).upper()} · [{r['title']}]({r['job_url']}) — "
                     f"{_str_or(r.get('company'), 'company not listed')} · {'; '.join(why)}"
                     + (" · check workplace badge" if _flag(r.get("likely_onsite")) else "")
                     + (" · remote, listed in one country" if _flag(r.get("country_remote"))
                        else " · verify location on the apply page" if r.get("eligibility") == "unclear" else ""))
        lines.append(f"  - {r['reason']}")
    return lines + [""]


def run_metrics_lines(site: str, f: dict, top: int, verdicts: list[str]) -> list[str]:
    """Markdown 'Run metrics' section for the bottom of one site's shortlist:
    that site's postings in and out of each step, from scrape to shortlist."""
    dry_run_note = (f"dry run: {f['flagged']} flagged but still scored"
                    if config.PREFILTER_DRY_RUN else "title/blocker/age/applicant rules, no LLM")
    coverage = coverage_text(site)
    lines = ["---", "", f"## Run metrics — {site}", "", _run_header(), "",
             site_paragraph(site, f, top, verdicts), "",
             *([f"**Coverage:** {coverage}", ""] if coverage else []),
             "| Step | Time | In | Out | Details |", "|---|---|---:|---:|---|",
             f"| scrape | {_fmt_time(f'scrape {site}')} | — | {f['scraped']} | {_scrape_details(site)} |",
             f"| dedupe | — | {f['scraped']} | {f['unique']} | "
             f"{f['scraped'] - f['unique']} duplicate(s) dropped (same URL; same title + company, "
             f"ignoring reference codes like REF#123; or same company + identical description) |",
             f"| pre-filter | — | {f['unique']} | {f['to_score']} | "
             f"{f['prefiltered']} filtered out; {dry_run_note} |",
             f"| LLM scoring | {_fmt_time('scoring')} (all boards) | {f['to_score']} | {f['to_score']} | "
             f"{f['from_cache']} from cache, {f['llm_calls']} LLM calls · "
             f"{f['apply']} apply, {f['maybe']} maybe, {f['skip']} skip"
             + (f" ({f['ineligible']} ineligible)" if f["ineligible"] else "")
             + (f", {f['error']} failed" if f["error"] else "") + " |",
             f"| verdict filter | — | {f['to_score']} | {f['verdict_ok']} | "
             f"keeps {'/'.join(verdicts)} only |",
             f"| score thresholds | — | {f['verdict_ok']} | {f['passed']} | "
             f"{thresholds_text(site)} |",
             f"| shortlist | — | {f['passed']} | {f['shortlisted']} | top {top} kept"
             + (f"; {f['country_remote']} remote in one country listed apart" if f["country_remote"] else "")
             + " |",
             f"| **total** | **{_total_time()}** (whole run) | **{f['scraped']}** | "
             f"**{f['shortlisted']}** | {f['scraped']} scraped → {f['shortlisted']} shortlisted |"]
    return lines + [""]


def run_summary_paragraph(funnels: dict[str, dict], verdicts: list[str]) -> str:
    """One plain-English paragraph on the whole run, all boards together."""
    t = {k: sum(f[k] for f in funnels.values()) for k in next(iter(funnels.values()), {})}
    if not t.get("scraped"):
        return (f"This run searched {', '.join(funnels) or 'no boards'} in {_total_time()} "
                f"and found no postings.")
    per_board = ", ".join(f"{site} {f['scraped']}" for site, f in funnels.items())
    text = (f"This run took {_total_time()} and searched {len(funnels)} "
            f"board{'' if len(funnels) == 1 else 's'}, collecting {t['scraped']} postings "
            f"({per_board}). After dedupe {t['unique']} were unique; the pre-filter removed "
            f"{t['prefiltered']} and {t['to_score']} were scored ({t['from_cache']} from cache, "
            f"{_plural(t['llm_calls'], 'new LLM call')}, {_fmt_time('scoring')}). "
            f"The LLM rated {t['apply']} apply, {t['maybe']} maybe and {t['skip']} skip"
            + (f", with {_plural(t['error'], 'failure')}" if t["error"] else "") + ". ")
    if t["shortlisted"]:
        by_board = ", ".join(f"{site} {f['shortlisted']}" for site, f in funnels.items()
                             if f["shortlisted"])
        text += (f"{t['passed']} cleared the score thresholds and {t['shortlisted']} "
                 f"{'was' if t['shortlisted'] == 1 else 'were'} shortlisted ({by_board})"
                 + (f"; {t['country_remote']} remote but listed in one country are listed apart."
                    if t.get("country_remote") else "."))
    else:
        text += "None cleared the score thresholds, so every shortlist is empty."
    return text


def run_summary_lines(funnels: dict[str, dict], top: int, verdicts: list[str]) -> list[str]:
    """Markdown for output/run_summary.md: every site searched this run side
    by side, plus the run's step timings."""
    s = RUN_STATS
    cols = [("scraped", "Scraped"), ("unique", "Unique"), ("prefiltered", "Pre-filtered"),
            ("to_score", "Scored"), ("from_cache", "From cache"), ("llm_calls", "LLM calls"),
            ("apply", "Apply"), ("maybe", "Maybe"), ("skip", "Skip"), ("ineligible", "Ineligible"),
            ("error", "Failed"),
            ("verdict_ok", "/".join(v.capitalize() for v in verdicts)),
            ("passed", "Passed thresholds"), ("shortlisted", "Shortlisted"),
            ("country_remote", "One country")]
    lines = ["# Run summary", "", _run_header(), "",
             run_summary_paragraph(funnels, verdicts), "",
             f"Boards: {', '.join(funnels) or '(none)'} · thresholds: "
             + "; ".join(f"{site} {thresholds_text(site)}" for site in funnels)
             + f" · top {top} per board", "",
             *(["## Coverage", "",
                *[f"- **{site}**: {text}" for site in funnels if (text := coverage_text(site))],
                ""] if any(coverage_text(site) for site in funnels) else []),
             "## Postings through each step", "",
             "| Board | " + " | ".join(label for _, label in cols) + " |",
             "|---|" + "---:|" * len(cols)]
    for site, f in funnels.items():
        lines.append(f"| [{site}](shortlist_{site}.md) | " + " | ".join(str(f[k]) for k, _ in cols) + " |")
    lines.append("| **all boards** | " + " | ".join(
        f"**{sum(f[k] for f in funnels.values())}**" for k, _ in cols) + " |")
    if config.PREFILTER_DRY_RUN:
        lines += ["", "PREFILTER_DRY_RUN is on: flagged postings were still scored, so "
                      "Pre-filtered is 0 — see prefilter_flag in the CSVs."]
    lines += ["", "Unique counts dedupe across boards too: a posting found on two boards "
                  "is kept once, under whichever board returned it first.", "",
              "## Time per step", "", "| Step | Time | Details |", "|---|---|---|"]
    for name, elapsed in s.get("timings", {}).items():
        if name == "total_so_far":
            continue
        details = _scrape_details(name.removeprefix("scrape ")) if name.startswith("scrape ") else (
            f"{s.get('rows_scraped', 0)} rows → {s.get('unique_postings', 0)} unique; "
            f"{s.get('prefiltered', 0)} filtered free, {s.get('from_cache', 0)} from cache, "
            f"{s.get('llm_calls', 0)} LLM calls" if name == "scoring" else "")
        lines.append(f"| {name} | {fmt_duration(elapsed)} | {details} |")
    lines.append(f"| **total** | **{_total_time()}** | |")
    return lines + [""]


def shortlist_lines(df: pd.DataFrame, site: str, top: int, verdicts: list[str],
                    funnel: dict) -> list[str]:
    """Markdown shortlist for one site's rows (already sorted best first)."""
    shortlist = df[df["passes_threshold"] & ~df["country_remote"]].head(top)
    by_verdict = shortlist["verdict"].astype(str).str.lower().value_counts()
    lines = [f"# {site} shortlist — top {len(shortlist)} of {len(df)} postings "
             f"({thresholds_text(site)}; "
             + ", ".join(f"{by_verdict.get(v, 0)} {v}" for v in verdicts) + ")\n"]
    for _, r in shortlist.iterrows():
        lines.append(f"## {r['overall']} · {r['title']} — {_str_or(r.get('company'), 'company not listed')}")
        lines.append(f"*{shortlist_meta(r)}* · [posting]({r['job_url']})")
        if is_crowded(r):
            lines.append(f"**Crowded ({_str_or(r.get('applicants_text'), str(r.get('applicants')))})** "
                         "— shortlisted for a strong match; apply soon and tailor the application.")
        if isinstance(r.get("also_posted"), str) and r["also_posted"]:
            lines.append(f"Also posted as: {r['also_posted']}")
        if _flag(r.get("feed_only")):
            lines.append("**Feed summary only** — jobs.ps blocked the page fetch, so this was "
                         "scored from a short summary. Open the posting to read it in full.")
        if _flag(r.get("likely_onsite")):
            lines.append("**Check workplace badge** — city-level location and no explicit "
                         "fully-remote statement; may be On-site/Hybrid.")
        if r.get("eligibility") == "unclear":
            lines.append(f"**Verify location on the apply page** — "
                         f"{_str_or(r.get('eligibility_reason'), 'the posting does not say where candidates may live')}")
        elif r.get("eligibility") == "pass":
            lines.append(f"Eligibility: pass — {_str_or(r.get('eligibility_reason'), '')}")
        lines.append(f"**{str(r['verdict']).upper()}** — {r['reason']}")
        if r["blockers"]:
            lines.append(f"- Blockers: {r['blockers']}")
        if r["matched"]:
            lines.append(f"- Matches: {r['matched']}")
        if r["gaps"]:
            lines.append(f"- Gaps: {r['gaps']}")
        lines.append("")
    lines += country_remote_lines(df, top)
    lines += near_miss_lines(df, site, verdicts)
    lines += dropped_lines(df)
    lines += run_metrics_lines(site, funnel, top, verdicts)
    return lines


def write_outputs(df: pd.DataFrame, top: int, sites: list[str]) -> None:
    """One jobs_scored_<site>.csv + shortlist_<site>.md per site this run
    scraped (so a single-site run leaves other sites' files alone), plus one
    append to the shared history.csv."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df = df.copy()
    verdicts = [v.lower() for v in getattr(config, "SHORTLIST_VERDICTS", ["apply", "maybe"])]
    min_overall = df["site"].map(lambda site: min_scores(site)[0])
    # Crowded postings need a stronger match (CROWDED_MIN_OVERALL_SCORE).
    crowded = df.apply(is_crowded, axis=1) if len(df) else pd.Series(dtype=bool)
    if crowded.any():
        min_overall = min_overall.where(
            ~crowded, min_overall.clip(lower=config.CROWDED_MIN_OVERALL_SCORE))
    min_stack = df["site"].map(lambda site: min_scores(site)[1])
    df["passes_threshold"] = (
        (df["overall"] >= min_overall)
        & (df["stack_fit"] >= min_stack)
        & df["verdict"].astype(str).str.lower().isin(verdicts)
        & (df["eligibility"] != "fail" if "eligibility" in df else True)
    )
    # Remote, one country, no word on hiring elsewhere: listed apart unless
    # the LLM found the posting open to the candidate ("pass").
    df["country_remote"] = (df.apply(is_country_remote, axis=1).astype(bool) if len(df)
                            else pd.Series(dtype=bool))
    if "eligibility" in df:
        df["country_remote"] &= df["eligibility"] != "pass"

    cols = ["overall", "passes_threshold", "verdict", "eligibility", "eligibility_reason", "title", "company", "site",
            "location", "search_location", "employment_type", "seniority_level", "date_posted",
            "days_old", "applicants", "applicants_text", "easy_apply", "is_remote",
            "likely_onsite", "country_remote", "feed_only", "also_posted", "reason", "blockers", "prefilter_flag", "matched", "gaps",
            "stack_fit", "seniority_fit", "domain_fit", "logistics_fit", "job_url"]
    cols = [c for c in cols if c in df.columns]
    history_path = append_history(df, cols) if len(df) else None

    print(f"\n[done] {df['verdict'].value_counts().to_dict()}")
    site_col = df["site"].astype(str).str.lower()
    funnels: dict[str, dict] = {}
    for site in sites:
        site_df = df[site_col == site]
        funnels[site] = site_funnel(site, site_df, top, verdicts)
        csv_path = OUT_DIR / f"jobs_scored_{site}.csv"
        md_path = OUT_DIR / f"shortlist_{site}.md"
        # Written even when the site returned nothing, so the file never shows
        # a previous run's postings as if they were current.
        site_df[cols].to_csv(csv_path, index=False, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
        md_path.write_text("\n".join(shortlist_lines(site_df, site, top, verdicts, funnels[site])),
                           encoding="utf-8")
        print(f"       {site}: {len(site_df)} postings, {funnels[site]['shortlisted']} shortlisted "
              + (f"(+{funnels[site]['country_remote']} remote in one country) " if funnels[site]["country_remote"] else "")
              + f"→ {md_path}, {csv_path}")
    summary_path = OUT_DIR / "run_summary.md"
    summary_path.write_text("\n".join(run_summary_lines(funnels, top, verdicts)), encoding="utf-8")
    print(f"       run summary → {summary_path}")
    if history_path:
        print(f"       {history_path} (appended, {len(df)} rows this run)")


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", help="resume file (.md/.txt/.pdf/.docx); "
                                     "falls back to config.CANDIDATE_PROFILE if omitted")
    ap.add_argument("--constraints", help="text file describing your hard requirements; "
                                          "falls back to config.CONSTRAINTS if omitted")
    ap.add_argument("--terms", nargs="+",
                    default=config.SEARCH_TERMS + getattr(config, "WIDE_SEARCH_TERMS", []))
    ap.add_argument("--locations", nargs="+", default=None,
                    help='locations for every site in this run, e.g. "United States" MENA '
                         '(LinkedIn/Indeed) or gaza-jobs (jobs.ps); pair with --sites. '
                         'Defaults to config.SITE_LOCATIONS per site')
    ap.add_argument("--remote", action=argparse.BooleanOptionalAction, default=None,
                    help="remote-only for every site in this run; defaults to "
                         "config.SITE_REMOTE_ONLY per site, else config.IS_REMOTE")
    ap.add_argument("--hours", type=int, default=config.HOURS_OLD, help="max posting age in hours")
    ap.add_argument("--results", type=int, default=config.RESULTS_PER_BOARD,
                    help="results per site per term")
    ap.add_argument("--top", type=int, default=config.TOP_N_REPORT,
                    help="how many to put in the report")
    ap.add_argument("--workers", type=int, default=None,
                    help="parallel scoring calls; defaults to config.WORKERS "
                         "(config.GEMINI_WORKERS when scoring with Gemini)")
    ap.add_argument("--delay", type=float, default=config.SEARCH_DELAY_SECONDS,
                    help="seconds between search terms")
    ap.add_argument("--proxies", nargs="*", default=[], help="user:pass@host:port …")
    ap.add_argument("--sites", nargs="+", choices=list(REGISTRY),
                    default=getattr(config, "SOURCES", list(REGISTRY)),
                    help="which job sites to search this run; defaults to config.SOURCES")
    ap.add_argument("--provider", choices=["openrouter", "gemini"], default=None,
                    help="which API scores postings for this run only; overrides "
                         "LLM_PROVIDER in .env and config.LLM_PROVIDER")
    ap.add_argument("--dry-run", action="store_true",
                    help="test run: write shortlists/CSVs to <OUTPUT_DIR>/dry_run/ and don't "
                         "append to history.csv (the score cache is still used and updated)")
    args = ap.parse_args()

    global PROVIDER, MODEL, DRY_RUN, OUT_DIR
    if args.provider:
        PROVIDER, MODEL = resolve_provider_and_model(args.provider)
    if args.dry_run:
        DRY_RUN = True
        OUT_DIR = OUT_DIR / "dry_run"
        print(f"[config] dry run: outputs → {OUT_DIR}/, history.csv not touched")
    site_remote = getattr(config, "SITE_REMOTE_ONLY", {})
    SITE_REMOTE_ONLY.update({site: args.remote if args.remote is not None
                             else site_remote.get(site, config.IS_REMOTE)
                             for site in args.sites})
    args.site_remote_only = SITE_REMOTE_ONLY
    # Each site's locations: --locations for all of them if passed, else that
    # site's entry in config.SITE_LOCATIONS, else config.LOCATIONS.
    site_defaults = getattr(config, "SITE_LOCATIONS", {})
    args.site_locations = {site: args.locations or site_defaults.get(site, config.LOCATIONS)
                           for site in args.sites}

    if args.workers is None:
        # Gemini's free tier enforces a per-minute request cap far below
        # OpenRouter's — config.WORKERS (tuned for OpenRouter) would blast
        # through it in one batch and immediately 429 every call. Only used
        # when --workers wasn't passed explicitly, so a manual override
        # always wins.
        args.workers = getattr(config, "GEMINI_WORKERS", 1) if PROVIDER == "gemini" else config.WORKERS

    if PROVIDER == "openrouter" and not os.getenv("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY is not set.")
    if PROVIDER == "gemini" and not os.getenv("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is not set (required when --provider/LLM_PROVIDER=gemini).")
    print(f"[config] job-hunter {__version__} · provider={PROVIDER}, model={MODEL}")

    resume = load_resume(args.resume)
    if not args.resume:
        print("[warn] no --resume passed, using config.CANDIDATE_PROFILE (a short summary) — "
              "scores will be less accurate than with a full resume", file=sys.stderr)
    constraints = load_constraints(args.constraints)
    if constraints.lstrip().startswith("EXAMPLE"):
        print("[warn] using the EXAMPLE constraints from config.py — put your real "
              "CONSTRAINTS in config_local.py (see README)", file=sys.stderr)
    run_start = time.monotonic()
    for site in args.sites:
        print(f"[config] {site}: {', '.join(args.site_locations[site]) or '(no locations)'}"
              f"{' · remote only' if SITE_REMOTE_ONLY[site] else ' · on-site allowed'}")
    jobs = fetch_jobs(args)  # times each site as "scrape <site>"
    if jobs.empty:
        # Still rewrite each site's files (empty), so they don't keep showing
        # the previous run's postings as current.
        print("[scrape] no postings returned. Try a longer --hours window, other "
              "locations/terms, or proxies.", file=sys.stderr)
        empty = jobs.reindex(columns=list(jobs.columns) + [
            "overall", "stack_fit", "verdict", "reason", "blockers", "matched", "gaps"])
        RUN_STATS.setdefault("timings", {})["total_so_far"] = time.monotonic() - run_start
        write_outputs(empty, args.top, args.sites)
        return
    with step_timer("scoring"):
        scored = score_all(jobs, resume, constraints, args.workers)
    # Total is recorded before writing so it can go into the shortlists; writing
    # the files takes well under a second.
    RUN_STATS.setdefault("timings", {})["total_so_far"] = time.monotonic() - run_start
    write_outputs(scored, args.top, args.sites)
    print(f"[time] total: {fmt_duration(time.monotonic() - run_start)}")


if __name__ == "__main__":
    main()