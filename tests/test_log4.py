"""Tests for src/gpylib/log4.py"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from log4 import Log4, DEBUG, INFO, WARN, ERROR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_logs(root: Path) -> str:
    parts: list[str] = []
    for p in sorted(root.rglob("*.log")):
        parts.append(p.read_text(encoding="utf-8"))
    return "".join(parts)


def _make(tmp: Path, name: str = "t", level: int = DEBUG, stdout: bool = False) -> Log4:
    return Log4(str(tmp), name, level=level, stdout=stdout)


# ---------------------------------------------------------------------------
# File / directory creation
# ---------------------------------------------------------------------------

class TestFileCreation:
    def test_creates_dated_directory(self, tmp_path):
        from datetime import datetime
        lg = _make(tmp_path)
        lg.close()
        date_str = datetime.now().strftime("%Y%m%d")
        assert (tmp_path / date_str).is_dir()

    def test_creates_log_file(self, tmp_path):
        from datetime import datetime
        lg = _make(tmp_path, name="app")
        lg.close()
        date_str = datetime.now().strftime("%Y%m%d")
        assert (tmp_path / date_str / f"app-{date_str}.log").exists()


# ---------------------------------------------------------------------------
# Level filtering
# ---------------------------------------------------------------------------

class TestLevelFiltering:
    def test_debug_visible_at_debug(self, tmp_path):
        lg = _make(tmp_path, level=DEBUG)
        lg.debug("dbg-msg")
        lg.close()
        assert "dbg-msg" in _read_logs(tmp_path)

    def test_debug_hidden_at_info(self, tmp_path):
        lg = _make(tmp_path, level=INFO)
        lg.debug("hidden")
        lg.close()
        assert "hidden" not in _read_logs(tmp_path)

    def test_info_visible_at_info(self, tmp_path):
        lg = _make(tmp_path, level=INFO)
        lg.info("info-msg")
        lg.close()
        assert "info-msg" in _read_logs(tmp_path)

    def test_warn_hidden_below_warn(self, tmp_path):
        lg = _make(tmp_path, level=ERROR)
        lg.warn("hidden-warn")
        lg.close()
        assert "hidden-warn" not in _read_logs(tmp_path)

    def test_always_bypasses_level(self, tmp_path):
        lg = _make(tmp_path, level=ERROR)
        lg.always("unconditional")
        lg.close()
        assert "unconditional" in _read_logs(tmp_path)

    def test_set_get_level(self, tmp_path):
        lg = _make(tmp_path, level=DEBUG)
        assert lg.get_level() == DEBUG
        lg.set_level(WARN)
        assert lg.get_level() == WARN
        lg.close()


# ---------------------------------------------------------------------------
# Format string: {} style
# ---------------------------------------------------------------------------

class TestFormatBrace:
    def test_positional(self, tmp_path):
        lg = _make(tmp_path)
        lg.info("a={} b={}", 10, "hi")
        lg.close()
        assert "a=10 b=hi" in _read_logs(tmp_path)

    def test_keyword(self, tmp_path):
        lg = _make(tmp_path)
        lg.debug("val={val}", val=42)
        lg.close()
        assert "val=42" in _read_logs(tmp_path)

    def test_print_brace(self, tmp_path):
        lg = _make(tmp_path)
        lg.print(DEBUG, "Hello, World!{}, {}", 30, "hi")
        lg.close()
        assert "Hello, World!30, hi" in _read_logs(tmp_path)

    def test_no_args_plain_string(self, tmp_path):
        lg = _make(tmp_path)
        lg.info("plain message")
        lg.close()
        assert "plain message" in _read_logs(tmp_path)


# ---------------------------------------------------------------------------
# Format string: % style
# ---------------------------------------------------------------------------

class TestFormatPercent:
    def test_d_and_s(self, tmp_path):
        lg = _make(tmp_path)
        lg.info("name=%s age=%d", "Alice", 30)
        lg.close()
        assert "name=Alice age=30" in _read_logs(tmp_path)

    def test_float(self, tmp_path):
        lg = _make(tmp_path)
        lg.debug("pi=%.2f", 3.14159)
        lg.close()
        assert "pi=3.14" in _read_logs(tmp_path)

    def test_print_percent(self, tmp_path):
        lg = _make(tmp_path)
        lg.print(DEBUG, "Hello, World! %d, %s", 30, "hi")
        lg.close()
        assert "Hello, World! 30, hi" in _read_logs(tmp_path)

    def test_percent_literal_not_treated_as_format(self, tmp_path):
        lg = _make(tmp_path)
        lg.info("100%% done")   # no args → returned as-is
        lg.close()
        assert "100%% done" in _read_logs(tmp_path)


# ---------------------------------------------------------------------------
# Stamps in output
# ---------------------------------------------------------------------------

class TestStamps:
    def _content(self, tmp_path) -> str:
        lg = _make(tmp_path, level=DEBUG)
        lg.debug("x")
        lg.info("x")
        lg.warn("x")
        lg.error("x")
        lg.always("x")
        lg.print(DEBUG, "x")
        lg.debug_dump(DEBUG, "x")
        lg.close()
        return _read_logs(tmp_path)

    def test_stamps_present(self, tmp_path):
        c = self._content(tmp_path)
        assert "DEBUG  :" in c
        assert "INFO   :" in c
        assert "WARN   :" in c
        assert "ERROR  :" in c
        assert "ALWAYS :" in c
        assert "PRINT  :" in c
        assert "DUMP   :" in c

    def test_name_in_each_line(self, tmp_path):
        lg = Log4(str(tmp_path), "myapp", level=DEBUG)
        lg.info("msg")
        lg.close()
        assert "[myapp]" in _read_logs(tmp_path)


# ---------------------------------------------------------------------------
# Hex dump
# ---------------------------------------------------------------------------

class TestDump:
    def test_header_contains_stamp_and_length(self, tmp_path):
        lg = _make(tmp_path)
        lg.dump(DEBUG, "pkt", bytes(5))
        lg.close()
        c = _read_logs(tmp_path)
        assert "pkt" in c
        assert "[5]" in c

    def test_hex_values(self, tmp_path):
        lg = _make(tmp_path)
        lg.dump(DEBUG, "raw", bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        lg.close()
        c = _read_logs(tmp_path)
        assert "DE" in c
        assert "EF" in c

    def test_20_bytes_per_line(self, tmp_path):
        lg = _make(tmp_path)
        lg.dump(DEBUG, "s", bytes(range(40)))
        lg.close()
        tab_lines = [l for l in _read_logs(tmp_path).splitlines() if l.startswith("\t")]
        assert len(tab_lines) == 2

    def test_dump_filtered_by_level(self, tmp_path):
        lg = _make(tmp_path, level=WARN)
        lg.dump(DEBUG, "x", bytes([0xAA]))
        lg.close()
        assert "AA" not in _read_logs(tmp_path)


# ---------------------------------------------------------------------------
# stdout option
# ---------------------------------------------------------------------------

class TestStdoutOption:
    def test_stdout_flag(self, tmp_path, capsys):
        lg = Log4(str(tmp_path), "so", level=DEBUG, stdout=True)
        lg.info("to-stdout")
        lg.close()
        captured = capsys.readouterr()
        assert "to-stdout" in captured.out
        assert "to-stdout" not in captured.err

    def test_stderr_default(self, tmp_path, capsys):
        lg = Log4(str(tmp_path), "se", level=DEBUG, stdout=False)
        lg.info("to-stderr")
        lg.close()
        captured = capsys.readouterr()
        assert "to-stderr" in captured.err
        assert "to-stderr" not in captured.out


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_writes(self, tmp_path):
        lg = _make(tmp_path, level=DEBUG)
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(50):
                    lg.info("t={} i={}", tid, i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lg.close()
        assert errors == []
        lines = [l for l in _read_logs(tmp_path).splitlines() if l.strip()]
        assert len(lines) == 500

    def test_lines_intact(self, tmp_path):
        lg = _make(tmp_path, level=DEBUG)

        def worker(tid: int) -> None:
            for i in range(20):
                lg.debug("T{:02d}-{:03d}", tid, i)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lg.close()
        for line in _read_logs(tmp_path).splitlines():
            assert line.startswith(("DEBUG", "INFO", "WARN", "ERROR", "ALWAYS",
                                     "PRINT", "DUMP", "LOG"))


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
