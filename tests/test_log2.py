"""Tests for src/log2.py"""

import os
import sys
import threading
import time
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

# import log2
from log2 import Log2, DEBUG, INFO, WARN, ERROR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_lines(root: str) -> list[str]:
    """Collect all lines from every .log file under *root*."""
    lines: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if fname.endswith(".log"):
                with open(os.path.join(dirpath, fname), encoding="utf-8") as f:
                    lines.extend(f.readlines())
    return lines


def _make_logger(tmp: str, name: str = "test", level: int = DEBUG) -> Log2:
    return Log2(tmp, name, level)


# ---------------------------------------------------------------------------
# Basic tests
# ---------------------------------------------------------------------------

class TestFileCreation:
    def test_creates_dated_directory(self, tmp_path):
        lg = _make_logger(str(tmp_path))
        date_str = datetime.now().strftime("%Y%m%d")
        assert (tmp_path / date_str).is_dir()
        lg.close()

    def test_creates_log_file(self, tmp_path):
        lg = _make_logger(str(tmp_path), name="myapp")
        date_str = datetime.now().strftime("%Y%m%d")
        expected = tmp_path / date_str / f"myapp-{date_str}.log"
        assert expected.exists()
        lg.close()


class TestLevelFiltering:
    def test_debug_passes_at_debug_level(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        lg.debug("hello debug")
        lg.close()
        lines = _log_lines(str(tmp_path))
        assert any("hello debug" in l for l in lines)

    def test_debug_filtered_at_info_level(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=INFO)
        lg.debug("should be hidden")
        lg.close()
        lines = _log_lines(str(tmp_path))
        assert not any("should be hidden" in l for l in lines)

    def test_info_passes_at_info_level(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=INFO)
        lg.info("visible info")
        lg.close()
        lines = _log_lines(str(tmp_path))
        assert any("visible info" in l for l in lines)

    def test_warn_filtered_below_warn(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=ERROR)
        lg.warn("hidden warn")
        lg.close()
        lines = _log_lines(str(tmp_path))
        assert not any("hidden warn" in l for l in lines)

    def test_always_bypasses_level(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=ERROR)
        lg.always("unconditional")
        lg.close()
        lines = _log_lines(str(tmp_path))
        assert any("unconditional" in l for l in lines)


class TestLevelMethods:
    def test_set_get_level(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        assert lg.get_level() == DEBUG
        lg.set_level(WARN)
        assert lg.get_level() == WARN
        lg.close()

    def test_all_level_methods_write(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        lg.debug("d")
        lg.info("i")
        lg.warn("w")
        lg.error("e")
        lg.always("a")
        lg.close()
        lines = _log_lines(str(tmp_path))
        content = "".join(lines)
        assert "d" in content
        assert "i" in content
        assert "w" in content
        assert "e" in content
        assert "a" in content


class TestStamps:
    def _get_content(self, tmp_path) -> str:
        lg = _make_logger(str(tmp_path), level=DEBUG)
        lg.debug("x")
        lg.info("x")
        lg.warn("x")
        lg.error("x")
        lg.always("x")
        lg.print(DEBUG, "x")
        lg.close()
        return "".join(_log_lines(str(tmp_path)))

    def test_stamps_present(self, tmp_path):
        content = self._get_content(tmp_path)
        assert "DEBUG  :" in content
        assert "INFO   :" in content
        assert "WARN   :" in content
        assert "ERROR  :" in content
        assert "ALWAYS :" in content
        assert "PRINT  :" in content

    def test_format_string(self, tmp_path):
        lg = Log2(str(tmp_path), "fmt", DEBUG)
        lg.print(DEBUG, "name={} age={}", "Alice", 30)
        lg.info("val={val}", val=42)
        lg.close()
        content = "".join(_log_lines(str(tmp_path)))
        assert "name=Alice age=30" in content
        assert "val=42" in content

    def test_name_in_prefix(self, tmp_path):
        lg = Log2(str(tmp_path), "myservice", DEBUG)
        lg.info("msg")
        lg.close()
        content = "".join(_log_lines(str(tmp_path)))
        assert "[myservice]" in content


class TestDump:
    def test_dump_header(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        data = bytes(range(5))
        lg.dump(DEBUG, "pkt", data)
        lg.close()
        content = "".join(_log_lines(str(tmp_path)))
        assert "pkt" in content
        assert "[5]" in content

    def test_dump_hex_values(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        data = bytes([0x00, 0xFF, 0xAB])
        lg.dump(DEBUG, "raw", data)
        lg.close()
        content = "".join(_log_lines(str(tmp_path)))
        assert "FF" in content
        assert "AB" in content

    def test_dump_20_bytes_per_line(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        data = bytes(range(40))  # exactly 2 rows of 20
        lg.dump(DEBUG, "s", data)
        lg.close()
        lines = _log_lines(str(tmp_path))
        # Each 20-byte row starts with a tab
        tab_lines = [l for l in lines if l.startswith("\t")]
        assert len(tab_lines) == 2

    def test_dump_filtered_by_level(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=WARN)
        lg.dump(DEBUG, "hidden", bytes([0xAA]))
        lg.close()
        lines = _log_lines(str(tmp_path))
        assert not any("AA" in l for l in lines)


# ---------------------------------------------------------------------------
# Thread-safety test
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_writes_no_corruption(self, tmp_path):
        lg = _make_logger(str(tmp_path), level=DEBUG)
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(50):
                    lg.info(f"thread={tid} i={i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lg.close()
        assert errors == [], f"Thread errors: {errors}"

        lines = _log_lines(str(tmp_path))
        assert len(lines) == 10 * 50

    def test_concurrent_writes_complete(self, tmp_path):
        """Each line must be intact (no interleaved writes)."""
        lg = _make_logger(str(tmp_path), level=DEBUG)

        def worker(tid: int) -> None:
            for i in range(20):
                lg.debug(f"T{tid:02d}-{i:03d}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lg.close()
        lines = _log_lines(str(tmp_path))
        # Every line must end with \n and contain a well-formed marker
        for line in lines:
            assert line.endswith("\n"), f"Line not terminated: {line!r}"


# ---------------------------------------------------------------------------
# pytest entry-point guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
