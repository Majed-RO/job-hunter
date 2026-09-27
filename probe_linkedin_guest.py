"""
One-off probe: what does LinkedIn's *guest* (logged-out) job page actually expose?

Checks, per posting, whether the page shows:
  - applicant count        (e.g. "Over 200 applicants")
  - workplace type         (Remote / On-site / Hybrid badge or text)
  - "Promoted" status
  - the job-criteria list  (seniority, employment type, ...)

Usage (from ~/job-hunter, with the venv active):
    python probe_linkedin_guest.py
    python probe_linkedin_guest.py 4012345678 https://www.linkedin.com/jobs/view/4098765432/

With no arguments it runs a small remote-only search and probes the first
results. Extra job IDs / URLs (e.g. the onsite posting from your screenshot)
are probed too. ~15 requests total, 3s apart — negligible rate-limit risk.

Output: output/guest_probe/  (raw HTML per job + summary.json)
Nothing here touches job_match.py, the cache, or history.
"""
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path(__file__).parent / "output" / "guest_probe"
SEARCH_TERMS = ["Next.js developer", "TypeScript full stack engineer"]
PER_TERM = 4
DELAY = 3

HEADERS = {
    "user-agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
}
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

WORKPLACE_RE = re.compile(r"\b(remote|on-?site|hybrid)\b", re.I)
APPLICANT_RE = re.compile(r"(over\s+)?[\d,]+\s+applicants?|be among the first[^.<]*applicants",
                          re.I)


def job_id_from(arg: str) -> str | None:
    m = re.search(r"(\d{8,})", arg)
    return m.group(1) if m else None


def search_ids(session: requests.Session) -> list[str]:
    ids: list[str] = []
    for term in SEARCH_TERMS:
        r = session.get(SEARCH_URL, params={"keywords": term, "f_WT": "2", "start": 0},
                        timeout=20)
        print(f"search {term!r}: HTTP {r.status_code}")
        if r.ok:
            found = re.findall(r"urn:li:jobPosting:(\d+)", r.text)
            ids += [i for i in dict.fromkeys(found) if i not in ids][:PER_TERM]
        time.sleep(DELAY)
    return ids


def text_of(el) -> str | None:
    return " ".join(el.get_text(" ", strip=True).split()) if el else None


def probe(session: requests.Session, job_id: str) -> dict:
    r = session.get(POSTING_URL.format(job_id), timeout=20)
    rec: dict = {"job_id": job_id, "http": r.status_code, "bytes": len(r.content)}
    if not r.ok:
        return rec
    (OUT / f"{job_id}.html").write_text(r.text, encoding="utf-8")
    soup = BeautifulSoup(r.text, "html.parser")

    rec["title"] = text_of(soup.select_one(".topcard__title, h2"))
    rec["company"] = text_of(soup.select_one(".topcard__org-name-link, .topcard__flavor"))
    rec["location_line"] = text_of(soup.select_one(".topcard__flavor--bullet"))

    # Applicant count: known class first, then any text match anywhere.
    rec["applicants_by_class"] = text_of(
        soup.select_one(".num-applicants__caption, figcaption.num-applicants__caption"))
    rec["applicants_by_text"] = sorted(set(m.group(0) for m in APPLICANT_RE.finditer(soup.get_text(" "))))

    # Criteria list (seniority / employment type / function / industries).
    criteria = {}
    for item in soup.select(".description__job-criteria-item"):
        k = text_of(item.select_one(".description__job-criteria-subheader"))
        v = text_of(item.select_one(".description__job-criteria-text"))
        if k:
            criteria[k] = v
    rec["criteria"] = criteria

    # Workplace type: search the top card (not the description body, which
    # mentions "remote" in all sorts of contexts) plus any explicit badge.
    top = soup.select_one(".top-card-layout, .topcard") or soup
    desc = soup.select_one(".description__text, .show-more-less-html__markup")
    top_text = top.get_text(" ")
    if desc is not None:
        top_text = top_text.replace(desc.get_text(" "), " ")
    rec["workplace_in_topcard"] = sorted(set(m.group(1).lower() for m in WORKPLACE_RE.finditer(top_text)))
    rec["workplace_in_json"] = sorted(set(re.findall(r'"workplaceTypes?"\s*:\s*[^,}]+', r.text)))[:5]

    rec["promoted_mentions"] = len(re.findall(r"\bpromoted\b", r.text, re.I))
    rec["has_description"] = bool(desc)
    return rec


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    extra = [i for i in (job_id_from(a) for a in sys.argv[1:]) if i]
    ids = extra + [i for i in search_ids(session) if i not in extra]
    print(f"probing {len(ids)} postings…")

    results = []
    for job_id in ids:
        rec = probe(session, job_id)
        results.append(rec)
        print(f"  {job_id}: HTTP {rec['http']}  applicants={rec.get('applicants_by_class') or rec.get('applicants_by_text')}"
              f"  workplace={rec.get('workplace_in_topcard')}  promoted={rec.get('promoted_mentions')}")
        time.sleep(DELAY)

    (OUT / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    print(f"\nwrote {OUT / 'summary.json'} and {len(results)} raw HTML files")


if __name__ == "__main__":
    main()
