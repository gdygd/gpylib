"""Tests for src/gpylib/sse2.py"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from sse2 import EventData, SseManager, CHBUF_SSE


# ---------------------------------------------------------------------------
# EventData serialization
# ---------------------------------------------------------------------------

class TestEventDataPrepareMessage:
    def test_full_fields(self):
        ev = EventData(event="update", data="hello", id="42")
        msg = ev.prepare_message().decode()
        assert "id: 42\n" in msg
        assert "event: update\n" in msg
        assert "data: hello\n" in msg
        assert msg.endswith("\n\n")

    def test_empty_fields_omitted(self):
        ev = EventData(data="only-data")
        msg = ev.prepare_message().decode()
        assert "id:" not in msg
        assert "event:" not in msg
        assert "data: only-data\n" in msg

    def test_multiline_data_split(self):
        ev = EventData(data="line1\nline2\nline3")
        msg = ev.prepare_message().decode()
        assert "data: line1\n" in msg
        assert "data: line2\n" in msg
        assert "data: line3\n" in msg

    def test_newline_stripped_from_id(self):
        ev = EventData(id="ab\ncd")
        msg = ev.prepare_message().decode()
        assert "id: abcd\n" in msg

    def test_newline_stripped_from_event(self):
        ev = EventData(event="my\nevent")
        msg = ev.prepare_message().decode()
        assert "event: myevent\n" in msg

    def test_returns_bytes(self):
        ev = EventData(data="x")
        assert isinstance(ev.prepare_message(), bytes)

    def test_prepare_message_with_id(self):
        ev = EventData(event="ping", data="pong")
        msg = ev.prepare_message_with_id("99").decode()
        assert "id: 99\n" in msg
        assert ev.id == "99"

    def test_terminator_always_present(self):
        ev = EventData()
        assert ev.prepare_message() == b"\n"


# ---------------------------------------------------------------------------
# Session key management
# ---------------------------------------------------------------------------

class TestSessionKeyManagement:
    def test_get_session_key_returns_positive(self):
        mgr = SseManager(max_sessions=10)
        key = mgr.get_session_key()
        assert key >= 1
        mgr.clear_session_key(key)

    def test_get_session_key_unique(self):
        mgr = SseManager(max_sessions=10)
        keys = [mgr.get_session_key() for _ in range(5)]
        assert len(set(keys)) == 5
        for k in keys:
            mgr.clear_session_key(k)

    def test_pool_exhausted_returns_zero(self):
        mgr = SseManager(max_sessions=3)
        keys = [mgr.get_session_key() for _ in range(3)]
        assert mgr.get_session_key() == 0
        for k in keys:
            mgr.clear_session_key(k)

    def test_clear_returns_key_to_pool(self):
        mgr = SseManager(max_sessions=1)
        key = mgr.get_session_key()
        assert mgr.get_session_key() == 0   # pool empty
        mgr.clear_session_key(key)
        new_key = mgr.get_session_key()
        assert new_key == key               # same key reused
        mgr.clear_session_key(new_key)

    def test_active_session_keys_updated(self):
        mgr = SseManager(max_sessions=10)
        k1 = mgr.get_session_key()
        k2 = mgr.get_session_key()
        assert set(mgr.active_session_keys()) == {k1, k2}
        mgr.clear_session_key(k1)
        assert k1 not in mgr.active_session_keys()
        mgr.clear_session_key(k2)

    def test_active_session_count(self):
        mgr = SseManager(max_sessions=10)
        k1 = mgr.get_session_key()
        k2 = mgr.get_session_key()
        assert mgr.active_session_count() == 2
        mgr.clear_session_key(k1)
        assert mgr.active_session_count() == 1
        mgr.clear_session_key(k2)

    def test_clear_drains_session_queue(self):
        mgr = SseManager(max_sessions=10)
        key = mgr.get_session_key()
        mgr.send_to_session(key, EventData(data="x"))
        assert mgr.session_queue_size(key) == 1
        mgr.clear_session_key(key)
        # Key is back in pool; queue should be drained
        new_key = mgr.get_session_key()
        assert mgr.session_queue_size(new_key) == 0
        mgr.clear_session_key(new_key)


# ---------------------------------------------------------------------------
# Per-session messaging
# ---------------------------------------------------------------------------

class TestPerSessionMessaging:
    def test_send_and_pop(self):
        mgr = SseManager()
        key = mgr.get_session_key()
        mgr.send_to_session(key, EventData(event="ping", data="hello"))
        msg = mgr.pop(key)
        assert msg is not None
        assert msg.data == "hello"
        mgr.clear_session_key(key)

    def test_pop_empty_returns_none(self):
        mgr = SseManager()
        key = mgr.get_session_key()
        assert mgr.pop(key) is None
        mgr.clear_session_key(key)

    def test_pop_invalid_key_returns_none(self):
        mgr = SseManager(max_sessions=5)
        assert mgr.pop(999) is None

    def test_queue_overflow_auto_cleared(self):
        buf = 5
        mgr = SseManager(max_sessions=10, buf_size=buf)
        key = mgr.get_session_key()
        for i in range(buf + 2):
            mgr.send_to_session(key, EventData(data=str(i)))
        # Queue should have been cleared and last message enqueued
        assert mgr.session_queue_size(key) <= buf
        mgr.clear_session_key(key)

    def test_session_queue_size(self):
        mgr = SseManager()
        key = mgr.get_session_key()
        mgr.send_to_session(key, EventData(data="a"))
        mgr.send_to_session(key, EventData(data="b"))
        assert mgr.session_queue_size(key) == 2
        mgr.clear_session_key(key)

    def test_fifo_order(self):
        mgr = SseManager()
        key = mgr.get_session_key()
        for i in range(5):
            mgr.send_to_session(key, EventData(data=str(i)))
        results = [mgr.pop(key).data for _ in range(5)]
        assert results == ["0", "1", "2", "3", "4"]
        mgr.clear_session_key(key)


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

class TestBroadcast:
    def test_send_delivers_to_all_active_sessions(self):
        mgr = SseManager()
        k1 = mgr.get_session_key()
        k2 = mgr.get_session_key()
        k3 = mgr.get_session_key()
        mgr.send(EventData(event="tick", data="broadcast"))
        for k in (k1, k2, k3):
            msg = mgr.pop(k)
            assert msg is not None and msg.data == "broadcast"
            mgr.clear_session_key(k)

    def test_send_also_enqueues_broadcast_queue(self):
        mgr = SseManager()
        mgr.send(EventData(data="global"))
        msg = mgr.pop_broadcast()
        assert msg is not None and msg.data == "global"

    def test_send_does_not_deliver_to_cleared_session(self):
        mgr = SseManager()
        k1 = mgr.get_session_key()
        k2 = mgr.get_session_key()
        mgr.clear_session_key(k2)           # k2 disconnected before send
        mgr.send(EventData(data="late"))
        assert mgr.pop(k1) is not None
        # k2 queue was cleared; re-acquiring gives a clean queue
        k2b = mgr.get_session_key()
        assert mgr.pop(k2b) is None
        mgr.clear_session_key(k1)
        mgr.clear_session_key(k2b)

    def test_pop_broadcast_empty_returns_none(self):
        mgr = SseManager()
        assert mgr.pop_broadcast() is None

    def test_broadcast_queue_overflow_auto_cleared(self):
        buf = 5
        mgr = SseManager(max_sessions=10, buf_size=buf)
        # Send without any sessions active → only broadcast queue fills
        for i in range(buf + 3):
            mgr.send(EventData(data=str(i)))
        assert mgr.broadcast_queue_size() <= buf

    def test_broadcast_queue_size(self):
        mgr = SseManager()
        mgr.send(EventData(data="a"))
        mgr.send(EventData(data="b"))
        assert mgr.broadcast_queue_size() == 2
        mgr.pop_broadcast()
        assert mgr.broadcast_queue_size() == 1


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_get_clear_session_keys(self):
        mgr = SseManager(max_sessions=50)
        acquired: list[int] = []
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker():
            try:
                key = mgr.get_session_key()
                if key:
                    with lock:
                        acquired.append(key)
                    time.sleep(0.005)
                    mgr.clear_session_key(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All keys returned → pool should be full again
        assert mgr.active_session_count() == 0

    def test_concurrent_send_and_pop(self):
        mgr = SseManager(max_sessions=10)
        key = mgr.get_session_key()
        errors: list[Exception] = []

        def sender():
            try:
                for i in range(100):
                    mgr.send(EventData(data=str(i)))
            except Exception as e:
                errors.append(e)

        def popper():
            try:
                for _ in range(100):
                    mgr.pop(key)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=sender) for _ in range(3)]
            + [threading.Thread(target=popper) for _ in range(3)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        mgr.clear_session_key(key)

    def test_concurrent_session_get_and_broadcast(self):
        mgr = SseManager(max_sessions=20)
        errors: list[Exception] = []

        def session_worker():
            try:
                key = mgr.get_session_key()
                if not key:
                    return
                time.sleep(0.01)
                mgr.pop(key)
                mgr.clear_session_key(key)
            except Exception as e:
                errors.append(e)

        def broadcast_worker():
            try:
                for i in range(30):
                    mgr.send(EventData(event="t", data=str(i)))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=session_worker) for _ in range(20)]
            + [threading.Thread(target=broadcast_worker) for _ in range(3)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
