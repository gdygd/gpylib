"""Tests for src/gpylib/process2.py"""

import os
import sys
import signal
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from process2 import (
    Process, RunBase, RST_OK, RST_UNEXIST, RST_ABNORMAL, MAX_WAIT_COUNT
)

# ---------------------------------------------------------------------------
# Helper: a long-running subprocess target
# ---------------------------------------------------------------------------
SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(30)"]


# ---------------------------------------------------------------------------
# RunBase unit tests
# ---------------------------------------------------------------------------

class TestRunBase:
    def test_register_sets_id(self):
        rb = RunBase()
        rb._register(1234)
        assert rb.id == 1234

    def test_deregister_clears_id(self):
        rb = RunBase()
        rb._register(999)
        rb.active = True
        rb._deregister(999)
        assert rb.id == 0
        assert rb.active is False

    def test_deregister_wrong_id_is_noop(self):
        rb = RunBase()
        rb._register(5)
        rb.active = True
        rb._deregister(99)
        assert rb.id == 5
        assert rb.active is True

    def test_update_run_info_tracks_elapsed(self):
        rb = RunBase()
        rb.mark_time()
        time.sleep(0.05)
        rb.update_run_info()
        assert rb._run_info.elapsed_ms >= 40

    def test_update_run_info_max_elapsed(self):
        rb = RunBase()
        rb.mark_time()
        time.sleep(0.1)
        rb.update_run_info()
        first_max = rb._run_info.max_elapsed_ms

        rb.mark_time()
        time.sleep(0.01)
        rb.update_run_info()
        assert rb._run_info.max_elapsed_ms == first_max  # shorter run doesn't lower max

    def test_update_run_info_increments_wdc(self):
        rb = RunBase()
        rb.mark_time()
        rb.update_run_info()
        rb.mark_time()
        rb.update_run_info()
        # wdc is reset by _check_run_info; just confirm it's positive before check
        assert rb._run_info.wdc == 2

    def test_check_run_info_healthy_with_heartbeat(self):
        rb = RunBase()
        rb.mark_time()
        rb.update_run_info()
        assert rb._check_run_info() is True

    def test_check_run_info_triggers_after_max_wait(self):
        rb = RunBase()
        # Never call update_run_info → wdc stays 0
        for _ in range(MAX_WAIT_COUNT + 2):
            result = rb._check_run_info()
        assert result is False

    def test_thread_safe_concurrent_update(self):
        rb = RunBase()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(100):
                    rb.mark_time()
                    time.sleep(0.001)
                    rb.update_run_info()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Process lifecycle tests
# ---------------------------------------------------------------------------

class TestProcessDetach:
    def test_start_detach_returns_valid_pid(self):
        proc = Process("sleep", SLEEP_CMD)
        ok, pid = proc.start_detach()
        assert ok is True
        assert pid > 0
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_start_detach_is_exist(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start_detach()
        assert proc.is_exist() is True
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_start_detach_new_session(self):
        """Detached process must be in its own session (sid == pid)."""
        proc = Process("sleep", SLEEP_CMD)
        ok, pid = proc.start_detach()
        sid = os.getsid(pid)
        assert sid == pid           # new session → sid equals the process's own pid
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_start_detach_kill(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start_detach()
        assert proc.kill() is True
        proc.wait(timeout=2.0)
        time.sleep(0.1)
        assert proc.is_exist() is False


class TestProcessLifecycle:
    def test_start_returns_valid_pid(self):
        proc = Process("sleep", SLEEP_CMD)
        ok, pid = proc.start()
        assert ok is True
        assert pid > 0
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_get_pid_matches_start_pid(self):
        proc = Process("sleep", SLEEP_CMD)
        ok, pid = proc.start()
        assert proc.get_pid() == pid
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_start_invalid_command_returns_false(self):
        proc = Process("no_such_binary_xyz", ["no_such_binary_xyz"])
        ok, pid = proc.start()
        assert ok is False
        assert pid == -1

    def test_is_exist_true_after_start(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        assert proc.is_exist() is True
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_is_exist_false_after_kill(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        proc.kill()
        proc.wait(timeout=2.0)
        time.sleep(0.1)
        assert proc.is_exist() is False

    def test_is_exist_false_when_no_pid(self):
        proc = Process("sleep", SLEEP_CMD)
        assert proc.is_exist() is False

    def test_kill_returns_true_on_alive_process(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        result = proc.kill()
        proc.wait(timeout=2.0)
        assert result is True

    def test_kill_force_returns_true_on_alive_process(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        result = proc.kill_force()
        proc.wait(timeout=2.0)
        assert result is True

    def test_kill_returns_true_when_already_gone(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        proc.kill_force()
        proc.wait(timeout=2.0)
        time.sleep(0.1)
        # Second kill on already-dead process
        result = proc.kill()
        assert result is True

    def test_wait_returns_exit_code(self):
        proc = Process("exit0", [sys.executable, "-c", "raise SystemExit(0)"])
        proc.start()
        code = proc.wait(timeout=3.0)
        assert code == 0

    def test_wait_returns_none_when_not_started(self):
        proc = Process("sleep", SLEEP_CMD)
        assert proc.wait(timeout=0.1) is None


# ---------------------------------------------------------------------------
# is_running tests
# ---------------------------------------------------------------------------

class TestIsRunning:
    def test_is_running_true_on_live_process(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        ok, state = proc.is_running()
        assert ok is True
        assert state == RST_OK
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_is_running_unexist_when_not_started(self):
        proc = Process("sleep", SLEEP_CMD)
        ok, state = proc.is_running()
        assert ok is False
        assert state == RST_UNEXIST

    def test_is_running_unexist_after_kill(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        proc.kill_force()
        proc.wait(timeout=2.0)
        time.sleep(0.1)
        # Force past the throttle
        proc._check_timer = 0.0
        ok, state = proc.is_running()
        assert ok is False
        assert state == RST_UNEXIST

    def test_is_running_throttled_within_1s(self):
        """Second call within 1s (process still alive) is throttled → RST_OK."""
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        proc.is_running()   # first call: full check, resets timer
        # Second call immediately — throttled, returns True without re-checking
        ok, state = proc.is_running()
        assert ok is True and state == RST_OK
        proc.kill_force()
        proc.wait(timeout=2.0)

    def test_watchdog_abnormal_without_heartbeat(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        # Drain wait_count past MAX_WAIT_COUNT without any update_run_info
        for _ in range(MAX_WAIT_COUNT + 2):
            proc._check_timer = 0.0
            ok, state = proc.is_running()
            if state == RST_ABNORMAL:
                break
        assert state == RST_ABNORMAL
        proc.kill_force()
        proc.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# PID registration
# ---------------------------------------------------------------------------

class TestPidRegistration:
    def test_register_pid_manually(self):
        proc = Process("manual", SLEEP_CMD)
        proc.register_pid(9999)
        assert proc.get_pid() == 9999
        assert proc._base.active is True

    def test_deregister_pid(self):
        proc = Process("manual", SLEEP_CMD)
        proc.register_pid(9999)
        proc.deregister(9999)
        assert proc.get_pid() == 0
        assert proc._base.active is False

    def test_is_active_pid(self):
        proc = Process("sleep", SLEEP_CMD)
        ok, pid = proc.start()
        assert proc.is_active_pid(pid) is True
        assert proc.is_active_pid(pid + 1) is False
        proc.kill_force()
        proc.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# Watchdog / timing
# ---------------------------------------------------------------------------

class TestWatchdog:
    def test_elapsed_ms_zero_before_update(self):
        proc = Process("sleep", SLEEP_CMD)
        assert proc.elapsed_ms == 0

    def test_elapsed_and_max_via_mark_update(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.mark_time()
        time.sleep(0.05)
        proc.update_run_info()
        assert proc.elapsed_ms >= 40
        assert proc.max_elapsed_ms >= 40

    def test_thread_safe_concurrent_is_running(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.start()
        results: list[tuple[bool, int]] = []
        lock = threading.Lock()

        def checker():
            for _ in range(20):
                r = proc.is_running()
                with lock:
                    results.append(r)
                time.sleep(0.01)

        threads = [threading.Thread(target=checker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        proc.kill_force()
        proc.wait(timeout=2.0)
        assert all(isinstance(r, tuple) for r in results)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

class TestMisc:
    def test_set_debug_level(self):
        proc = Process("sleep", SLEEP_CMD)
        proc.set_debug_level(5)
        assert proc.debug_level == 5

    def test_name_and_cmd_stored(self):
        proc = Process("myproc", ["echo", "hi"])
        assert proc.name == "myproc"
        assert proc.cmd == ["echo", "hi"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
