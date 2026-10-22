"""Per-stage wall-clock timing for request paths whose latency we need to break down."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def timed(timings: dict[str, int], stage: str) -> Iterator[None]:
    """Record the block's wall-clock duration in milliseconds as ``timings[stage]``.

    Records on failure too, so a log line written from an error path still shows
    how long the failing stage ran. Safe to share one dict across threads: each
    stage writes its own key.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[stage] = round((time.perf_counter() - start) * 1000)


def format_timings(timings: dict[str, int]) -> str:
    """``stage=12ms`` pairs in recording order, for one greppable log line."""
    return " ".join(f"{stage}={ms}ms" for stage, ms in timings.items())
