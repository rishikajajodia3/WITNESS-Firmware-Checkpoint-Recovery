"""
Discrete-event virtual clock.

The whole simulation runs on simulated milliseconds, not real wall-clock
time. This is what lets the scale sweep run thousands of simulated nodes
and a 300-second baseline timeout without the demo actually taking five
minutes to execute -- and it makes every latency number exactly
reproducible instead of subject to OS scheduling noise.
"""

import heapq
from typing import Callable


class SimClock:
    def __init__(self):
        self._t = 0.0
        self._events: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0

    def now(self) -> float:
        return self._t

    def schedule(self, delay_ms: float, callback: Callable[[], None]) -> None:
        if delay_ms < 0:
            raise ValueError("delay_ms must be >= 0")
        self._seq += 1
        heapq.heappush(self._events, (self._t + delay_ms, self._seq, callback))

    def run_until_idle(self, max_time_ms: float | None = None) -> None:
        """Drain scheduled events in time order until none remain (or max_time_ms is hit)."""
        while self._events:
            t, seq, cb = self._events[0]
            if max_time_ms is not None and t > max_time_ms:
                break
            heapq.heappop(self._events)
            self._t = t
            cb()
