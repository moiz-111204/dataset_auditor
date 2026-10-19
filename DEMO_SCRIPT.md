# Demo script: Dataset Bias & Privacy Auditor (about 2 minutes)

Covers the seven topics the rulebook asks for: problem, main features, technical approach, AI
component, challenges, limitations, future improvements. The spoken text is about 255 words,
roughly 105 seconds at a steady pace, so with the on-screen clicks it runs close to 2:00.
Presenter: **Rimsha** (Team Doxa).

## Before you record

- **Wake the site.** The free host sleeps after 15 minutes idle and takes about a minute to
  wake. Open the live URL a few minutes early and run the hiring sample once.
- **Check the badge** that appears under the risk gauge after you run the hiring sample. It says
  either "AI explanations" or "Template explanations". Use the matching line in step 5.
- **Never show the API key**: not in the Render dashboard, not in a terminal.
- Browser at 100% zoom, window about 1280 x 720, one tab. Close other tabs and notifications.
- If the live site takes more than about 10 seconds to analyse, cut the wait when editing.
- Upload the video as unlisted (YouTube) or "anyone with the link" (Drive), then open the link
  in an incognito window to confirm it plays without logging in.

## Script

| Time | On screen | Say |
|---|---|---|
| 0:00-0:15 **Problem** | The upload page | "Models trained on skewed, incomplete or leaky data hurt people who are already treated unfairly, and small teams rarely have time to check. This auditor screens a CSV for bias, missing data and personal data before training." |
| 0:15-0:45 **Features: results** | Click **Problematic hiring**. Show the gauge (100/100, Critical) and the 16 issues. Open the **Bias** tab and point at the gender finding, then the **Missing data** and **PII** tabs | "We upload a synthetic hiring file. Risk 100 out of 100, sixteen issues. Women were hired at 15 percent against 28 percent for men, a ratio of 0.54, far below the four-fifths rule. First name is a proxy for gender, ZIP code for race. Income is missing for 54 percent of women. It also found fake SSNs, emails and phones." |
| 0:45-1:08 **Features: settings** | Scroll to **Analysis settings**, untick **age**, press **Re-run analysis**. The Bias tab drops from 8 to 5 | "The tool guesses the label and protected columns from column names, and you can correct them. Here we untick age and re-run on the same in-memory data: the three age findings disappear." |
| 1:08-1:22 **Features: data card** | Click **Download data card (HTML)**, open it, scroll to the feature table where personal columns say "withheld (PII)" | "Then we download the data card, a datasheet for the dataset, in HTML or Markdown. Personal columns show as withheld, never the raw values." |
| 1:22-1:42 **Technical approach and AI** | Stay on the results page, point at the badge | "Technically: FastAPI, pandas and SciPy. AI plays two roles: Presidio with a spaCy model runs locally to find names in free text, and Claude explains each finding in plain language. Claude only receives aggregate numbers, never cell values; without a key the app uses templates." |
| 1:42-2:02 **Challenges, limitations, future, privacy** | Scroll to the bottom of the page, or show the README Limitations section | "Testing on real data, UCI Adult and COMPAS, exposed four mistakes we fixed. Limits: it is a screening aid, finds protected columns by name, and can miss unusual personal data. Next: intersectional groups, more languages and ID formats, and checking trained models. Files stay in memory for 30 minutes after last use and are never saved to disk." |

**Step 5 wording to match the badge.** After "Claude explains each finding in plain
language", add:
- Badge says **AI explanations**: "These explanations on screen were written by Claude."
- Badge says **Template explanations**: "This live site runs without a key, so you see the
  built-in explanations; with a key, Claude writes them." Do not claim the screen shows Claude's
  text when it does not.

**If you are over 2:00**, cut in this order: "First name is a proxy for gender, ZIP code for
race." in step 2, "in HTML or Markdown" in step 4, then "more languages and ID formats," in
step 6.

## Rulebook coverage

| Topic | Where |
|---|---|
| Problem | 0:00 |
| Main features | 0:15 to 1:22 |
| Technical approach | 1:22 |
| AI component | 1:22, plus the badge |
| Challenges | 1:42 (real-data testing found four mistakes; if you have time, add that free hosting has only 512 MB of memory) |
| Limitations | 1:42, README "Limitations" |
| Future improvements | 1:42 |

## Facts to have ready if a judge asks

- **Risk score:** each finding adds points by severity, with a cap per severity (critical 25
  each up to 60, high 10 up to 30, medium 3 up to 15, low 1 up to 5), total capped at 100.
  Critical is above 75, High above 50, Medium above 25.
- **Four-fifths rule:** a group whose favourable-outcome rate is below 80% of the best group's
  rate is flagged, but only if the gap is statistically significant (Fisher's exact test,
  p < 0.05). Gaps that could be chance are listed as low severity.
- **Which outcome is favourable:** guessed from the label's values and name. In a column named
  after a bad event (default, fraud, churn, recidivism) with 1/0 values, 0 is the favourable one.
  The finding says which value was used.
- **What Claude receives:** finding ids, categories, severities, titles, column names and
  aggregate numbers, plus row count, column count and risk score. No cell values, rows, group
  labels or label values. A test (`tests/test_explainer.py`) checks this.
- **What if the AI fails:** timeout, error, refusal or invalid output all fall back to templates,
  and the badge says so. The app is fully usable without a key.
- **Real-data check:** README section "Accuracy check on real data". Numbers matched independent
  pandas results; four problems were found and fixed; one false alarm remains (a COMPAS
  charge-description column reported as names).
- **Where uploads live:** server memory only, expiring 30 minutes after last use, at most 10 analyses kept on the
  free host (50 by default), cleared on restart. Never written to disk or logged.
- **Limits:** 10 MB, 50,000 rows and 100 columns per file; comma-separated files only.
