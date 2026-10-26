# Run summary

Run finished 2026-10-25 18:30 · job-hunter 1.0.0 · provider openrouter · model anthropic/claude-haiku-5.5

This run took 31m 18s and searched 4 boards, collecting 498 postings (linkedin 273, indeed 130, jobsps 15, weworkremotely 80). After dedupe 441 were unique; the pre-filter removed 318 and 123 were scored (62 from cache, 61 new LLM calls, 1m 9s). The LLM rated 4 apply, 22 maybe and 97 skip. 5 cleared the score thresholds and 5 were shortlisted (linkedin 2, weworkremotely 3).

Boards: linkedin, indeed, jobsps, weworkremotely · thresholds: linkedin overall >= 60, stack_fit >= 70; overall >= 60 when over 100 applicants; indeed overall >= 60, stack_fit >= 65; jobsps overall >= 60, stack_fit >= 0; weworkremotely overall >= 60, stack_fit >= 65 · top 25 per board

## Coverage

- **linkedin**: 14 of 25 searches hit the results cap, so more postings probably exist (Next.js developer · United States (10); TypeScript full stack engineer · United States (10); senior React engineer · United States (10); Full Stack Web Developer · United States (30); Web Developer · United States (30); Next.js developer · Worldwide (30); TypeScript full stack engineer · Worldwide (30); senior React engineer · Worldwide (30); Full Stack Web Developer · Worldwide (30); Web Developer · Worldwide (30); TypeScript full stack engineer · United Arab Emirates (15); senior React engineer · United Arab Emirates (15); Full Stack Web Developer · United Arab Emirates (30); Web Developer · United Arab Emirates (30)). Raise RESULTS_PER_BOARD or LOCATION_RESULTS_OVERRIDE for those to see them.
- **indeed**: 5 of 35 searches hit the results cap, so more postings probably exist (Next.js developer · United States (10); TypeScript full stack engineer · United States (10); senior React engineer · United States (10); Full Stack Web Developer · United States (30); Web Developer · United States (30)). Raise RESULTS_PER_BOARD or LOCATION_RESULTS_OVERRIDE for those to see them.

## Postings through each step

| Board | Scraped | Unique | Pre-filtered | Scored | From cache | LLM calls | Apply | Maybe | Skip | Ineligible | Failed | Apply/Maybe | Passed thresholds | Shortlisted | One country |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| linkedin | 273 | 256 | 229 | 27 | 3 | 24 | 2 | 5 | 20 | 14 | 0 | 7 | 2 | 2 | 0 |
| indeed | 130 | 92 | 73 | 19 | 2 | 17 | 0 | 4 | 15 | 12 | 0 | 4 | 0 | 0 | 0 |
| jobsps | 15 | 14 | 0 | 14 | 3 | 11 | 0 | 6 | 8 | 1 | 0 | 6 | 0 | 0 | 0 |
| weworkremotely | 80 | 79 | 16 | 63 | 54 | 9 | 2 | 7 | 54 | 11 | 0 | 9 | 3 | 3 | 0 |
| **all boards** | **498** | **441** | **318** | **123** | **62** | **61** | **4** | **22** | **97** | **38** | **0** | **26** | **5** | **5** | **0** |

Unique counts dedupe across boards too: a posting found on two boards is kept once, under whichever board returned it first.

## Time per step

| Step | Time | Details |
|---|---|---|
| scrape linkedin | 24m 22s | 25 searches; 259 pages fetched, 5 failed |
| scrape indeed | 5m 15s | 35 searches |
| scrape jobsps | 18s | 1 feed(s): gaza-jobs; 0 pages fetched, 5 failed; 15 kept from the feed summary only (page blocked/failed, category filter not applied) |
| scrape weworkremotely | 14s | 4 feed(s); 80 kept of 124 (8 outside Anywhere in the World; 10 older than 720h) |
| scoring | 1m 9s | 498 rows → 441 unique; 318 filtered free, 62 from cache, 61 LLM calls |
| **total** | **31m 18s** | |
