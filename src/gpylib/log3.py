"""
log2.py - Thread-safe rotating file logger

Features:
- Daily log file rotation (rootdir/YYYYMMDD/name-YYYYMMDD.log)
- Dual output: file + stderr
- Level-based filtering
- Hex dump support
- Thread-safe
"""

import os
import sys
import threading
from datetime import datetime
from typing import Optional

# Log levels (lower = more verbose)
DEBUG = 0
INFO = 1
WARN = 2
ERROR = 3
_ALWAYS_LEVEL = 99


class Log2:
    """Thread-safe daily-rotating logger.

    Args:
        root_dir: Root directory where log subdirectories are created.
        name:     Logger name, used in log prefix and filename.
        level:    Minimum log level to output. Defaults to DEBUG (0).

    Example::

        log = Log2("/var/log/myapp", "server", level=INFO)
        log.info("Server started")
        log.error("Something went wrong")
        log.close()
    """

    def __init__(self, root_dir: str, name: str, level:int = DEBUG) -> None:
        self._root_dir = root_dir
        self._name = name
        self._level = level
        self._fp: Optional[object] = None
        self._path: str = ""
        self._lock = threading.Lock()

        self._path = self._get_file_path()
        self._reopen_file(self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_level(self, level:int) -> None:
        """Set the minimum log level."""
        self._level = level
    
    def get_level(self) -> int:
        """Return the current log level."""
        return self._level
    
    def close(self) -> None:
        """Close the underlying log file."""
        with self._lock:
            if self._fp is not None:
                self._fp.close()
                self._fp = None
            
    def always(self, fmt: str, *args: object, **kwargs: object) -> None:
        """Log unconditionally (bypasses level filter)."""
        self._write(_ALWAYS_LEVEL, "ALWAYS : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)

    def info(self, fmt: str, *args: object, **kwargs: object) ->None:
        """Log at INFO level."""
        self._write(INFO, "INFO   : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)
    
    def debug(self, fmt: str, *args: object, **kwargs: object) -> None:
        """Log at DEBUG level."""
        # self._write(DEBUG, "DEBUG : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)    
        self._write(DEBUG, "DEBUG  : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)
    
    def warn(self, fmt: str, *args: object, **kwargs: object) -> None:
        """Log at WARN level."""
        self._write(WARN, "WARN   : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)
    
    def error(self, fmt: str, *args: object, **kwargs: object) -> None:
        """Log at ERROR level."""
        self._write(ERROR, "ERROR  : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)

    def print(self, level: int, fmt: str, *args: object, **kwargs: object) -> None:
        """Log at a custom level with PRINT stamp."""
        self._write(level, "PRINT  : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)

    def debug_dump(self, level: int, fmt: str, *args: object, **kwargs: object) -> None:
        """Log a dump-style message at a custom level."""
        self._write(level, "DUMP   : ", fmt.format(*args, **kwargs) if args or kwargs else fmt)

    def dump(self, level: int, stamp: str, data: bytes) -> None:
        """Log a hex dump of *data* at the given level.

        Output format (20 bytes per line)::

              stamp  [length]
            \t XX XX XX ...
            \t XX XX ...
        """

        length = len(data)
        header = f" {stamp} [{length}]"

        hex_lines: list[str] = []
        for i in range (0, length, 20):
            chunk = data[i : i + 20]
            hex_lines.append("\t" + " ".join(f"{b:02X}" for b in chunk))

        body = "\n".join(hex_lines)
        self.debug_dump(level, header + "\n" + body)
    
    def _get_file_path(self) -> str:
        date_str = datetime.now().strftime("%Y%m%d")
        dir_path = os.path.join(self._root_dir, date_str)
        os.makedirs(dir_path, exist_ok=True)
        return os.path.join(dir_path, f"{self._name}-{date_str}.log")
    
    def _reopen_file(self, path: str) -> None:
        """Open *path* for appending. Must be called with self._lock held."""
        if self._fp is not None:
            self._fp.close()
            self._fp = None
        try:
            self._fp = open(path, "a", encoding="utf-8")
        except OSError as ext:
            sys.stderr.write(f"Failed to open log file '{path}' : {exc}\n")
        
    def _write(self, level: int, stamp: str, msg: str) -> None:
        if self._level > level:
            return
        
        now = datetime.now()
        ts = now.strftime("%Y/%m/%d %H:%M:%S.") + f"{now.microsecond:06d}"
        line = f"{stamp}[{self._name}]{ts} {msg}\n"

        with self._lock:
            new_path = self._get_file_path()
            if new_path != self._path or not os.path.exists(new_path):
                self._path = new_path
                self._reopen_file(self._path)
            
            if self._fp is not None:
                self._fp.write(line)
                self._fp.flush()
            
        sys.stderr.write(line)

