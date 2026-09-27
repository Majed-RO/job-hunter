"""
LinkedIn: search via JobSpy, then one guest (logged-out) job page per posting.

JobSpy downloads each LinkedIn job page for its description but ignores the
rest of the page, and its output has a fixed column list, so extra fields
can't be added through it. Instead JobSpy's LinkedIn description fetch is
turned off and this module downloads the same public page itself — still one
request per posting — and keeps: description, applicant count, employment
type, seniority, post age and Easy Apply.

Verified 2026-09-25 against 9 real pages (output/guest_probe/). NOT on the
guest page, only when logged in: the On-site/Remote/Hybrid badge and
"Promoted by hirer". likely_onsite below is a heuristic stand-in for the badge.
"""

from __future__ import annotations

import random
import re
import sys
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup
from jobspy.linkedin import LinkedIn
from jobspy.model import Location
from jobspy.util import markdown_converter  # same HTML->markdown JobSpy used for descriptions

import config
from sources import _jobspy
from sources.common import RUN_STATS, USER_AGENT, fetch_with_retry, note, text_of, title_blocked

SITE = "linkedin"

# --------------------------------------------------------------------------- #
# JobSpy patch: LinkedIn location parsing
# --------------------------------------------------------------------------- #
# JobSpy maps every LinkedIn result's country to a fixed internal list. When a
# job is in a country missing from that list (Algeria, Jordan, Palestine,
# Lebanon, Iraq, Tunisia, ...), it raises ValueError and discards the ENTIRE
# LinkedIn search for that term — common with region searches like "MENA".
# This wrapper keeps the country name as LinkedIn displayed it instead of
# crashing. Supported countries go through JobSpy's original code unchanged.
# Applied on import of this module.
_orig_get_location = LinkedIn._get_location


def _safe_get_location(self, metadata_card):
    try:
        return _orig_get_location(self, metadata_card)
    except ValueError:
        tag = metadata_card.find("span", class_="job-search-card__location") if metadata_card else None
        parts = tag.text.strip().split(", ") if tag else []
        if len(parts) == 3:
            return Location(city=parts[0], state=parts[1], country=parts[2])
        return Location(city=", ".join(parts) or None)


LinkedIn._get_location = _safe_get_location


# --------------------------------------------------------------------------- #
# Guest job pages
# --------------------------------------------------------------------------- #
GUEST_POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
GUEST_HEADERS = {
    "user-agent": USER_AGENT,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
}
LINKEDIN_FIELDS = ("applicants", "applicants_text", "employment_type", "seniority_level",
                   "days_old", "easy_apply", "likely_onsite", "li_location")

_LI_JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d{6,})")
_APPLICANTS_RE = re.compile(r"(?:over\s+)?([\d,]+)\s+applicants?", re.I)
_FIRST_APPLICANTS_RE = re.compile(r"among the first\s+([\d,]+)\s+applicants", re.I)
_POSTED_AGO_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.I)
_DAYS_PER_UNIT = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}
_REMOTE_KEYWORDS = ("remote", "work from home", "wfh")  # same list JobSpy uses

# "likely onsite" heuristic. In the probe, postings with a city-level location
# ("Cairo, Egypt", "San Francisco, CA") were the onsite/unclear ones, while
# genuinely remote ones listed a country ("United States"). A city location is
# excused only when the text states remote unambiguously — "Remote/Hybrid" or
# "remote or on-site" (the Living Stones case, whose badge said On-site) is not.
_STRONG_REMOTE_RE = re.compile(
    r"\b(?:fully|100\s*%|completely|entirely|permanently)\s+remote\b"
    r"|\bremote[- ]first\b|\bwork\s+from\s+anywhere\b|\bremote,?\s+anywhere\b"
    r"|\bfully\s+distributed\b|\banywhere\s+in\s+the\s+world\b"
    r"|\bremote\s*\(\s*(?:global|worldwide|anywhere)", re.I)
_CITY_WORDS_RE = re.compile(r"\b(?:area|metropolitan|metroplex|metro|greater)\b", re.I)


def parse_applicants(caption: str | None) -> int | None:
    """"91 applicants" -> 91, "Over 200 applicants" -> 200,
    "Be among the first 25 applicants" -> 0 (fewer than 25 so far)."""
    if not caption:
        return None
    if _FIRST_APPLICANTS_RE.search(caption):
        return 0
    m = _APPLICANTS_RE.search(caption)
    return int(m.group(1).replace(",", "")) if m else None


def parse_days_old(posted: str | None) -> int | None:
    """"13 hours ago" -> 0, "2 days ago" -> 2, "1 week ago" -> 7, "5 months ago" -> 150."""
    m = _POSTED_AGO_RE.search(posted or "")
    return int(m.group(1)) * _DAYS_PER_UNIT[m.group(2).lower()] if m else None


def is_likely_onsite(li_location: str | None, text: str) -> bool:
    loc = (li_location or "").strip()
    if not loc or "remote" in loc.lower():
        return False
    city_level = "," in loc or bool(_CITY_WORDS_RE.search(loc))
    return city_level and not _STRONG_REMOTE_RE.search(text)


def parse_guest_posting(html: str) -> dict:
    """Pull every field we use out of one LinkedIn guest job page."""
    soup = BeautifulSoup(html, "html.parser")
    info: dict = {}

    desc_el = soup.select_one(".show-more-less-html__markup")
    info["description"] = markdown_converter(str(desc_el)) if desc_el else None

    info["li_location"] = text_of(soup.select_one(".topcard__flavor--bullet"))
    caption = text_of(soup.select_one(".num-applicants__caption"))
    info["applicants_text"] = caption
    info["applicants"] = parse_applicants(caption)
    info["days_old"] = parse_days_old(text_of(soup.select_one(".posted-time-ago__text")))

    criteria = {}
    for item in soup.select(".description__job-criteria-item"):
        key = text_of(item.select_one(".description__job-criteria-subheader"))
        if key:
            criteria[key.lower()] = text_of(item.select_one(".description__job-criteria-text"))
    info["employment_type"] = criteria.get("employment type")
    info["seniority_level"] = criteria.get("seniority level")

    # Apply button: links back into LinkedIn ("apply-link-onsite" — Easy Apply)
    # or out to the company's site ("apply-link-offsite"). "onsite" here is
    # about WHERE you apply, not the workplace.
    tracking = " ".join(el.get("data-tracking-control-name", "")
                        for el in soup.select("[data-tracking-control-name*=apply-link]"))
    info["easy_apply"] = (True if "apply-link-onsite" in tracking
                          else False if "apply-link-offsite" in tracking else None)
    return info


def enrich(jobs: pd.DataFrame, proxies: list[str] | None = None) -> pd.DataFrame:
    """Fetch each posting's guest page and fill in the extra columns."""
    jobs = jobs.copy()
    for col in LINKEDIN_FIELDS:
        jobs[col] = pd.Series([None] * len(jobs), index=jobs.index, dtype=object)
    if "description" not in jobs.columns:
        jobs["description"] = None

    todo = [(idx, row) for idx, row in jobs.iterrows()
            if not title_blocked(row.get("title") or "")]
    n_skipped = len(jobs) - len(todo)
    print(f"[linkedin] fetching {len(todo)} job page(s)"
          + (f" ({n_skipped} skipped: blocked by title)" if n_skipped else "") + " …", flush=True)

    session = requests.Session()
    session.headers.update(GUEST_HEADERS)
    if proxies:
        proxy = proxies[0] if "://" in proxies[0] else f"http://{proxies[0]}"
        session.proxies.update({"http": proxy, "https": proxy})

    delay = getattr(config, "LINKEDIN_DETAIL_DELAY_SECONDS", 1.0)
    max_failures = getattr(config, "LINKEDIN_DETAIL_MAX_FAILURES", 5)
    ok = failed = in_a_row = 0
    for n, (idx, row) in enumerate(todo, 1):
        m = _LI_JOB_ID_RE.search(str(row.get("job_url") or ""))
        html = fetch_with_retry(session, GUEST_POSTING_URL.format(m.group(1))) if m else None
        if html is None:
            failed += 1
            in_a_row += 1
            if in_a_row >= max_failures:
                print(f"  ! {in_a_row} failures in a row — LinkedIn is likely throttling; "
                      f"skipping the remaining {len(todo) - n} page(s)", file=sys.stderr)
                break
        else:
            in_a_row = 0
            ok += 1
            info = parse_guest_posting(html)
            for key, value in info.items():
                jobs.at[idx, key] = value
            text = f"{row.get('title') or ''} {info.get('description') or ''}"
            jobs.at[idx, "likely_onsite"] = is_likely_onsite(info.get("li_location"), text)
        if n % 20 == 0 or n == len(todo):
            print(f"  fetched {n}/{len(todo)}", flush=True)
        time.sleep(delay + random.uniform(0, delay / 2))
    if todo:
        print(f"[linkedin] {ok} ok, {failed} failed")
    RUN_STATS.update(linkedin_pages_ok=ok, linkedin_pages_failed=failed)

    # With its description fetch off, JobSpy computed is_remote from title +
    # location only — recompute it with the description, using JobSpy's own
    # keyword list. No description -> unknown (None), so the remote backstop
    # in prefilter() doesn't fire on a failed fetch.
    jobs["is_remote"] = jobs["is_remote"].astype(object) if "is_remote" in jobs.columns else None
    for idx in jobs.index:
        desc = jobs.at[idx, "description"]
        if not isinstance(desc, str) or not desc:
            jobs.at[idx, "is_remote"] = None
            continue
        blob = f"{jobs.at[idx, 'title']} {desc} {jobs.at[idx, 'li_location'] or jobs.at[idx, 'location']}".lower()
        jobs.at[idx, "is_remote"] = any(k in blob for k in _REMOTE_KEYWORDS)
    return jobs


def fetch(args) -> pd.DataFrame:
    # linkedin_fetch_description=False: job pages are fetched by enrich()
    # instead, which reads more fields from the same page.
    jobs, n_searches = _jobspy.search(args, SITE, args.site_remote_only[SITE],
                                      linkedin_fetch_description=False)
    RUN_STATS["searches"] = RUN_STATS.get("searches", 0) + n_searches
    if not len(jobs):
        note(SITE, f"{n_searches} search{'' if n_searches == 1 else 'es'}")
        return jobs
    # Same posting from several searches: fetch its page once.
    jobs = jobs.drop_duplicates(subset=["job_url"]).reset_index(drop=True)
    jobs = enrich(jobs, args.proxies)
    note(SITE, f"{n_searches} search{'' if n_searches == 1 else 'es'}; "
               f"{RUN_STATS['linkedin_pages_ok']} pages fetched, "
               f"{RUN_STATS['linkedin_pages_failed']} failed")
    return jobs
