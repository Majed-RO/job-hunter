"""
Job sources. Each site lives in its own module and exposes one function:

    fetch(args) -> pd.DataFrame

The rows it returns carry at least common.STANDARD_COLUMNS (plus any extra
site-specific columns), so they merge into the rest of the pipeline
(dedupe, prefilter, scoring, output) unchanged.

Modules are imported only when their site is enabled for the run (config.SOURCES
or --sites), so e.g. a jobs.ps-only run never imports JobSpy.
"""

from __future__ import annotations

import importlib
from types import ModuleType

# Site name (as used in config.SOURCES, --sites, the `site` column and output
# filenames) -> module that implements it.
REGISTRY = {
    "linkedin": "sources.linkedin",
    "indeed": "sources.indeed",
    "jobsps": "sources.jobsps",
    "weworkremotely": "sources.weworkremotely",
}


def load_source(name: str) -> ModuleType:
    if name not in REGISTRY:
        raise ValueError(f"Unknown source {name!r}; expected one of {', '.join(REGISTRY)}")
    return importlib.import_module(REGISTRY[name])
