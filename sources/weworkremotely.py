"""
We Work Remotely (weworkremotely.com) — remote-only job board.

Public RSS feed per category (e.g. .../categories/remote-full-stack-programming-jobs.rss).
Each item already carries the full description, the region the job is open
to ("Anywhere in the World", "North America Only", ...) and the job type, so
no per-posting page fetch is needed: one request per category.

- Categories to read: config.WWR_CATEGORIES (slugs from the category page
  URLs on weworkremotely.com).
- Locations for this site (config.SITE_LOCATIONS["weworkremotely"] or
  --locations) filter on the region: a posting is kept when its region
  contains one of them, case-insensitively. Empty list = keep every region.
- Postings older than --hours / config.HOURS_OLD are dropped here, like
  JobSpy's hours_old for LinkedIn/Indeed.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

import config
from sources.common import RUN_STATS, USER_AGENT, fill_generic_fields, note

SITE = "weworkremotely"

WWR_RSS_URL = "https://weworkremotely.com/categories/{}.rss"
WWR_HEADERS = {
    "user-agent": USER_AGENT,
    "accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_rss(category: str) -> list[dict]:
    """One category feed -> list of raw item dicts (tag -> text)."""
    url = WWR_RSS_URL.format(category)
    try:
        resp = requests.get(url, headers=WWR_HEADERS, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  ! weworkremotely feed {category!r} failed: {exc}", file=sys.stderr)
        return []
    return [{child.tag: (child.text or "").strip() for child in item}
            for item in root.findall(".//item")]


def _posted_at(item: dict) -> datetime | None:
    try:
        return parsedate_to_datetime(item.get("pubDate") or "")
    except (TypeError, ValueError):
        return None


def to_row(item: dict, category: str) -> dict:
    # Titles are "Company: Job title".
    company, sep, title = (item.get("title") or "").partition(": ")
    if not sep:
        company, title = None, item.get("title")
    posted = _posted_at(item)
    desc_html = item.get("description") or ""
    description = BeautifulSoup(desc_html, "html.parser").get_text("\n", strip=True) if desc_html else None
    return {
        "title": title,
        "company": company,
        "location": item.get("region") or item.get("country") or None,
        "description": description,
        "job_url": item.get("link") or item.get("guid"),
        "job_type": item.get("type") or None,
        "date_posted": posted.date() if posted else None,
        "min_amount": None,
        "max_amount": None,
        "interval": None,
        "site": SITE,
        "is_remote": True,  # the whole board is remote-only
        "search_term": category,
        "search_location": item.get("region") or None,
    }


def fetch(args) -> pd.DataFrame:
    categories = list(getattr(config, "WWR_CATEGORIES", []))
    if not categories:
        note(SITE, "no categories configured")
        return pd.DataFrame()
    regions = [r.lower() for r in args.site_locations.get(SITE, [])]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    rows, seen = [], set()
    n_items = off_region = too_old = 0
    for category in categories:
        print(f"[weworkremotely] fetching RSS: {category} …", flush=True)
        for item in fetch_rss(category):
            n_items += 1
            link = item.get("link") or item.get("guid")
            if not link or link in seen:
                continue
            seen.add(link)
            region = (item.get("region") or "").lower()
            if regions and not any(r in region for r in regions):
                off_region += 1
                continue
            posted = _posted_at(item)
            if posted and posted < cutoff:
                too_old += 1
                continue
            rows.append(to_row(item, category))

    dropped = []
    if off_region:
        dropped.append(f"{off_region} outside {', '.join(args.site_locations.get(SITE, []))}")
    if too_old:
        dropped.append(f"{too_old} older than {args.hours}h")
    summary = f"{len(rows)} kept of {n_items}" + (f" ({'; '.join(dropped)})" if dropped else "")
    print(f"[weworkremotely] {summary}")
    RUN_STATS.update(wwr_items=n_items)
    note(SITE, f"{len(categories)} feed(s); {summary}")
    return fill_generic_fields(pd.DataFrame(rows)) if rows else pd.DataFrame()
