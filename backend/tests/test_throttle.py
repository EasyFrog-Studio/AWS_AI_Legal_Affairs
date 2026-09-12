"""Tests use fake clock/sleep only; no real time.sleep is invoked."""
import threading

import pytest

from app.throttle import RateGate


def test_first_acquire_does_not_sleep():
    sleeps = []
    gate = RateGate(1.0, clock=lambda: 100.0, sleep=sleeps.append)

    gate.acquire()

    assert sleeps == []


def test_second_acquire_sleeps_remaining():
    for min_interval, elapsed, expected_sleep in [
        (1.0, 0.25, 0.75),
        (2.5, 0.5, 2.0),
    ]:
        times = iter([0.0, elapsed])
        sleeps = []
        gate = RateGate(min_interval, clock=lambda: next(times), sleep=sleeps.append)

        gate.acquire()
        gate.acquire()

        assert sleeps == [expected_sleep]


def test_no_sleep_when_interval_already_elapsed():
    times = iter([0.0, 5.0])
    sleeps = []
    gate = RateGate(1.0, clock=lambda: next(times), sleep=sleeps.append)

    gate.acquire()
    gate.acquire()

    assert sleeps == []


def test_zero_interval_never_sleeps():
    times = iter([0.0, 0.0, 0.0])
    sleeps = []
    gate = RateGate(0.0, clock=lambda: next(times), sleep=sleeps.append)

    gate.acquire()
    gate.acquire()
    gate.acquire()

    assert sleeps == []


def test_negative_interval_rejected():
    with pytest.raises(ValueError):
        RateGate(-0.1)


def test_concurrent_acquires_are_serialised():
    min_interval = 0.05
    state_lock = threading.Lock()
    state = {"t": 0.0}

    def clock():
        with state_lock:
            return state["t"]

    def sleep(seconds):
        with state_lock:
            state["t"] += seconds

    gate = RateGate(min_interval, clock=clock, sleep=sleep)

    results = []
    results_lock = threading.Lock()

    def worker():
        gate.acquire()
        with results_lock:
            results.append(clock())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    results.sort()
    for earlier, later in zip(results, results[1:]):
        assert later - earlier >= min_interval - 1e-9
