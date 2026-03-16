"""
process2.py - Subprocess management with watchdog monitoring

Ported from Go runBase.go / process.go.

Features:
- Start / kill / force-kill subprocess
- Process existence check via OS signal (kill -0)
- Watchdog: detects stuck processes via mark_time / update_run_info heartbeat
- Elapsed cycle time tracking (current / max)
- Thread-safe
"""

import os
import signal
import subprocess
import threading
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RST_OK = 1        # process healthy
RST_UNEXIST = 2   # process not found / not registered
RST_ABNORMAL = 3  # watchdog triggered (no heartbeat)

MAX_WAIT_COUNT = 5              # watchdog miss tolerance
_DEF_CHECK_INTERVAL_MS = 1000  # is_running throttle period (ms)


# ---------------------------------------------------------------------------
# RunInfo
# ---------------------------------------------------------------------------
class RunInfo:
    """Internal watchdog and timing state (not public API)."""

    def __init__(self) -> None:
        self.wdc: int = 0             # watchdog counter (incremented per heartbeat)
        self.prev_wdc: int = 0
        self.elapsed_ms: int = 0      # last cycle duration
        self.wait_count: int = 0      # consecutive checks with wdc == 0
        self.max_elapsed_ms: int = 0  # all-time max cycle duration


# ---------------------------------------------------------------------------
# RunBase
# ---------------------------------------------------------------------------
class RunBase:
    """Base tracking state: PID registration + watchdog/timing.

    Mirrors Go's RunBase struct.
    """

    def __init__(self) -> None:
        self.id: int = 0         # registered PID
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
        """
        with self._lock:
            elapsed = time.monotonic() - self._mark_time
            self._run_info.elapsed_ms = int(elapsed * 1000)
            if self._run_info.elapsed_ms > self._run_info.max_elapsed_ms:
                self._run_info.max_elapsed_ms = self._run_info.elapsed_ms
            self._run_info.wdc += 1
            self._run_info.wait_count = 0

    def _check_run_info(self) -> bool:
        """Watchdog check. Returns False when process appears stuck."""
        with self._lock:
            self._run_info.prev_wdc = self._run_info.wdc
            self._run_info.wdc = 0
            hung = (
                self._run_info.prev_wdc == 0
                and self._run_info.wait_count > MAX_WAIT_COUNT
            )
            self._run_info.wait_count += 1
        return not hung

    def _register(self, pid: int) -> None:
        self.id = pid

    def _deregister(self, pid: int) -> None:
        if self.id == pid:
            self.id = 0
            self.active = False


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------
class Process:
    """Managed subprocess with watchdog monitoring.

    Typical usage::

        proc = Process("worker", ["python3", "-c", "import time; time.sleep(30)"])
        ok, pid = proc.start()

        # from a monitor thread:
        ok, state = proc.is_running()

        proc.kill()
        proc.wait(timeout=3.0)

    Watchdog (optional) — call from inside the child process or a dedicated
    monitor that has access to this Process object::

        proc.mark_time()
        # ... do cycle work ...
        proc.update_run_info()
    """

    def __init__(self, name: str, cmd: list[str]) -> None:
        self.name = name
        self.cmd = cmd
        self.debug_level: int = 3

        self._proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._check_timer: float = 0.0
        self._base = RunBase()

    # ------------------------------------------------------------------
    # PID management
    # ------------------------------------------------------------------

    def register_pid(self, pid: int) -> None:
        """Manually register a PID (e.g., after external fork)."""
        self._base._register(pid)
        self._base.active = True

    def deregister(self, pid: int) -> None:
        """Deregister the given PID and mark process inactive."""
        self._base._deregister(pid)

    def get_pid(self) -> int:
        """Return the registered PID (0 if none)."""
        return self._base.id

    def is_active_pid(self, pid: int) -> bool:
        """Return True if *pid* matches the registered PID."""
        return self._base.id == pid

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> tuple[bool, int]:
        """Start the subprocess.

        Returns:
            ``(ok, pid)`` — *pid* is -1 on failure.
        """
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.DEVNULL,
            )
            pid = self._proc.pid
            self._base._register(pid)
            self._base.active = True
            return True, pid
        except OSError as exc:
            print(f"ERROR Unable to run '{self.name}': {exc}")
            return False, -1

    def start_detach(self) -> tuple[bool, int]:
        """Start the subprocess detached from the parent.

        The child runs in its own session (``setsid``), so it survives
        parent exit and is not tied to the parent's terminal or process group.
        stdin/stdout/stderr are redirected to ``/dev/null``.

        Returns:
            ``(ok, pid)`` — *pid* is -1 on failure.
        """
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,   # detach: new session / process group
                close_fds=True,
            )
            pid = self._proc.pid
            self._base._register(pid)
            self._base.active = True
            return True, pid
        except OSError as exc:
            print(f"ERROR Unable to run '{self.name}': {exc}")
            return False, -1

    def kill(self) -> bool:
        """Send SIGTERM to the process.

        Returns True if the signal was sent (or the process is already gone).
        """
        if not self.is_exist():
            return True
        try:
            os.kill(self._base.id, signal.SIGTERM)
            self._base.active = False
            return True
        except OSError:
            return False

    def kill_force(self) -> bool:
        """Send SIGKILL to the process (immediate, no cleanup)."""
        if not self.is_exist():
            return True
        try:
            os.kill(self._base.id, signal.SIGKILL)
            self._base.active = False
            return True
        except OSError:
            return False

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Wait for the process to exit.

        Returns:
            Exit code, or *None* on timeout / not started.
        """
        if self._proc is None:
            return None
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def is_exist(self) -> bool:
        """Return True if the registered PID is alive (via kill -0)."""
        pid = self._base.id
        if pid == 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but owned by another user

    def is_running(self) -> tuple[bool, int]:
        """Check process health (rate-limited to once per second).

        Checks:
        1. active flag
        2. process existence (kill -0)
        3. watchdog counter (if heartbeat is used)

        Returns:
            ``(healthy, state)`` where *state* is one of
            ``RST_OK``, ``RST_UNEXIST``, ``RST_ABNORMAL``.
        """
        if not self._base.active:
            return False, RST_UNEXIST

        now = time.monotonic()
        if (now - self._check_timer) * 1000 < _DEF_CHECK_INTERVAL_MS:
            return True, RST_OK

        self._check_timer = now

        if not self.is_exist():
            self._base.active = False
            return False, RST_UNEXIST

        if not self._base._check_run_info():
            return False, RST_ABNORMAL

        return True, RST_OK

    # ------------------------------------------------------------------
    # Watchdog / timing (forwarded from RunBase)
    # ------------------------------------------------------------------

    def mark_time(self) -> None:
        """Record start of a work cycle (call at top of monitored loop)."""
        self._base.mark_time()

    def update_run_info(self) -> None:
        """Record end of a work cycle (call at bottom of monitored loop)."""
        self._base.update_run_info()

    @property
    def elapsed_ms(self) -> int:
        """Elapsed time of the last recorded work cycle (ms)."""
        return self._base._run_info.elapsed_ms

    @property
    def max_elapsed_ms(self) -> int:
        """Maximum observed work cycle elapsed time (ms)."""
        return self._base._run_info.max_elapsed_ms

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def set_debug_level(self, level: int) -> None:
        """Set debug verbosity level."""
        self.debug_level = level
