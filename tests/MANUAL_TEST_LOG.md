# Manual Test Log

Manual checks that complement the automated suite (`python -m pytest`). Record the real result
and date of each run; leave a check as **Pending** until someone has actually done it.

Environment for the 2026-10-17 run: Windows 11, Python 3.12.10, local server
(`python backend/app.py`), Chromium-based browser pane, no `ANTHROPIC_API_KEY` set.

| Check | How | Result | Date |
|---|---|---|---|
| Empty file | Upload a 0-byte `.csv` | Pass: the client shows "That file is empty." before uploading; the API returns 400 "The file is empty." (also covered by `test_api.py`) | 2026-10-17 |
| Header-only CSV | Upload `a,b,c` with no rows | Pass: "The file has a header but no data rows." shown in the error box | 2026-10-17 |
| Non-CSV file | Choose `notes.txt` | Pass: "Please choose a .csv file." shown; the API returns 415 for non-CSV names and binary content (`test_api.py`) | 2026-10-17 |
| Oversized file | Upload a file over 10 MB | Pass (automated): API returns 413 with "File is larger than the 10 MB limit." (`test_upload_rejects_oversized_file`); the browser also blocks files over 10 MB before upload. In-browser upload of a real 11 MB file: Pending | 2026-10-17 |
| Malformed CSV | Upload a CSV with a broken quote and ragged rows | Pass: the error box shows "Could not parse the file as CSV: Error tokenizing data. C error: Expected 2 fields in line 3, saw 4" (also `test_upload_rejects_empty_malformed_and_header_only`) | 2026-10-17 |
| Both samples in the browser | Click "Problematic hiring" and "Clean survey" | Pass: hiring sample shows risk 100/100 Critical with 16 issues (8 bias, 4 missing data, 4 PII); clean sample shows 0 issues, risk 0/100 Low | 2026-10-17 |
| Settings re-run | Uncheck all protected attributes and press "Re-run analysis" | Pass: same session id, disparity findings removed, group chart shows its empty-state hint | 2026-10-17 |
| Data card downloads | Download HTML and Markdown cards | Pass: both download with `*_data_card.*` file names; the HTML card renders all 9 sections with coloured badges and "withheld (PII)" for PII columns | 2026-10-17 |
| XSS in data | Upload a CSV whose column name and file name contain HTML tags | Pass: shown as text, no element injected, no script ran | 2026-10-17 |
| Mobile layout at 375 px | Emulated 375x812 viewport, all three tabs | Pass: no horizontal page scroll on any tab (document width 375 px); wide tables scroll inside their own box | 2026-10-17 |
| Light and dark themes | OS setting plus the theme toggle | Pass: both themes render; charts redraw with theme colours | 2026-10-17 |
| Run without an API key | No `ANTHROPIC_API_KEY` | Pass: "Template explanations" badge; every finding has an explanation and a recommendation | 2026-10-17 |
| Run with an API key | Set `ANTHROPIC_API_KEY` in `.env`, restart, analyze the hiring sample | Pending: no key was available during development. Expected: "AI explanations" badge and `ai_enabled: true` | |
| Incognito window | Open the app in a private window | Pending (to run against the deployed URL) | |
| Two tabs, separate sessions | Analyze different files in two tabs, download each card | Pass (automated): `test_sessions_are_isolated`; the session id lives only in page memory. In-browser: Pending | 2026-10-17 |
