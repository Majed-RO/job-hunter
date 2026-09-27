"""Indeed, via JobSpy. Only searches locations Indeed knows as a country."""

from __future__ import annotations

import pandas as pd

from sources import _jobspy
from sources.common import RUN_STATS, fill_generic_fields, note

SITE = "indeed"


def fetch(args) -> pd.DataFrame:
    jobs, n_searches = _jobspy.search(args, SITE, args.site_remote_only[SITE], countries_only=True)
    RUN_STATS["searches"] = RUN_STATS.get("searches", 0) + n_searches
    note(SITE, f"{n_searches} search{'' if n_searches == 1 else 'es'}")
    if not len(jobs):
        return jobs
    return fill_generic_fields(jobs)
