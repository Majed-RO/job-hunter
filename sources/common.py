"""Helpers shared by job_match.py and every source module."""

from __future__ import annotations

import random
import re
import time
from datetime import date

import pandas as pd
import requests

import config

# Per-run counts and step durations, filled in as the run goes; printed at
# the end and written as a "Run metrics" section at the bottom of each shortlist.
RUN_STATS: dict = {}

# Columns every source returns (missing ones are added as None by job_match).
STANDARD_COLUMNS = ("title", "company", "location", "description", "job_url", "job_type",
                    "date_posted", "min_amount", "max_amount", "interval", "site", "is_remote")

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")

# Title-only blockers (e.g. junior/intern) — matched against title alone so a
# word like "graduate" or "junior" inside a description doesn't false-positive.
_COMPILED_TITLE_BLOCKERS = [
    (entry[0], re.compile(entry[1], re.I), re.compile(entry[2], re.I) if len(entry) > 2 else None)
    for entry in getattr(config, "TITLE_BLOCKERS", [])
]


def title_blocked(title: str) -> str | None:
    for label, pattern, exception in _COMPILED_TITLE_BLOCKERS:
        if pattern.search(title) and not (exception and exception.search(title)):
            return label
    return None


def text_of(el) -> str | None:
    """Whitespace-normalized text of a BeautifulSoup element, or None."""
    return " ".join(el.get_text(" ", strip=True).split()) if el else None


def fetch_with_retry(session: requests.Session, url: str) -> str | None:
    """One page; a single retry after a pause on a 429/999/5xx."""
    for attempt in range(2):
        try:
            resp = session.get(url, timeout=15)
        except requests.RequestException:
            resp = None
        if resp is not None and resp.ok:
            return resp.text
        if resp is not None and resp.status_code not in (429, 999) and resp.status_code < 500:
            return None  # e.g. 404: posting removed; retrying won't help
        if attempt == 0:
            time.sleep(10 + random.uniform(0, 5))
    return None


def days_since(posted) -> int | None:
    if posted is None or pd.isna(posted):
        return None
    try:
        posted_date = posted if isinstance(posted, date) else pd.to_datetime(posted).date()
        return (date.today() - posted_date).days
    except (TypeError, ValueError):
        return None


def fill_generic_fields(jobs: pd.DataFrame) -> pd.DataFrame:
    """employment_type / days_old from the standard job_type / date_posted
    columns, for sources without a richer per-page equivalent (LinkedIn fills
    these from its own job pages instead)."""
    jobs = jobs.copy()
    for col in ("employment_type", "days_old"):
        jobs[col] = pd.Series([None] * len(jobs), index=jobs.index, dtype=object)
    for idx in jobs.index:
        job_type = jobs.at[idx, "job_type"] if "job_type" in jobs.columns else None
        if isinstance(job_type, (list, tuple)):
            job_type = ", ".join(str(j) for j in job_type)
        jobs.at[idx, "employment_type"] = job_type if isinstance(job_type, str) and job_type else None
        jobs.at[idx, "days_old"] = days_since(jobs.at[idx, "date_posted"])
    return jobs


def note(site: str, text: str) -> None:
    """Record a one-line summary for this source's row in the run metrics."""
    RUN_STATS.setdefault("source_notes", {})[site] = text
