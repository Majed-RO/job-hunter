"""
jobs.ps (Palestine local job board).

jobs.ps has no search API and isn't a JobSpy-supported site. It has public
RSS feeds per location (https://www.jobs.ps/rss/jobs/gaza-jobs) and per
category (https://www.jobs.ps/rss/jobs/it-jobs) — title/link/pubDate only,
description truncated — so each posting's page is fetched once for the
fields the feed doesn't carry: full description, location, workplace type
(office/remote/field), category and deadline.

Which feeds are read:
- Locations set for jobsps (config.SITE_LOCATIONS["jobsps"] or --locations):
  read those location feeds, then keep only postings whose page category is
  in config.JOBSPS_CATEGORIES (empty list = keep every category).
- No locations: read the config.JOBSPS_CATEGORIES feeds directly.
Location slugs: https://www.jobs.ps/locations; category slugs:
https://www.jobs.ps/categories (the URL's last path segment).
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup

import config
from sources.common import RUN_STATS, USER_AGENT, fetch_with_retry, fill_generic_fields, note, text_of

SITE = "jobsps"

JOBSPS_RSS_URL = "https://www.jobs.ps/rss/jobs/{}"
JOBSPS_HEADERS = {
    "user-agent": USER_AGENT,
    "accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
}
# Arabic label -> our field name, as used in the details list on every
# jobs.ps posting page (verified 2026-09-27 against a live posting).
JOBSPS_LABELS = {
    "التصنيف": "jp_category_name",
    "المكان": "jp_location",
    "طبيعة العمل": "jp_work_nature",   # "مكتبي أو ميداني (مقر العمل)" vs "العمل عن بعد"
    "نوع الوظيفة": "jp_job_type",
    "المستوى المهني": "jp_career_level",
    "الخبرة": "jp_experience",
    "آخر موعد للتقديم": "jp_deadline",
    "الراتب": "jp_salary",
    "الشركة": "jp_company",
}
# "عن بعد" = "remote" — this is what طبيعة العمل (workplace type) says for a
# remote posting; anything else in that field (office/field/hybrid wording)
# is treated as not-remote.
_JOBSPS_REMOTE_RE = re.compile(r"عن\s*بعد")
# Location/category feed slugs look like "gaza-jobs", "it-jobs",
# "AI-Big-Data-jobs". Anything else (e.g. "MENA" from a shared --locations
# list) isn't a jobs.ps location and is skipped.
_SLUG_RE = re.compile(r"^[A-Za-z0-9-]+-jobs$")


# Which postings each feed returned last run, to detect a feed that
# overflowed between runs (see check_overflow).
FEED_STATE_PATH = Path(config.CACHE_PATH).parent / "jobsps_feeds.json"


def check_overflow(feed_items: dict[str, list[dict]]) -> list[str]:
    """Warn about feeds that may have dropped postings since the last run.

    A feed only ever lists its newest ~15 postings, with no paging. If none
    of this run's postings were in the feed last run, more postings arrived
    in between than the feed holds, so some were never seen. Returns the
    affected feed names and saves this run's postings for the next check.
    """
    try:
        state = json.loads(FEED_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    overflowed = []
    for feed, items in feed_items.items():
        urls = [i["job_url"] for i in items]
        prev = state.get(feed)
        if prev and urls and not set(urls) & set(prev["urls"]):
            overflowed.append(feed)
            oldest = min((i["date_posted"] or "?")[:10] for i in items)
            print(f"  ! jobs.ps feed {feed!r} overflowed: none of its {len(urls)} postings were "
                  f"in it at the last run ({prev['run']}). Postings from between then and "
                  f"{oldest} were probably missed — check https://www.jobs.ps/locations/{feed} "
                  "by hand, and run more often.", file=sys.stderr)
        if urls:  # a failed/empty fetch keeps the previous state
            state[feed] = {"run": datetime.now().strftime("%Y-%m-%d %H:%M"), "urls": urls}
    FEED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEED_STATE_PATH.write_text(json.dumps(state, indent=0))
    return overflowed


def _slug_key(slug: str) -> str:
    """Comparable form of a category slug. jobs.ps's own links aren't always
    clean: Digital Marketing's is "%20Digital-Marketing-jobs" (a stray
    leading space), which should still match "Digital-Marketing-jobs"."""
    return unquote(slug).strip().lower()


def fetch_rss(slug: str) -> list[dict]:
    """One location or category RSS feed -> list of {title, job_url, date_posted}."""
    url = JOBSPS_RSS_URL.format(slug)
    try:
        resp = requests.get(url, headers=JOBSPS_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  ! jobs.ps feed {slug!r} failed: {exc}", file=sys.stderr)
        return []
    items = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "job_url": link,
            "date_posted": (item.findtext("pubDate") or "").strip() or None,
        })
    return items


def parse_page(html: str) -> dict:
    """Pull the details-table fields + full description out of one jobs.ps posting page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    info: dict = {}
    # Details list: <div class="view--detail-item"><span>label</span><span>value</span></div>.
    # Older pages used <tr><td>label</td><td>value</td></tr>; both are read.
    pairs = [item.find_all("span", recursive=False) for item in soup.select(".view--detail-item")]
    pairs += [row.find_all(["td", "th"]) for row in soup.find_all("tr")]
    for cells in pairs:
        if len(cells) < 2:
            continue
        key = JOBSPS_LABELS.get(text_of(cells[0]))
        if key:
            info[key] = text_of(cells[1])

    # A posting can be filed under several categories; keep every slug.
    info["jp_categories"] = [unquote(a["href"]).rstrip("/").rsplit("/", 1)[-1].strip() for a in
                             soup.select('.view--detail-item--category a[href*="/categories/"]')]

    if not info.get("jp_company"):
        # First employer link with text (the logo link has none).
        info["jp_company"] = next((t for a in soup.select('a[href*="/employers/"]')
                                   if (t := text_of(a))), None)

    # The job description block (it also holds the requirements, which the
    # LLM scorer wants too). Falls back to the page text from the "الوصف
    # الوظيفي" (job description) heading onward, then to the whole page, so a
    # template change degrades gently instead of returning nothing.
    content = soup.select(".view--content")
    if content:
        info["description"] = "\n".join(b.get_text("\n", strip=True) for b in content)
    else:
        full_text = soup.get_text("\n", strip=True)
        idx = full_text.find("الوصف الوظيفي")
        info["description"] = full_text[idx:] if idx != -1 else full_text
    return info


def fetch(args) -> pd.DataFrame:
    """RSS discovery + per-page enrichment (see the module docstring for which feeds)."""
    categories = list(getattr(config, "JOBSPS_CATEGORIES", []))
    locations = [loc for loc in args.site_locations.get(SITE, []) if _SLUG_RE.match(loc)]
    ignored = [loc for loc in args.site_locations.get(SITE, []) if not _SLUG_RE.match(loc)]
    if ignored:
        print(f"[jobsps] ignoring non-jobs.ps location(s): {', '.join(ignored)} "
              "(expected slugs like gaza-jobs)")
    # Location feeds + category filter, or category feeds with no filter.
    feeds = locations or categories
    wanted = {_slug_key(c) for c in categories} if locations else set()
    if not feeds:
        note(SITE, "no locations or categories configured")
        return pd.DataFrame()

    cap = getattr(config, "JOBSPS_MAX_PER_FEED", 30)
    raw_items: list[dict] = []
    for feed in feeds:
        print(f"[jobsps] fetching RSS: {feed} …", flush=True)
        items = fetch_rss(feed)[:cap]
        for item in items:
            item["feed"] = feed
        raw_items.extend(items)
    overflowed = check_overflow({feed: [i for i in raw_items if i["feed"] == feed]
                                 for feed in feeds})
    overflow_note = (f"; ⚠ possible missed postings in {', '.join(overflowed)} "
                     "(feed overflowed since last run)" if overflowed else "")
    if not raw_items:
        note(SITE, f"{len(feeds)} feed(s), no postings")
        return pd.DataFrame()

    # The same posting can appear in more than one feed.
    seen: set[str] = set()
    deduped = []
    for item in raw_items:
        if item["job_url"] not in seen:
            seen.add(item["job_url"])
            deduped.append(item)

    session = requests.Session()
    session.headers.update(JOBSPS_HEADERS)
    delay = getattr(config, "JOBSPS_DETAIL_DELAY_SECONDS", 1.0)
    max_failures = getattr(config, "JOBSPS_DETAIL_MAX_FAILURES", 5)
    print(f"[jobsps] fetching {len(deduped)} job page(s) …", flush=True)
    ok = failed = in_a_row = off_category = 0
    rows = []
    for n, item in enumerate(deduped, 1):
        if n > 1:
            time.sleep(delay + random.uniform(0, delay / 2))
        if n % 10 == 0 or n == len(deduped):
            print(f"  fetching {n}/{len(deduped)}", flush=True)
        html = fetch_with_retry(session, item["job_url"])
        if html is None:
            failed += 1
            in_a_row += 1
            if in_a_row >= max_failures:
                print(f"  ! {in_a_row} failures in a row — skipping the remaining "
                      f"{len(deduped) - n} page(s)", file=sys.stderr)
                break
        else:
            in_a_row = 0
            ok += 1
            info = parse_page(html)
            slugs = info.get("jp_categories") or ([] if locations else [item["feed"]])
            if wanted and not wanted & {_slug_key(c) for c in slugs}:
                off_category += 1
                continue
            category = ", ".join(slugs) or None
            work_nature = info.get("jp_work_nature") or ""
            rows.append({
                "title": item["title"],
                "company": info.get("jp_company"),
                "location": info.get("jp_location") or "Palestine",
                "description": info.get("description"),
                "job_url": item["job_url"],
                "job_type": info.get("jp_job_type"),
                "date_posted": item.get("date_posted"),
                "min_amount": None,
                "max_amount": None,
                "interval": None,
                "site": SITE,
                "is_remote": bool(_JOBSPS_REMOTE_RE.search(work_nature)) if work_nature else None,
                "search_term": category,
                "search_location": item["feed"] if locations else "Palestine",
            })
    kept = (f", {off_category} dropped (category not in JOBSPS_CATEGORIES)"
            if off_category else "")
    print(f"[jobsps] {ok} ok, {failed} failed{kept}")
    RUN_STATS.update(jobsps_pages_ok=ok, jobsps_pages_failed=failed)
    note(SITE, f"{len(feeds)} feed(s): {', '.join(feeds)}; {ok} pages fetched, "
               f"{failed} failed{kept}{overflow_note}")
    return fill_generic_fields(pd.DataFrame(rows)) if rows else pd.DataFrame()
