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
| NumPy | 2.5.4 | BSD-3-Clause | Numeric computation |
| SciPy | 1.18.1 | BSD-3-Clause | Statistical tests (chi-squared) |
| Pydantic | 2.14.1 | MIT | Data models and validation |
| pytest | 9.1.1 | MIT | Test runner |
| HTTPX | 0.28.1 | BSD-3-Clause | HTTP client for tests |
| Presidio Analyzer (Microsoft) | 2.2.364 | MIT | ML/NER-based PII detection |
| spaCy | 3.8.16 | MIT | NLP engine used by Presidio |
| en_core_web_sm (spaCy model) | 3.8.0 | MIT | English NER model used by Presidio |
