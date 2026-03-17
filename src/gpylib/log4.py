"""
log4.py - Thread-safe rotating file logger (logging 패키지 기반)

log2.py와 동일한 API, 내부 구현을 표준 logging 패키지로 교체.

Features:
- Daily log file rotation  (rootdir/YYYYMMDD/name-YYYYMMDD.log)
- 표준출력(stdout) + 파일 동시 출력
- Level 기반 필터링
- Hex dump 지원
- 포맷 스트링 2가지 스타일 지원
    - str.format 스타일 :  "name={}, age={}"  ,  name, age
    - %-format  스타일  :  "name=%s, age=%d"  ,  name, age
- Thread-safe
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Level constants  (log2와 동일)
# ---------------------------------------------------------------------------
DEBUG = 0
INFO = 1
WARN = 2
ERROR = 3
_ALWAYS = 99

# 내부 logging 레벨 매핑
_TO_LOGGING: dict[int, int] = {
    DEBUG:   logging.DEBUG,
    INFO:    logging.INFO,
    WARN:    logging.WARNING,
    ERROR:   logging.ERROR,
    _ALWAYS: logging.CRITICAL,
}

_STAMPS: dict[int, str] = {
    DEBUG:   "DEBUG  :",
    INFO:    "INFO   :",
    WARN:    "WARN   :",
    ERROR:   "ERROR  :",
    _ALWAYS: "ALWAYS :",
}

# %-format 감지 정규식  (%% 는 리터럴이므로 제외)
_RE_PERCENT = re.compile(r"%(?!%)[-+0 #]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]")


# ---------------------------------------------------------------------------
# Internal: format string helper
# ---------------------------------------------------------------------------

def _render(fmt: str, args: tuple, kwargs: dict) -> str:
    """fmt + args/kwargs → 완성된 문자열.

    %-format  감지되면  ``fmt % args`` 사용,
    그 외에는 ``fmt.format(*args, **kwargs)`` 사용.
    """
    if not args and not kwargs:
        return fmt
    if args and _RE_PERCENT.search(fmt):
        return fmt % args
    return fmt.format(*args, **kwargs)


def _to_logging_level(level: int) -> int:
    """사용자 레벨 → logging 레벨 변환."""
    if level >= _ALWAYS:
        return logging.CRITICAL
    if level >= ERROR:
        return logging.ERROR
    if level >= WARN:
        return logging.WARNING
    if level >= INFO:
        return logging.INFO
    return logging.DEBUG


def _get_stamp(level: int) -> str:
    return _STAMPS.get(level, "LOG    :")


# ---------------------------------------------------------------------------
# Internal: custom handlers / formatter
# ---------------------------------------------------------------------------

class _Log4Formatter(logging.Formatter):
    """microsecond 정밀도 + 'STAMP[name]timestamp message' 형식."""

    def format(self, record: logging.LogRecord) -> str:
        ct = datetime.fromtimestamp(record.created)
        ts = ct.strftime("%Y/%m/%d %H:%M:%S.") + f"{ct.microsecond:06d}"
        stamp: str = getattr(record, "stamp", "LOG    :")
        name: str = getattr(record, "log4_name", record.name)
        return f"{stamp}[{name}]{ts} {record.getMessage()}"


class _DailyFileHandler(logging.FileHandler):
    """자정에 새 날짜 서브디렉터리로 자동 교체되는 FileHandler."""

    def __init__(self, root_dir: str, log_name: str) -> None:
        self._root_dir = root_dir
        self._log_name = log_name
        self._current_date: str = ""
        path = self._make_path()
        super().__init__(path, mode="a", encoding="utf-8", delay=False)

    def _make_path(self) -> str:
        date_str = datetime.now().strftime("%Y%m%d")
        dir_path = os.path.join(self._root_dir, date_str)
        os.makedirs(dir_path, exist_ok=True)
        self._current_date = date_str
        return os.path.join(dir_path, f"{self._log_name}-{date_str}.log")

    def emit(self, record: logging.LogRecord) -> None:
        # emit() 은 Handler.handle() 내부의 acquire/release 안에서 호출됨 → thread-safe
        date_str = datetime.now().strftime("%Y%m%d")
        if date_str != self._current_date:
            self.close()
            new_path = self._make_path()
            self.baseFilename = os.path.abspath(new_path)
            self.stream = self._open()
        super().emit(record)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class Log4:
    """표준 logging 패키지 기반 Thread-safe 일별 로테이션 로거.

    log2.Log2 와 동일한 API.  포맷 스트링 2가지 스타일 추가 지원.

    Args:
        root_dir:    로그 서브디렉터리가 생성될 루트 경로.
        name:        로거 이름 (파일명·prefix에 사용).
        level:       최소 출력 레벨. 기본값 DEBUG(0).
        stdout:      True 이면 sys.stdout, False 이면 sys.stderr 로 출력. 기본 False.

    Example::

        log = Log4("/tmp/logs", "server", level=INFO)
        log.info("started")
        log.print(DEBUG, "name={}, age={}", "Alice", 30)
        log.print(DEBUG, "name=%s, age=%d", "Alice", 30)
        log.close()
    """

    def __init__(
        self,
        root_dir: str,
        name: str,
        level: int = DEBUG,
        stdout: bool = False,
    ) -> None:
        self._level = level
        self._name = name

        # 각 인스턴스마다 고유 logger (이름 충돌 방지)
        self._logger = logging.getLogger(f"log4.{name}.{id(self)}")
        self._logger.setLevel(logging.DEBUG)   # 필터링은 _level로 직접 처리
        self._logger.propagate = False

        fmt = _Log4Formatter()

        # 파일 핸들러
        self._file_handler: Optional[_DailyFileHandler] = None
        if root_dir:
            self._file_handler = _DailyFileHandler(root_dir, name)
            self._file_handler.setFormatter(fmt)
            self._logger.addHandler(self._file_handler)

        # 콘솔 핸들러 (stdout / stderr)
        import sys
        stream_handler = logging.StreamHandler(sys.stdout if stdout else sys.stderr)
        stream_handler.setFormatter(fmt)
        self._logger.addHandler(stream_handler)

    # ------------------------------------------------------------------
    # Level control
    # ------------------------------------------------------------------

    def set_level(self, level: int) -> None:
        """최소 출력 레벨 설정."""
        self._level = level

    def get_level(self) -> int:
        """현재 레벨 반환."""
        return self._level

    def close(self) -> None:
        """파일 핸들러를 닫고 핸들러를 제거한다."""
        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)

    # ------------------------------------------------------------------
    # Logging methods
    # ------------------------------------------------------------------

    def always(self, fmt: str, *args: object, **kwargs: object) -> None:
        """레벨 무관하게 무조건 출력."""
        self._log(_ALWAYS, "ALWAYS :", fmt, args, kwargs)

    def info(self, fmt: str, *args: object, **kwargs: object) -> None:
        self._log(INFO, "INFO   :", fmt, args, kwargs)

    def debug(self, fmt: str, *args: object, **kwargs: object) -> None:
        self._log(DEBUG, "DEBUG  :", fmt, args, kwargs)

    def warn(self, fmt: str, *args: object, **kwargs: object) -> None:
        self._log(WARN, "WARN   :", fmt, args, kwargs)

    def error(self, fmt: str, *args: object, **kwargs: object) -> None:
        self._log(ERROR, "ERROR  :", fmt, args, kwargs)

    def print(self, level: int, fmt: str, *args: object, **kwargs: object) -> None:
        """임의 레벨로 출력. PRINT 스탬프 사용."""
        self._log(level, "PRINT  :", fmt, args, kwargs)

    def debug_dump(self, level: int, fmt: str, *args: object, **kwargs: object) -> None:
        """DUMP 스탬프로 출력."""
        self._log(level, "DUMP   :", fmt, args, kwargs)

    def dump(self, level: int, stamp: str, data: bytes) -> None:
        """bytes를 16진수 덤프로 출력 (20바이트 단위).

        출력 형식::

              stamp  [length]
            \\t XX XX XX ...
        """
        length = len(data)
        header = f"  {stamp}  [{length}]"
        hex_lines = [
            "\t" + " ".join(f"{b:02X}" for b in data[i: i + 20])
            for i in range(0, length, 20)
        ]
        self.debug_dump(level, header + "\n" + "\n".join(hex_lines))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log(
        self,
        level: int,
        stamp: str,
        fmt: str,
        args: tuple,
        kwargs: dict,
    ) -> None:
        if self._level > level:
            return
        msg = _render(fmt, args, kwargs)
        log_level = _to_logging_level(level)
        self._logger.log(
            log_level,
            msg,
            extra={"stamp": stamp, "log4_name": self._name},
            stacklevel=3,
        )
