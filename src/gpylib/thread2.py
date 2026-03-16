"""
thread2.py - Thread wrapper with watchdog monitoring

Ported from Go runBase.go / thread.go.

Features:
- Auto-assigned thread IDs with gap-filling reuse
- Kill signal via threading.Event
- Watchdog: detects hung threads via UpdateRunInfo heartbeat
- Elapsed time tracking (current / max per cycle)
- Thread-safe
"""

import threading
import time
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
THR_START_ID = 1
THR_MAX_ID = 10_000
MAX_WAIT_COUNT = 5          # watchdog miss tolerance
_DEF_CHECK_INTERVAL_MS = 1000  # IsRunning check period

RST_OK = 1       # thread healthy
RST_UNEXIST = 2  # thread not registered
RST_ABNORMAL = 3  # watchdog triggered

# ThreadFunc signature:
#   def my_func(t: Thread, kill: threading.Event, arg1, arg2, arg3) -> None
ThreadFunc = Callable[["Thread", threading.Event, Any, Any, Any], None]

# ---------------------------------------------------------------------------
# ID pool (module-level, thread-safe)
# ---------------------------------------------------------------------------
_id_pool: list[int] = []
_id_lock = threading.Lock()


def _acquire_id() -> int:
    """Return the smallest available thread ID and add it to the pool."""
    with _id_lock:
        if not _id_pool:
            _id_pool.append(THR_START_ID)
            return THR_START_ID

        _id_pool.sort()
        new_id = THR_START_ID
        for i in range(1, len(_id_pool)):
            prev, cur = _id_pool[i - 1], _id_pool[i]
            if cur - prev > 1:
                new_id = prev + 1
                break
        else:
            new_id = _id_pool[-1] + 1

        _id_pool.append(new_id)
        return new_id


def _release_id(id_: int) -> None:
    """Return *id_* to the pool."""
    with _id_lock:
        try:
            _id_pool.remove(id_)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# RunInfo
# ---------------------------------------------------------------------------
class RunInfo:
    """Internal watchdog and timing state."""

    def __init__(self) -> None:
        self.wdc: int = 0           # watch-dog counter (heartbeat)
        self.prev_wdc: int = 0
        self.elapsed_ms: int = 0    # last cycle elapsed time
        self.wait_count: int = 0    # consecutive misses
        self.max_elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# RunBase
# ---------------------------------------------------------------------------
class RunBase:
    """Base tracking state shared by Thread."""

    def __init__(self) -> None:
        self.id: int = 0
        self.active: bool = False
        self._run_info = RunInfo()
        self._mark_time: float = 0.0
        self._lock = threading.Lock()

    def mark_time(self) -> None:
        """Record the start of a work cycle (call at top of loop)."""
        self._mark_time = time.monotonic()

    def update_run_info(self) -> None:
        """Record the end of a work cycle (call at bottom of loop).

        Increments the watchdog counter and records elapsed time.
        Must be called each iteration for :meth:`Thread.is_running` to
        report healthy.
        """
        with self._lock:
            elapsed = time.monotonic() - self._mark_time
            self._run_info.elapsed_ms = int(elapsed * 1000)
            if self._run_info.elapsed_ms > self._run_info.max_elapsed_ms:
                self._run_info.max_elapsed_ms = self._run_info.elapsed_ms
            self._run_info.wdc += 1
            self._run_info.wait_count = 0

    def _check_run_info(self) -> bool:
        """Watchdog check. Returns False when thread appears hung."""
        with self._lock:
            self._run_info.prev_wdc = self._run_info.wdc
            self._run_info.wdc = 0
            hung = (self._run_info.prev_wdc == 0
                    and self._run_info.wait_count > MAX_WAIT_COUNT)
            self._run_info.wait_count += 1
        return not hung

    def _register(self, id_: int) -> None:
        self.id = id_

    def _deregister(self, id_: int) -> None:
        if self.id == id_:
            self.id = 0
            self.active = False


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------
class Thread:
    """Managed thread with kill signal and watchdog monitoring.

    Typical usage::

        def worker(t: Thread, kill: threading.Event, arg1, arg2, arg3):
            while not kill.is_set():
                t.mark_time()
                # ... do work ...
                t.update_run_info()
                kill.wait(t.interval / 1000)   # sleep interval ms

        thr = Thread()
        thr.init(worker, interval=100, arg1="hello")
        thr.start()

        # health-check from another thread:
        ok, state = thr.is_running()

        thr.kill()
        thr.join()
    """

    def __init__(self) -> None:
        self.interval: int = 0   # milliseconds
        self.arg1: Any = None
        self.arg2: Any = None
        self.arg3: Any = None

        self._kill_event = threading.Event()
        self._start_func: Optional[ThreadFunc] = None
        self._thread: Optional[threading.Thread] = None
        self._check_timer: float = 0.0   # for is_running throttle

        self._base = RunBase()
        self._base._register(_acquire_id())

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> int:
        return self._base.id

    @property
    def active(self) -> bool:
        return self._base.active

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def init(self, func: ThreadFunc, interval: int, *args: Any) -> None:
        """Configure the thread before calling :meth:`start`.

        Args:
            func:     Worker function ``(t, kill_event, arg1, arg2, arg3)``.
            interval: Suggested loop period in milliseconds (passed to func
                      via ``t.interval``; the func is responsible for sleeping).
            *args:    Up to three positional arguments forwarded to *func*.
        """
        self._start_func = func
        self.interval = interval
        for i, arg in enumerate(args[:3]):
            setattr(self, f"arg{i + 1}", arg)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread (50 ms warm-up delay, matching Go)."""
        if self._start_func is None:
            raise RuntimeError("Call init() before start()")
        self._base.active = True
        self._kill_event.clear()
        time.sleep(0.05)
        self._thread = threading.Thread(
            target=self._start_func,
            args=(self, self._kill_event, self.arg1, self.arg2, self.arg3),
            daemon=True,
        )
        self._thread.start()

    def kill(self) -> None:
        """Signal the worker to stop."""
        self._base.active = False
        self._kill_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the worker thread to finish."""
        if self._thread is not None:
            self._thread.join(timeout)

    def __del__(self) -> None:
        _release_id(self._base.id)

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def is_running(self) -> tuple[bool, int]:
        """Check whether the thread is alive and making progress.

        Performs the watchdog check at most once per second.

        Returns:
            ``(healthy, state)`` where *state* is one of
            ``RST_OK``, ``RST_UNEXIST``, ``RST_ABNORMAL``.
        """
        if not self._base.active:
            return False, RST_UNEXIST

        now = time.monotonic()
        elapsed_ms = (now - self._check_timer) * 1000
        if elapsed_ms < _DEF_CHECK_INTERVAL_MS:
            return True, RST_OK

        self._check_timer = now
        if not self._base._check_run_info():
            return False, RST_ABNORMAL

        return True, RST_OK

    # ------------------------------------------------------------------
    # Forwarded timing helpers (convenience)
    # ------------------------------------------------------------------

    def mark_time(self) -> None:
        """Shortcut for ``RunBase.mark_time``; call at top of loop."""
        self._base.mark_time()

    def update_run_info(self) -> None:
        """Shortcut for ``RunBase.update_run_info``; call at bottom of loop."""
        self._base.update_run_info()

    @property
    def elapsed_ms(self) -> int:
        """Elapsed time of the last work cycle in milliseconds."""
        return self._base._run_info.elapsed_ms

    @property
    def max_elapsed_ms(self) -> int:
        """Maximum observed cycle elapsed time in milliseconds."""
        return self._base._run_info.max_elapsed_ms
