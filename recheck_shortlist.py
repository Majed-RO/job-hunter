"""
Has anything on the LinkedIn shortlist closed since the run? LinkedIn closes
postings early, often within a day when hundreds have applied, and its page
then says "No longer accepting applications". Run this before working through
output/shortlist_linkedin.md: it re-opens each shortlisted posting's public
page and moves the closed ones to a short "Closed since the run" list at the
end of the shortlist. Each re-check also adds an entry to a "Re-check log" at
the very bottom of the file, saying what it moved, so the cleaned shortlist
can be compared with the one the run wrote.

Usage (from ~/job-hunter, with the venv active):
    python recheck_shortlist.py

One page request per shortlisted posting (plus its "Also posted as" copies),
paced like a run's page fetches; no LLM calls. A posting counts as closed only
when every copy of it is closed. A page that can't be fetched is left where it
is. Safe to run again: postings already moved stay in the closed list.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import config
from sources.common import utf8_console
from sources.linkedin import check_closed

SHORTLIST_PATH = Path(config.OUTPUT_DIR) / "shortlist_linkedin.md"
CLOSED_HEADING = "## Closed since the run"
NOTE_PREFIX = "> Re-checked "
LOG_HEADING = "## Re-check log"

_POSTING_RE = re.compile(r"\[posting\]\((\S+?)\)")
_ALSO_PREFIX = "Also posted as: "


def split_shortlist(lines: list[str]) -> tuple[list[str], list[list[str]], list[str], list[str]]:
    """(header, entries, already-closed lines, rest). Entries are the "## "
    sections before the first "---"; rest is everything from that "---" on."""
    header: list[str] = []
    entries: list[list[str]] = []
    closed: list[str] = []
    in_closed = False
    for i, line in enumerate(lines):
        if line == "---":
            return header, entries, closed, lines[i:]
        if line.startswith("## "):
            in_closed = line.startswith(CLOSED_HEADING)
            if not in_closed:
                entries.append([line])
        elif in_closed:
            if line.startswith("- "):
                closed.append(line)
        elif entries:
            entries[-1].append(line)
        elif not line.startswith(NOTE_PREFIX):
            header.append(line)
    return header, entries, closed, []


def entry_urls(entry: list[str]) -> list[str]:
    urls = [m.group(1) for line in entry for m in _POSTING_RE.finditer(line)][:1]
    for line in entry:
        if line.startswith(_ALSO_PREFIX):
            urls += [u.strip() for u in line[len(_ALSO_PREFIX):].split(";") if u.strip()]
    return urls


def with_log_entry(rest: list[str], stamp: str, n_checked: int, moved: list[str],
                   unchecked: list[str], n_from_run: int) -> list[str]:
    """rest with this re-check's entry added to the "Re-check log" at the
    bottom (created on the first re-check)."""
    rest = list(rest)
    while rest and not rest[-1].strip():
        rest.pop()
    if LOG_HEADING not in rest:
        rest += ["", "---", "", LOG_HEADING, "",
                 f"What each re-check changed. The run shortlisted {n_from_run} posting(s); "
                 "everything above this log is as the run wrote it, except the postings "
                 "moved to \"Closed since the run\"."]
    n_open = n_checked - len(moved)
    rest += ["", f"### {stamp}", "",
             f"- Checked {n_checked} posting(s): {len(moved)} closed, {n_open} still open. "
             f"Shortlist now {n_open} of the run's {n_from_run}."]
    if moved:
        rest.append('- Moved to "Closed since the run":')
        rest += [f"  {line}" for line in moved]
    else:
        rest.append("- Nothing moved.")
    if unchecked:
        rest.append("- Couldn't be checked (page not fetched), left in place:")
        rest += [f"  {line}" for line in unchecked]
    return rest


def main() -> None:
    utf8_console()
    if not SHORTLIST_PATH.exists():
        raise SystemExit(f"{SHORTLIST_PATH} not found — run job_match.py first.")
    lines = SHORTLIST_PATH.read_text(encoding="utf-8").splitlines()
    header, entries, closed, rest = split_shortlist(lines)
    while header and not header[-1].strip():
        header.pop()

    urls = list(dict.fromkeys(u for e in entries for u in entry_urls(e)))
    print(f"[recheck] checking {len(urls)} LinkedIn page(s) for {len(entries)} shortlisted posting(s) …")
    status = check_closed(urls)

    still_open: list[list[str]] = []
    moved: list[str] = []      # "- <title> · [posting](<url>)", this re-check only
    unchecked: list[str] = []  # kept in place: no copy open, some page not fetched
    for entry in entries:
        states = [status.get(u) for u in entry_urls(entry)]
        line = f"- {entry[0].removeprefix('## ')} · [posting]({(entry_urls(entry) or [''])[0]})"
        if states and all(s is True for s in states):
            moved.append(line)
            print(f"  closed: {entry[0].removeprefix('## ')}")
            continue
        still_open.append(entry)
        if None in states and False not in states:
            unchecked.append(line)
    closed += moved
    newly_closed = len(moved)
    unknown = sum(1 for s in status.values() if s is None)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = (f"{NOTE_PREFIX}{stamp}: {newly_closed} newly closed ({len(closed)} closed since the run), "
            f"{len(still_open)} still open"
            + (f", {unknown} page(s) couldn't be fetched (left in place)" if unknown else "")
            + ". Closed postings are listed at the end; what each re-check "
              "changed is in the Re-check log at the bottom.")
    out = header + ["", note, ""]
    for entry in still_open:
        out += entry
    if closed:
        out += [f"{CLOSED_HEADING} ({len(closed)})", "",
                "LinkedIn now says \"No longer accepting applications\" for these.", ""]
        out += closed + [""]
    SHORTLIST_PATH.write_text("\n".join(out + with_log_entry(
        rest, stamp, len(entries), moved, unchecked, len(still_open) + len(closed))) + "\n",
        encoding="utf-8")
    print(f"[recheck] {newly_closed} newly closed, {len(still_open)} still open"
          + (f", {unknown} unreachable" if unknown else "") + f" → {SHORTLIST_PATH}")


if __name__ == "__main__":
    main()
