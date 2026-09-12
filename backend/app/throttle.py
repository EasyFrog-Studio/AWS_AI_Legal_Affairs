"""行程內的請求速率閘:保證相鄰兩次 acquire() 返回之間至少間隔指定秒數。"""
import threading
import time
from typing import Callable

from app.config import settings


class RateGate:
    """鎖涵蓋 sleep,並行呼叫者排成一列而不是一起醒來。"""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval 不得為負")
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def acquire(self) -> None:
        with self._lock:
            if self._last is None:
                self._last = self._clock()
                return
            now = self._clock()
            remaining = self._min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                self._last += self._min_interval
            else:
                self._last = now


bedrock_gate = RateGate(settings.BEDROCK_MIN_INTERVAL_SECONDS)
