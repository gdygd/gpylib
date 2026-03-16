"""Tests for src/gpylib/thread2.py"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from thread2 import (
    Thread, RunBase, RunInfo,
    RST_OK, RST_UNEXIST, RST_ABNORMAL,
    MAX_WAIT_COUNT, _acquire_id, _release_id, _id_pool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_worker(t: Thread, kill: threading.Event, arg1, arg2, arg3):
    """Worker that loops at t.interval ms, calling mark/update each cycle."""
    while not kill.is_set():
        t.mark_time()
        kill.wait(t.interval / 1000)
        t.update_run_info()


def _noop_worker(t: Thread, kill: threading.Event, arg1, arg2, arg3):
    """Worker that does nothing (no update_run_info → watchdog will trip)."""
    kill.wait()  # block until killed


# ---------------------------------------------------------------------------
# ID pool
# ---------------------------------------------------------------------------

class TestIDPool:
    def test_ids_are_unique(self):
        t1, t2, t3 = Thread(), Thread(), Thread()
        ids = {t1.id, t2.id, t3.id}
        assert len(ids) == 3
        t1.kill(); t2.kill(); t3.kill()

    def test_ids_start_at_1(self):
        # Clear pool and get a fresh id
        from thread2 import _id_pool, _id_lock
        with _id_lock:
            saved = _id_pool.copy()
            _id_pool.clear()
        t = Thread()
        assert t.id == 1
        with _id_lock:
            _id_pool.clear()
            _id_pool.extend(saved)

    def test_id_reuse_after_release(self):
        from thread2 import _id_pool, _id_lock
        with _id_lock:
            saved = _id_pool.copy()
            _id_pool.clear()

        t1 = Thread()  # id=1
        id1 = t1.id
        _release_id(id1)
        with _id_lock:
            _id_pool.remove(id1) if id1 in _id_pool else None

        t2 = Thread()  # should reuse id=1
        assert t2.id == id1

        with _id_lock:
            _id_pool.clear()
            _id_pool.extend(saved)


# ---------------------------------------------------------------------------
# RunBase
# ---------------------------------------------------------------------------

class TestRunBase:
    def test_mark_and_update_elapsed(self):
        rb = RunBase()
        rb.mark_time()
        time.sleep(0.05)
        rb.update_run_info()
        assert rb._run_info.elapsed_ms >= 40  # at least 40ms

    def test_update_increments_wdc(self):
        rb = RunBase()
        rb.mark_time()
        rb.update_run_info()
        rb.mark_time()
        rb.update_run_info()
        # wdc was incremented twice; after check it resets
        assert rb._run_info.wdc == 2

    def test_max_elapsed_tracked(self):
        rb = RunBase()
        rb.mark_time()
        time.sleep(0.01)
        rb.update_run_info()
        first_max = rb._run_info.max_elapsed_ms

        rb.mark_time()
        time.sleep(0.05)
        rb.update_run_info()
        assert rb._run_info.max_elapsed_ms >= first_max

    def test_check_run_info_healthy(self):
        rb = RunBase()
        rb.mark_time()
        rb.update_run_info()   # wdc=1
        assert rb._check_run_info() is True

    def test_check_run_info_hung(self):
        rb = RunBase()
        # Never call update_run_info → wdc stays 0
        for _ in range(MAX_WAIT_COUNT + 1):
            rb._check_run_info()
        assert rb._check_run_info() is False

    def test_deregister_clears_id(self):
        rb = RunBase()
        rb._register(42)
        rb.active = True
        rb._deregister(42)
        assert rb.id == 0
        assert rb.active is False

    def test_deregister_wrong_id_noop(self):
        rb = RunBase()
        rb._register(42)
        rb.active = True
        rb._deregister(99)   # wrong id
        assert rb.id == 42
        assert rb.active is True


# ---------------------------------------------------------------------------
# Thread lifecycle
# ---------------------------------------------------------------------------

class TestThreadLifecycle:
    def test_start_sets_active(self):
        t = Thread()
        t.init(_simple_worker, 50)
        t.start()
        assert t.active is True
        t.kill()
        t.join(timeout=1)

    def test_kill_clears_active(self):
        t = Thread()
        t.init(_simple_worker, 50)
        t.start()
        t.kill()
        t.join(timeout=1)
        assert t.active is False

    def test_thread_actually_runs(self):
        ran = threading.Event()

        def worker(t, kill, a1, a2, a3):
            ran.set()
            kill.wait()

        t = Thread()
        t.init(worker, 50)
        t.start()
        assert ran.wait(timeout=1)
        t.kill()
        t.join(timeout=1)

    def test_init_passes_args(self):
        received = {}

        def worker(t, kill, a1, a2, a3):
            received["a1"] = a1
            received["a2"] = a2
            received["a3"] = a3
            kill.wait()

        t = Thread()
        t.init(worker, 50, "hello", 42, [1, 2])
        t.start()
        time.sleep(0.1)
        t.kill()
        t.join(timeout=1)
        assert received == {"a1": "hello", "a2": 42, "a3": [1, 2]}

    def test_start_without_init_raises(self):
        t = Thread()
        try:
            t.start()
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Watchdog / IsRunning
# ---------------------------------------------------------------------------

class TestIsRunning:
    def test_healthy_thread_is_running(self):
        t = Thread()
        t.init(_simple_worker, 50)
        t.start()
        time.sleep(0.1)
        ok, state = t.is_running()
        assert ok is True
        assert state == RST_OK
        t.kill()
        t.join(timeout=1)

    def test_killed_thread_not_running(self):
        t = Thread()
        t.init(_simple_worker, 50)
        t.start()
        t.kill()
        t.join(timeout=1)
        ok, state = t.is_running()
        assert ok is False
        assert state == RST_UNEXIST

    def test_hung_thread_detected(self):
        t = Thread()
        t.init(_noop_worker, 50)   # never calls update_run_info
        t.start()

        # Force check_timer to be old enough each call
        for _ in range(MAX_WAIT_COUNT + 2):
            t._check_timer = 0.0   # reset throttle
            t.is_running()

        t._check_timer = 0.0
        ok, state = t.is_running()
        assert ok is False
        assert state == RST_ABNORMAL

        t.kill()
        t.join(timeout=1)

    def test_is_running_throttled(self):
        """is_running called twice rapidly should not double-count."""
        t = Thread()
        t.init(_simple_worker, 50)
        t.start()
        time.sleep(0.1)

        ok1, s1 = t.is_running()
        ok2, s2 = t.is_running()   # within 1s window → throttled
        assert ok1 is True and ok2 is True
        assert s1 == RST_OK and s2 == RST_OK

        t.kill()
        t.join(timeout=1)


# ---------------------------------------------------------------------------
# Elapsed time properties
# ---------------------------------------------------------------------------

class TestElapsedTime:
    def test_elapsed_ms_updates(self):
        t = Thread()
        t.init(_simple_worker, 30)
        t.start()
        time.sleep(0.15)
        t.kill()
        t.join(timeout=1)
        assert t.elapsed_ms >= 0

    def test_max_elapsed_ms_non_negative(self):
        t = Thread()
        t.init(_simple_worker, 30)
        t.start()
        time.sleep(0.1)
        t.kill()
        t.join(timeout=1)
        assert t.max_elapsed_ms >= 0


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_start_kill(self):
        errors: list[Exception] = []

        def run_one():
            try:
                t = Thread()
                t.init(_simple_worker, 20)
                t.start()
                time.sleep(0.05)
                t.kill()
                t.join(timeout=1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run_one) for _ in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Errors: {errors}"

    def test_concurrent_update_run_info(self):
        """Simultaneous update_run_info calls must not corrupt state."""
        rb = RunBase()
        errors: list[Exception] = []

        def updater():
            try:
                for _ in range(100):
                    rb.mark_time()
                    rb.update_run_info()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=updater) for _ in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
