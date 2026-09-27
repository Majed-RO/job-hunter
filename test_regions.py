"""
Quick check of which region strings LinkedIn understands.

Importing sources.linkedin applies the LinkedIn location patch, so results in
countries JobSpy doesn't know (Algeria, Jordan, Palestine, ...) no longer
crash the search.

Run (venv active):  python test_regions.py
"""
import sources.linkedin  # noqa: F401  (import applies the patch)
from jobspy import scrape_jobs

for loc in ["Worldwide"]:
    try:
        df = scrape_jobs(site_name=["linkedin"], search_term="next.js developer",
                         location=loc, results_wanted=10)
    except Exception as exc:
        print(f"\n=== {loc}: FAILED — {exc}")
        continue
    print(f"\n=== {loc}: {len(df)} jobs ===")
    if len(df):
        print(df["location"].value_counts().head(8).to_string())
