"""
Did the script see this job? Paste links to postings you found by browsing a
board yourself; for each one this says whether the script ever found it, and
if so where it stopped: dropped by the pre-filter, scored too low, or
shortlisted.

Usage (from ~/job-hunter, with the venv active):
    python check_missed.py https://www.linkedin.com/jobs/view/4312345678 ...
    python check_missed.py < links.txt          # one link per line

Reads output/history.csv only (every run's rows); no network calls.
LinkedIn links match by job id, so any form works (/jobs/view/<id>,
?currentJobId=<id> from search or recommendations); other sites match by URL.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd

import config

HISTORY_PATH = Path(config.OUTPUT_DIR) / config.HISTORY_FILENAME
# Same id pattern sources/linkedin.py uses, plus the currentJobId= form that
# LinkedIn's search and recommendation pages put in the address bar.
_LI_ID_RES = (re.compile(r"/jobs/view/(?:[^/?]*-)?(\d{6,})"), re.compile(r"currentJobId=(\d{6,})"))


def job_key(url: str) -> str:
    """Comparable form of a job link: "li:<id>" for LinkedIn, else the bare URL."""
    url = str(url).strip()
    if "linkedin.com" in url:
        for pattern in _LI_ID_RES:
            if m := pattern.search(url):
                return f"li:{m.group(1)}"
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{unquote(parts.path).rstrip('/')}".lower()


def describe(rows: pd.DataFrame) -> str:
    """Verdict for one job from all its history rows (one per run that saw it)."""
    rows = rows.sort_values("run_timestamp")
    last = rows.iloc[-1]
    seen = (f"seen in {len(rows)} run(s), first {rows['run_timestamp'].iloc[0][:10]}, "
            f"last {last['run_timestamp'][:10]}")
    title = f"{last['title']} — {last['company'] if pd.notna(last['company']) else 'company not listed'}"
    passed = rows[rows["passes_threshold"].astype(str).str.lower() == "true"]
    if len(passed):
        r = passed.iloc[-1]
        return (f"SHORTLISTED ({r['run_timestamp'][:10]}, overall {r['overall']}, {r['verdict']}) "
                f"· {title} · {seen}")
    if last["reason"] == "filtered out before scoring":
        return f"DROPPED BEFORE SCORING: {last['blockers']} · {title} · {seen}"
    return (f"SCORED, NOT SHORTLISTED: overall {last['overall']}, stack_fit {last['stack_fit']}, "
            f"{last['verdict']} — {last['reason']} · {title} · {seen}")


def main(urls: list[str]) -> None:
    if not HISTORY_PATH.exists():
        sys.exit(f"No history yet ({HISTORY_PATH}); run job_match.py first "
                 "(with ENABLE_HISTORY_LOG = True).")
    history = pd.read_csv(HISTORY_PATH, low_memory=False)
    history["key"] = history["job_url"].map(job_key)
    missed = 0
    for url in urls:
        rows = history[history["key"] == job_key(url)]
        if len(rows):
            print(f"{url}\n  {describe(rows)}\n")
        else:
            missed += 1
            print(f"{url}\n  NEVER FOUND: no run's searches returned it. Add a search term "
                  "or location that matches it, or raise RESULTS_PER_BOARD if the run "
                  "reported capped searches.\n")
    print(f"{len(urls)} checked, {missed} never found "
          f"(history covers {history['run_timestamp'].min()[:10]} to "
          f"{history['run_timestamp'].max()[:10]}).")


if __name__ == "__main__":
    links = sys.argv[1:] or [line.strip() for line in sys.stdin if line.strip()]
    if not links:
        sys.exit(__doc__)
    main(links)
