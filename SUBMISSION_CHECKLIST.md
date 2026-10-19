# Submission checklist: NYLP AI Hackathon 2026

Items come from the rulebook checklist. First checked on 19 Oct 2026 at about 18:56 PKT at
commit `9a6db86`, then updated at about 19:55 PKT after the live deploy on Railway. Final push
target: 11:00 PM PKT; the submission form closes at 11:59 PM PKT on 19 Oct 2026.

**Status meanings.** PASS: checked today, with evidence. FAIL: checked and not met yet.
OPEN: cannot be decided yet because it depends on the live URL, the team's names, a person, or
something that happens later. Re-check every row before the final push; the evidence column says
where. More detail for the code-side items is in `AUDIT_REPORT.md`.

**Count: 22 PASS, 2 FAIL, 7 OPEN (31 items).**

## Submission package

| Item | Status | Evidence / what is left |
|---|---|---|
| Core features work end to end | PASS | 118 tests pass, including 6 end-to-end tests that start the real server. Hiring sample: 8 bias, 4 missing-data and 4 PII findings, risk 100/100; clean sample: 0 findings. Browser re-check on 18 Oct in `tests/MANUAL_TEST_LOG.md` |
| Working product link (live URL opens in an incognito window and on a phone) | OPEN | https://dataset-auditor-production.up.railway.app/ works: health ok, both samples, both data cards, 375 px layout, no console errors (`tests/MANUAL_TEST_LOG.md`, "Live deployment"). Still to do by a person: open it in an incognito window and on a real phone |
| Demo: video link or live link, opens without login | PASS | The live link needs no login (checked in a fresh browser tab and with plain HTTP requests). No video yet (README.md:6 is a TODO); `DEMO_SCRIPT.md` is ready |
| Written description of the project | PASS | README.md "Problem" (l.11) and "Solution" (l.18). Paste a short version into the form |
| Repository is public, or judges have access | FAIL | Private: an anonymous request to the GitHub API returns 404. On GitHub: Settings > General > Danger Zone > Change repository visibility > Make public, or Settings > Collaborators to invite each judge. Then open the URL in incognito |
| Official submission form completed | FAIL | Not started. Needs the repository URL, live URL, video link, team details and the description |

## README contents

| Item | Status | Evidence / what is left |
|---|---|---|
| Problem | PASS | README.md:11 |
| Solution | PASS | README.md:18 |
| Tech stack | PASS | README.md:53 |
| Setup steps | PASS | README "Setup Instructions", with the real clone URL `https://github.com/moiz-111204/dataset_auditor.git` (works once the repo is public) |
| AI methodology | PASS | README.md:65-110: what each component does, the Claude workflow, and the template fallback |
| Model and API usage (every third-party AI API listed) | PASS | Presidio + spaCy `en_core_web_sm` and the Anthropic Claude API, each with the data it receives. Matches the code: the only AI imports are `anthropic` (explainer.py:140) and `presidio_analyzer` (pii_scanner.py:230) |
| Screenshots | PASS | Six screenshots of the live site in `docs/screenshots/` (upload page, results dashboard, Bias, Missing data and PII tabs, HTML data card), embedded in the README "Screenshots" section |
| Team contributions with real names and roles | PASS | README "Team Contributions": Team Doxa, Muhammad Moiz Ali Khan (backend, AI layer, deployment) and Rimsha (frontend, testing, demo video), as confirmed by the team |

## AI and quality

| Item | Status | Evidence / what is left |
|---|---|---|
| AI or ML plays a meaningful role | PASS | Presidio NER runs in the app (`_ner_matches`, pii_scanner.py:283); Claude explains findings when a key is set (`explain_findings`, explainer.py:417). Caveat: the Claude path has never run against the real API (`tests/MANUAL_TEST_LOG.md` row "Run with an API key" is Pending), and the live site shows template explanations unless a key is set |
| All AI output was tested and reviewed by the team | OPEN | 118 tests and browser checks exist, but README.md says the team reviewed all code. The team has to actually do that review before submitting |
| Team can explain architecture, prompts and workflows, model usage and limitations | OPEN | A person's task. Prepare with the README Architecture section, the prompt constants at the top of `backend/ai/explainer.py`, and "Facts to have ready" in `DEMO_SCRIPT.md` |
| Limitations section is written | PASS | README.md:415 onward |

## Ethics, privacy and security

| Item | Status | Evidence / what is left |
|---|---|---|
| Project is legal, ethical and responsible | PASS | Only synthetic samples are in the repository, with fake emails, phones and SSN-style IDs. The README calls it a screening aid, not a compliance guarantee. The two public datasets used for accuracy testing are linked, not redistributed |
| No malware, phishing, credential theft, unauthorized access, data theft or harassment | PASS | The only outbound network call in the backend is the optional Anthropic request; the app reads nothing except the uploaded CSV (`AUDIT_REPORT.md` items 2 and 7) |
| User privacy and data protection respected | PASS | Uploads stay in memory (`test_upload_stays_in_memory`); logs carry exception type names only; the LLM payload is aggregate numbers only (`test_outgoing_payload_contains_no_raw_cell_values`) |
| No API keys, passwords or .env files in the repository | PASS | `git grep` over all 18 commits finds no key; `.env` is ignored; only `.env.example` is tracked, with an empty key |

## Originality and team

| Item | Status | Evidence / what is left |
|---|---|---|
| Built for this hackathon; no old projects | PASS | The first commit is `db957a3` on 2026-10-17 08:08; nothing older in the history; README states it was built fresh |
| No copied project, bought project, stolen code or existing commercial product | PASS | No foreign licence headers, "copied from" notes or vendored files. Limit: git cannot prove authorship, and the README discloses that Claude Code assisted |
| Open-source libraries, templates, APIs and datasets are license-permitted and acknowledged | PASS | `THIRD_PARTY.md` lists all 14 libraries plus Chart.js, with versions and permissive licenses; the bundled sample data is synthetic. Note: the licence terms of UCI Adult and COMPAS (used only for local testing, linked in the README) are not recorded in `THIRD_PARTY.md`; check them and add a line if you want them listed |
| Team has at most 5 members and each made a meaningful contribution | PASS | Two members, each with a stated role (README "Team Contributions") |
| Team and participant information is accurate | OPEN | The README matches what the team confirmed; enter exactly the same names, team name and roles in the submission form |

## Deadline and conduct

| Item | Status | Evidence / what is left |
|---|---|---|
| Final commit pushed before the deadline (target 11:00 PM PKT) | OPEN | Latest pushed commit is `9a6db86` (18 Oct). More commits are expected (live URL, screenshots, team names, video link). After the last push, run `git log -1 --format=%cd` and check it |
| No commits or changes after the deadline | OPEN | All 18 commits so far are dated 17-18 Oct 2026. Stays open until the deadline passes |
| Git history and timestamps not manipulated | PASS | 18 small incremental commits by one author; `git reflog` has no reset, rebase, amend or force entries |
| Demo covers: problem, main features, technical approach, AI component, challenges, limitations, future improvements | OPEN | `DEMO_SCRIPT.md` covers all seven in about 2 minutes. Becomes PASS once the video is recorded and checked against it |

## Order of work

Done: live deploy on Railway, live checks, URL and screenshots in the README, clone line fixed,
team Doxa's names and roles in the README.

1. **You:** make the repository public, or invite the judges.
2. **You:** open the live link in an incognito window and on a real phone.
3. **Rimsha:** record the video from `DEMO_SCRIPT.md`; send me the link for the README.
4. **You:** fill in the official form, with the same team details as the README.
5. **Final audit and push by 11:00 PM PKT.** Update the OPEN rows above, commit this file, and stop
   committing.

`AUDIT_REPORT.md` is not committed. Decide whether it should be public before running
`git add -A`.
