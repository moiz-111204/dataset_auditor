"""FastAPI entry point (placeholder)."""

from fastapi import FastAPI

app = FastAPI(title="Dataset Bias & Privacy Auditor")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
