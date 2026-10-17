# Third-Party Libraries

Every open-source library this project depends on directly, with the pinned version from
`requirements.txt` and its license. All licenses are permissive.

| Library | Version | License | Used for |
|---|---|---|---|
| FastAPI | 0.143.3 | MIT | Web API framework |
| Uvicorn | 0.54.0 | BSD-3-Clause | ASGI server |
| python-multipart | 0.0.32 | Apache-2.0 | Parsing file uploads |
| python-dotenv | 1.2.4 | BSD-3-Clause | Loading `.env` for local development |
| pandas | 3.0.6 | BSD-3-Clause | Reading and analyzing CSV data |
| NumPy | 2.4.6 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) | Numeric computation |
| SciPy | 1.18.1 | BSD-3-Clause | Statistical tests (chi-squared) |
| Pydantic | 2.14.1 | MIT | Data models and validation |
| pytest | 9.1.1 | MIT | Test runner |
| HTTPX | 0.28.1 | BSD-3-Clause | HTTP client for tests |
| Presidio Analyzer (Microsoft) | 2.2.364 | MIT | ML/NER-based PII detection |
| spaCy | 3.8.16 | MIT | NLP engine used by Presidio |
| en_core_web_sm (spaCy model) | 3.8.0 | MIT | English NER model used by Presidio |
| Anthropic Python SDK | 1.15.0 | MIT | Calling the Claude API for plain-language explanations |

## Frontend (loaded from a CDN, not in requirements.txt)

| Library | Version | License | Used for |
|---|---|---|---|
| Chart.js | 4.4.4 | MIT | Bar charts in the dashboard (`dist/chart.umd.js` loaded from cdn.jsdelivr.net with an SRI integrity hash) |

## Models and services

| Name | License / terms | Used for |
|---|---|---|
| spaCy `en_core_web_sm` 3.8.0 | MIT | Named-entity recognition inside Presidio |
| Anthropic Claude API (`claude-opus-5-5` by default) | Anthropic commercial terms (API service, not a bundled library) | Optional plain-language explanations |
