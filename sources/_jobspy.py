"""JobSpy search loop shared by the LinkedIn and Indeed sources."""

from __future__ import annotations

import sys
import time

import pandas as pd
from jobspy import scrape_jobs
from jobspy.model import Country

import config

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
    n_searches = 0
    for i, (loc, term) in enumerate(combos, 1):
        country = jobspy_country(loc)
        results_wanted = overrides.get(loc, args.results)
        note = f" (results capped at {results_wanted} for {loc})" if loc in overrides else ""
        if countries_only and not country:
            print(f"[{site}] ({i}/{len(combos)}) {term!r} in {loc!r} skipped: not a supported country")
            continue
        print(f"[{site}] ({i}/{len(combos)}) {term!r} in {loc!r}{note} …", flush=True)
        n_searches += 1
        try:
            df = scrape_jobs(
                site_name=[site],
                search_term=term,
                location=loc,
                is_remote=remote_only,
                results_wanted=results_wanted,
                hours_old=args.hours,
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
            continue
        if df is not None and len(df):
            df["search_term"] = term
            df["search_location"] = loc
            frames.append(df.dropna(axis=1, how="all"))
        time.sleep(args.delay)  # be polite; reduces 429s
    jobs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return jobs, n_searches
