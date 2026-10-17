# Dataset Bias & Privacy Auditor

Hackathon project for the NYLP AI Hackathon 2026. Final push by 19 Oct 2026, 11:00 PM PKT
(official deadline 11:59 PM PKT).

## Rules for every change

- Stack: Python 3.11+, FastAPI + uvicorn, pandas, scipy, pydantic, vanilla HTML/CSS/JS frontend
  with Chart.js. Do not switch frameworks.
- Privacy: uploaded files are processed in memory only. Never write uploads to disk, a database
  or logs. Never put actual PII values in findings, logs, data cards or API payloads.
- Secrets: read keys from environment variables only. Never commit `.env`. Keep `.env.example`
  and `.gitignore` up to date.
- Honesty: every README claim must match what the code really does. Make small, honest commits.
  Never rewrite git history.
- Testing: run the tests for each step and fix failures before moving on. Do not leave untested
  output.
- Originality: built fresh for this hackathon. Only use open-source libraries with permissive
  licenses and record each one (name, version, license) in `THIRD_PARTY.md` as you add it.

## Layout

- `backend/` — FastAPI app (`app.py`), pipeline (`orchestrator.py`), in-memory sessions
  (`session_store.py`), analyzers, AI explainer, data card generator, Pydantic schemas.
- `frontend/` — static single-page dashboard served by the backend.
- `sample_data/` — synthetic CSVs and their deterministic generator.
- `tests/` — pytest suite. Run with `venv\Scripts\python -m pytest`.

## Commands

- Run the app: `venv\Scripts\python backend\app.py` (port from `PORT`, default 8000)
- Run tests: `venv\Scripts\python -m pytest -q`
