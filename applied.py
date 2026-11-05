"""
Save the jobs you've applied to. Saved jobs are kept out of the shortlist on
every later run and listed under "Already in pipeline" with the date you
applied, including copies of the job under another link (same title + company).

Usage (from ~/job-hunter, with the venv active):
    python applied.py                # paste links, one per line; Enter to finish
    python applied.py <link> ...     # or pass them, in quotes if they contain &
    python applied.py --list         # what you've saved

Use the job's link from the shortlist or the job board: LinkedIn and Indeed
links match in any form (see check_missed.py). A company careers-page link is
saved, but can't be matched to the board's posting.

Saved in config.APPLIED_JOBS_FILE, a plain text file, one job per line. To undo
one, delete its line. No network calls, no LLM cost.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

import config
from check_missed import HISTORY_PATH, job_key
from sources.common import utf8_console

_HEADER = "# date<TAB>link<TAB>title<TAB>company. Delete a line to undo; a bare link also works.\n"


def load_applied() -> list[dict]:
    """The saved jobs: dicts with date, url, title and company (blank if unknown)."""
    path = Path(config.APPLIED_JOBS_FILE)
    if not path.exists():
        return []
    jobs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = [f.strip() for f in line.split("\t")]
        if line.startswith("#") or not line.strip():
            continue
        if fields[0].startswith("http"):  # a bare link added by hand
            fields.insert(0, "")
        fields += [""] * 4
        jobs.append(dict(zip(("date", "url", "title", "company"), fields)))
    return jobs


def history_titles() -> dict[str, tuple[str, str]]:
    """(title, company) by job_key, for every link in history.csv."""
    if not HISTORY_PATH.exists():
        return {}
    wanted = {"job_url", "also_posted", "title", "company"}
    hist = pd.read_csv(HISTORY_PATH, usecols=lambda c: c in wanted, low_memory=False)
    titles = {}
    for _, r in hist.iterrows():
        also = r["also_posted"].split("; ") if isinstance(r["also_posted"], str) else []
        for link in [r["job_url"], *also]:
            titles[job_key(link)] = (r["title"], r["company"] if isinstance(r["company"], str) else "")
    return titles


def save(url: str, titles: dict[str, tuple[str, str]]) -> None:
    url = url.strip()
    if not url.startswith("http"):
        print(f"Not a link, skipped: {url}")
        return
    for job in load_applied():
        if job_key(job["url"]) == job_key(url):
            print(f"Already saved on {job['date'] or 'an earlier date'}: {job['title'] or url}")
            return
    title, company = titles.get(job_key(url), ("", ""))
    path = Path(config.APPLIED_JOBS_FILE)
    with path.open("a", encoding="utf-8") as f:
        if not path.stat().st_size:
            f.write(_HEADER)
        f.write("\t".join((date.today().isoformat(), url, str(title), company)) + "\n")
    print(f"Saved: {title} — {company}" if title
          else f"Saved by link only (no run has seen this link): {url}")


def main(args: list[str]) -> None:
    if "--list" in args:
        jobs = load_applied()
        for job in jobs:
            print(f"{job['date'] or '(no date)'}  {job['title'] or '(unknown title)'} — "
                  f"{job['company'] or '(unknown company)'}  {job['url']}")
        print(f"{len(jobs)} saved in {config.APPLIED_JOBS_FILE}")
        return
    titles = history_titles()
    for url in args:
        save(url, titles)
    if args:
        return
    print("Paste a job link and press Enter. Press Enter on an empty line to finish.")
    try:
        while url := input("> ").strip():
            save(url, titles)
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    utf8_console()
    main(sys.argv[1:])
