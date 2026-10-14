"""JobSpy search loop shared by the LinkedIn and Indeed sources."""

from __future__ import annotations

import math
import sys
import time

import pandas as pd
from jobspy import scrape_jobs
from jobspy.model import Country

import config
from sources.common import RUN_STATS, days_since

# JobSpy lists these as "countries", but they aren't real Indeed markets:
# Indeed has no worldwide or combined US/Canada search.
PSEUDO_COUNTRIES = {Country.WORLDWIDE, Country.US_CANADA}


def jobspy_country(location: str) -> str | None:
    """Return the location if Indeed can search it as a country, else None.

    None means LinkedIn only: regions ("MENA"), "Worldwide", and countries
    Indeed doesn't cover ("Palestine").
    """
    try:
        country = Country.from_string(location)
    except ValueError:
        return None
    return None if country in PSEUDO_COUNTRIES else location


def search(args, site: str, remote_only: bool, countries_only: bool = False,
           **extra) -> tuple[pd.DataFrame, int]:
    """Run one JobSpy search per (location, term) for a single site.

    remote_only: ask the site for remote postings only.
    countries_only: skip locations Indeed can't search as a country.
    Returns (all rows, number of searches run).
    """
    frames = []
    combos = [(loc, term) for loc in args.site_locations.get(site, []) for term in args.terms]
    overrides = getattr(config, "LOCATION_RESULTS_OVERRIDE", {})
    wide_terms = set(getattr(config, "WIDE_SEARCH_TERMS", []))
    n_searches = 0
    # Searches that came back full (more postings probably exist past the cap)
    # or failed outright, for the Coverage line in the reports.
    coverage = RUN_STATS.setdefault("coverage", {}).setdefault(
        site, {"searches": 0, "capped": [], "failed": []})
    # JobSpy's Indeed query takes either a date filter or the remote filter,
    # never both (hours_old wins). Neither alone finds everything:
    # - date filter: fresh postings, about half onsite (the pre-filter drops
    #   those free, on JobSpy's is_remote). Its remote half still beat the
    #   remote filter on a test (Next.js developer, US: 6 fresh remote vs 1).
    # - remote filter: newest first, but mostly older than HOURS_OLD (dropped
    #   here). Used alone from 2026-10-08, runs kept 2-7 Indeed postings
    #   (about 60 before) and shortlisted none.
    # So a remote-only Indeed search runs both queries and merges them.
    both = site == "indeed" and remote_only and args.hours
    max_days = math.ceil(args.hours / 24) if both else None
    too_old = 0
    for i, (loc, term) in enumerate(combos, 1):
        country = jobspy_country(loc)
        if term in wide_terms:
            results_wanted = config.WIDE_RESULTS_PER_BOARD
            note = f" (wide term, results capped at {results_wanted})"
        else:
            results_wanted = overrides.get(loc, args.results)
            note = f" (results capped at {results_wanted} for {loc})" if loc in overrides else ""
        if countries_only and not country:
            print(f"[{site}] ({i}/{len(combos)}) {term!r} in {loc!r} skipped: not a supported country")
            continue
        print(f"[{site}] ({i}/{len(combos)}) {term!r} in {loc!r}{note} …", flush=True)
        n_searches += 1
        # (remote filter, hours_old, check age afterwards) per query
        queries = ([(False, args.hours, False), (True, None, True)] if both
                   else [(remote_only, args.hours, False)])
        parts, capped, failed = [], False, None
        for is_remote, hours_old, age_after in queries:
            try:
                df = scrape_jobs(
                    site_name=[site],
                    search_term=term,
                    location=loc,
                    is_remote=is_remote,
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    # For regions like "MENA", pass "worldwide": JobSpy otherwise defaults
                    # to USA and tags some LinkedIn locations as "..., USA".
                    country_indeed=country or "worldwide",
                    description_format="markdown",
                    proxies=args.proxies or None,
                    verbose=0,
                    **extra,
                )
            except Exception as exc:  # a 429 on one search shouldn't kill the run
                print(f"  ! failed: {exc}", file=sys.stderr)
                failed = exc
                continue
            if df is None or not len(df):
                continue
            if age_after:
                ages = df["date_posted"].map(days_since)
                fresh = ages.isna() | (ages <= max_days)
                too_old += int((~fresh).sum())
                df = df[fresh]
            capped = capped or len(df) >= results_wanted
            parts.append(df.dropna(axis=1, how="all"))
        coverage["searches"] += 1
        if failed is not None:
            coverage["failed"].append(f"{term} · {loc}" + (" (1 of 2 queries)" if parts else ""))
            if not parts:
                continue
        df = (pd.concat(parts, ignore_index=True).drop_duplicates("job_url")
              if parts else None)
        if capped:
            coverage["capped"].append(f"{term} · {loc} ({results_wanted})")
        if df is not None and len(df):
            df["search_term"] = term
            df["search_location"] = loc
            frames.append(df.dropna(axis=1, how="all"))
        time.sleep(args.delay)  # be polite; reduces 429s
    jobs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if too_old:
        print(f"[{site}] dropped {too_old} postings older than {max_days} days "
              "(remote-filter query, which has no date filter)")
    for kind, what in (("capped", "hit the results cap — more postings probably exist"),
                       ("failed", "failed — their postings are missing from this run")):
        if coverage[kind]:
            print(f"[warn] {site}: {len(coverage[kind])} of {coverage['searches']} searches {what}: "
                  + "; ".join(coverage[kind]), file=sys.stderr)
    return jobs, n_searches
