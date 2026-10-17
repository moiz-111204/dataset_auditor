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
| Incognito window | Open the app in a private window | Pending (to run against the deployed URL; see "Live deployment" below) | |
| Two tabs, separate sessions | Analyze different files in two tabs, download each card | Pass (automated): `test_sessions_are_isolated`; the session id lives only in page memory. In-browser: Pending | 2026-10-17 |

## Live deployment (Render)

Run these against the live URL after deploying (see "Deployment" in the README) and replace
**Pending** with the real result and date.

| Check | How | Result | Date |
|---|---|---|---|
| Clean build as on Render | Fresh `git clone`, new Python 3.12 venv, `pip install -r requirements.txt`, then `python backend/app.py` with `PORT=10000` and `FORWARDED_ALLOW_IPS=*` | Pass on Windows 11 (Render builds on Linux, so this is not the real build): `en_core_web_sm-3.8.0` installed from its GitHub wheel; `/api/health` showed Presidio NER on with `en_core_web_sm`; hiring sample 16 findings, risk 100; clean survey 0 findings; all four data cards downloaded with no SSN-shaped value in them; with `RATE_LIMIT_PER_MINUTE=3`, four requests with one `X-Forwarded-For` address gave 200, 200, 200, 429 and a different address got 200 | 2026-10-17 |
| Render build | Render's build log for the first deploy | Pending. Expected: Python 3.12 and `en_core_web_sm-3.8.0` installed, health check on `/api/health` passes | |
| `/api/health` on the live URL | Open `<live URL>/api/health` | Pending. Expected: `"status": "ok"`, `presidio_ner: true` (unless `PII_NER_ENABLED=false` was set), `ai_enabled` true only if a key was entered | |
| Incognito window | Open the live URL in a private window | Pending | |
| Phone | Open the live URL on a real phone (mobile data or Wi-Fi) | Pending | |
| Both samples on the live site | Click "Problematic hiring" and "Clean survey" | Pending. Expected: hiring 16 issues, risk 100/100 Critical; clean survey 0 issues, risk 0/100 Low | |
| Both data cards on the live site | Download the HTML and Markdown cards after the hiring sample | Pending. Expected: both download; PII columns show "withheld (PII)" | |
| Memory on Render | Service **Metrics** page after running both samples | Pending. Must stay under 512 MB; if not, set `PII_NER_ENABLED=false` | |
| Cold start | Open the live URL after more than 15 minutes without traffic | Pending. Expected: about a minute's wait, then the dashboard loads | |
