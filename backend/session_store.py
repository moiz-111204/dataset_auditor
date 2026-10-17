"""In-memory session store.

Each analysis lives under a random uuid4 session id together with its DataFrame, settings and
report. Sessions expire 30 minutes after their last use and at most MAX_SESSIONS are kept
(default 50; the least recently used is dropped first). Nothing is written to disk, and there
is no global "latest result": every request must name its session.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from backend.models.schemas import AnalysisReport, AnalysisSettings

DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_MAX_SESSIONS = 50


def max_sessions_from_env() -> int:
    """MAX_SESSIONS from the environment (default 50, at least 1). Lower it on small hosts."""
    return max(1, int(os.getenv("MAX_SESSIONS", str(DEFAULT_MAX_SESSIONS))))


@dataclass
class Session:
    session_id: str
    dataset_name: str
    df: pd.DataFrame
    settings: AnalysisSettings
    report: AnalysisReport
    last_used: float = field(default=0.0)


class SessionStore:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_sessions: int = DEFAULT_MAX_SESSIONS,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def _purge_expired(self) -> None:
        now = self.clock()
        for sid in [sid for sid, s in self._sessions.items() if now - s.last_used > self.ttl_seconds]:
            del self._sessions[sid]

    def save(self, report: AnalysisReport, df: pd.DataFrame) -> Session:
        """Store (or replace) the session named by report.session_id."""
        with self._lock:
            self._purge_expired()
            session = Session(
                session_id=report.session_id,
                dataset_name=report.dataset_name,
                df=df,
                settings=report.detected_settings,
                report=report,
                last_used=self.clock(),
            )
            self._sessions[report.session_id] = session
            self._sessions.move_to_end(report.session_id)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            self._purge_expired()
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_used = self.clock()
                self._sessions.move_to_end(session_id)
            return session

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._sessions)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
