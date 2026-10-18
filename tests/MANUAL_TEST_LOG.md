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

## Pre-deploy re-check (commit `5253e81`)

Re-run on 2026-10-18 after the column limit, session cap and Chart.js integrity hash were added:
`python -m pytest` (104 passed), then the local server in the Chromium-based browser pane, no
`ANTHROPIC_API_KEY`, Presidio NER on.

| Check | How | Result | Date |
|---|---|---|---|
| `/api/health` | Open `/api/health` | Pass: `status: ok`, Presidio NER on with `en_core_web_sm`, `ai_enabled: false` | 2026-10-18 |
| Both samples | Click both sample buttons | Pass: hiring 100/100 Critical, 16 issues (Bias 8, Missing data 4, PII 4), every finding with an explanation and a recommendation; clean survey 0/100 Low, 0 issues | 2026-10-18 |
| Charts | All three tabs after the hiring sample | Pass: class, group and missing-data charts drawn; Chart.js 4.4.4 loaded with its integrity hash | 2026-10-18 |
| No raw PII on the page | Search the page text after the hiring sample | Pass: no `example.com` email, SSN-style or 555-01xx phone value shown | 2026-10-18 |
| Settings re-run | Untick `age`, press "Re-run analysis" | Pass: same session id; Bias 8 -> 5, all three `age` findings removed | 2026-10-18 |
| Data cards after re-run | Fetch HTML and Markdown cards for that session | Pass: HTTP 200, `problematic_hiring_data_card.html/.md`, 9 sections each, 5 columns "withheld (PII)", no `age` disparity, no raw PII; HTML card renders | 2026-10-18 |
| Upload errors | Choose `notes.txt`, an empty CSV, a header-only CSV, a broken CSV | Pass: "Please choose a .csv file.", "That file is empty.", "The file has a header but no data rows.", "Could not parse the file as CSV: ..." | 2026-10-18 |
| Column limit | Upload a 150-column CSV | Pass: "The file has 150 columns; the limit is 100." (HTTP 413) | 2026-10-18 |
| NER on free text | Upload an 80-row CSV whose `notes` column mentions names ("Spoke with John Smith ...") | Pass: "Person names found in 1 column", 40 of 80 rows in `notes`, marked as a lower bound from the NER layer; no name shown on the page | 2026-10-18 |
| Mobile layout at 375 px | Emulated 375x812 viewport, all three tabs | Pass: document width 375 px on every tab, no horizontal scroll | 2026-10-18 |
| Light and dark themes | Theme toggle on the 375 px view | Pass: switches to light (`data-theme="light"`) and back | 2026-10-18 |
| Errors in logs | Browser console and server log after all checks | Pass: only the expected 400/413 responses from the bad-file checks; no script errors, no server errors | 2026-10-18 |

## Real-data accuracy check

Run on 2026-10-18 with the user's approval to download two public files into a temporary folder
outside the repository (not committed): `adult.zip` from archive.ics.uci.edu (620,237 bytes;
`adult.data`, 32,561 rows, with a header row added) and `compas-scores-two-years.csv` from
github.com/propublica/compas-analysis (2,546,489 bytes, 7,214 rows, 53 columns, holds real
defendants' names and birth dates). Expected values were computed with plain pandas, not the
app's code, then compared with `run_analysis` output. Personal-data values were never printed.
Local run time: Adult 3.8 s, COMPAS 8.5 s. Full comparison table: README, "Accuracy check on
real data".

| Check | Result | Date |
|---|---|---|
| Adult: gap sizes (sex 0.358, race 0.347, under-30 0.158), `?` read as missing (workclass 5.6%, occupation 5.7%), class imbalance 3.2:1, `relationship` vs `sex` proxy 0.65, no PII | Pass: every number matched | 2026-10-18 |
| COMPAS (first run, commit `4f2387d`): gaps, missing columns, proxies, `name`/`dob` | Pass: matched, with the exceptions below | 2026-10-18 |
| COMPAS: label choice | Fail, fixed: picked `is_recid` instead of `two_year_recid` (97% agreement), which flagged a sex gap of 0.798 that is 0.819 on the published label. Now the last matching column wins | 2026-10-18 |
| Adult `native-country`: small best group (Iran, 43 rows) | Fail, fixed: 9 of 25 flagged groups were not significant (Fisher p >= 0.05). Gaps now need p < 0.05 to count; others are noted as possibly chance, or low severity if none is significant | 2026-10-18 |
| COMPAS: charge descriptions tagged as person names (3 columns) | Partly fixed: `c_charge_desc` and `r_charge_desc` no longer flagged; `vr_charge_desc` still is | 2026-10-18 |
| COMPAS: columns `first` and `last` | Fail, fixed: found only by NER in 207 and 92 rows; now recognised by column name in all 7,214 | 2026-10-18 |
| After the fixes: `python -m pytest`, both bundled samples, synthetic probes | Pass: 118 tests; samples give the same findings as before; probes 74 of 75 | 2026-10-18 |

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
