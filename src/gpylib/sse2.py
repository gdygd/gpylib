"""
sse2.py - SSE (Server-Sent Events) session and message management

Ported from Go sse.go / ssesession.go.

Features:
- EventData with SSE wire-format serialization
- Per-session message queues with key pool (1..max_sessions)
- Broadcast fan-out to all active sessions
- Queue overflow protection (auto-clear when full)
- Thread-safe
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Optional

CHBUF_SSE = 100  # default max sessions / queue buffer size


# ---------------------------------------------------------------------------
# EventData
# ---------------------------------------------------------------------------

# @dataclass
# class EventData:
#     """SSE event payload.

#     Fields map directly to the SSE wire format fields.

#     Example::

#         ev = EventData(event="update", data="hello\\nworld", id="42")
#         raw: bytes = ev.prepare_message()
#         # b"id: 42\\nevent: update\\ndata: hello\\ndata: world\\n\\n"
#     """

#     event: str = ""   # SSE 'event' field  (Msgtype in Go)
#     data: str = ""    # SSE 'data' field
#     id: str = ""      # SSE 'id'   field

#     def prepare_message(self) -> bytes:
#         """Serialize to SSE wire format bytes."""
#         parts: list[str] = []
#         if self.id:
#             parts.append(f"id: {self.id.replace(chr(10), '')}\n")
#         if self.event:
#             parts.append(f"event: {self.event.replace(chr(10), '')}\n")
#         if self.data:
#             for line in self.data.split("\n"):
#                 parts.append(f"data: {line}\n")
#         parts.append("\n")
#         return "".join(parts).encode()

#     def prepare_message_with_id(self, id_: str) -> bytes:
#         """Set *id_* then serialize to SSE wire format."""
#         self.id = id_
#         return self.prepare_message()

class EventData:
    """SSE event payload.
    Fields map directly to the SSE wire format fields.
    Example::
        ev = EventData(event="update", data="hello\\nworld", id="42")
        raw: bytes = ev.prepare_message()
        # b"id: 42\\nevent: update\\ndata: hello\\ndata: world\\n\\n"
    """

    def __init__(self, event: str = "", data: str = "", id: str = "") -> None:
        self.event = event  # SSE 'event' field  (Msgtype in Go)
        self.data = data    # SSE 'data' field
        self.id = id        # SSE 'id'   field

    def prepare_message(self) -> bytes:
        """Serialize to SSE wire format bytes."""
        parts: list[str] = []
        if self.id:
            parts.append(f"id: {self.id.replace(chr(10), '')}\n")
        if self.event:
            parts.append(f"event: {self.event.replace(chr(10), '')}\n")
        if self.data:
            for line in self.data.split("\n"):
                parts.append(f"data: {line}\n")
        parts.append("\n")
        return "".join(parts).encode()

    def prepare_message_with_id(self, id_: str) -> bytes:
        """Set *id_* then serialize to SSE wire format."""
        self.id = id_
        return self.prepare_message()
    
# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _drain(q: "queue.Queue[EventData]") -> None:
    """Empty a queue without blocking."""
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


# ---------------------------------------------------------------------------
# SseManager
# ---------------------------------------------------------------------------

class SseManager:
    """Thread-safe SSE session manager.

    Maintains a pool of integer session keys (1..max_sessions), a per-session
    message queue for each key, and a global broadcast queue.

    Typical usage (HTTP SSE handler)::

        mgr = SseManager()

        # client connects:
        key = mgr.get_session_key()     # acquire key (0 = pool exhausted)

        # server sends an event to every connected client:
        mgr.send(EventData(event="update", data="hello"))

        # HTTP handler streams to this client:
        while client_connected:
            msg = mgr.pop(key)           # non-blocking
            if msg:
                yield msg.prepare_message()

        # client disconnects:
        mgr.clear_session_key(key)

    Args:
        max_sessions: Maximum simultaneous sessions (default 100).
        buf_size:     Per-queue and broadcast-queue capacity (default 100).
    """

    def __init__(
        self,
        max_sessions: int = CHBUF_SSE,
        buf_size: int = CHBUF_SSE,
    ) -> None:
        self._buf_size = buf_size

        # Protects key pool + active list
        self._session_lock = threading.Lock()
        # Protects per-session queues
        self._queue_lock = threading.Lock()
        # Protects broadcast queue
        self._broadcast_lock = threading.Lock()

        self._key_pool: list[int] = list(range(1, max_sessions + 1))
        self._active_keys: list[int] = []

        # One queue per key, pre-allocated
        self._session_queues: dict[int, queue.Queue[EventData]] = {
            k: queue.Queue(maxsize=buf_size)
            for k in range(1, max_sessions + 1)
        }

        # Global broadcast queue (mirrors Go's ChEvent)
        self._broadcast_queue: queue.Queue[EventData] = queue.Queue(maxsize=buf_size)

    # ------------------------------------------------------------------
    # Session key management
    # ------------------------------------------------------------------

    def get_session_key(self) -> int:
        """Acquire a session key from the pool.

        Returns:
            A positive integer key, or ``0`` if the pool is exhausted.
        """
        with self._session_lock:
            if not self._key_pool:
                return 0
            key = self._key_pool.pop(0)
            self._active_keys.append(key)
            return key

    def clear_session_key(self, key: int) -> None:
        """Release *key* back to the pool and drain its queue."""
        with self._session_lock:
            if key in self._active_keys:
                self._active_keys.remove(key)
            self._key_pool.append(key)

        with self._queue_lock:
            q = self._session_queues.get(key)
            if q:
                _drain(q)

    def active_session_keys(self) -> list[int]:
        """Return a snapshot of currently active session keys."""
        with self._session_lock:
            return list(self._active_keys)

    def active_session_count(self) -> int:
        """Return the number of active sessions."""
        with self._session_lock:
            return len(self._active_keys)

    # ------------------------------------------------------------------
    # Per-session messaging
    # ------------------------------------------------------------------

    def pop(self, key: int) -> Optional[EventData]:
        """Non-blocking pop from session *key*'s queue.

        Returns:
            The next :class:`EventData`, or ``None`` if the queue is empty.
        """
        with self._queue_lock:
            q = self._session_queues.get(key)
            if q is None:
                return None
            try:
                return q.get_nowait()
            except queue.Empty:
                return None

    def send_to_session(self, key: int, data: EventData) -> None:
        """Send *data* to a specific session queue.

        If the queue is full it is auto-cleared before enqueuing.
        """
        with self._queue_lock:
            q = self._session_queues.get(key)
            if q is None:
                return
            if q.qsize() >= self._buf_size:
                _drain(q)
            try:
                q.put_nowait(data)
            except queue.Full:
                pass

    def session_queue_size(self, key: int) -> int:
        """Return the current number of pending messages for session *key*."""
        with self._queue_lock:
            q = self._session_queues.get(key)
            return q.qsize() if q else 0

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    def send(self, data: EventData) -> None:
        """Broadcast *data* to the global queue and all active session queues.

        Mirrors Go's ``SendSSE``: checks for overflow, then fan-outs.
        """
        # Update global broadcast queue
        with self._broadcast_lock:
            if self._broadcast_queue.qsize() >= self._buf_size:
                _drain(self._broadcast_queue)
            try:
                self._broadcast_queue.put_nowait(data)
            except queue.Full:
                pass

        # Fan-out to every active session
        for key in self.active_session_keys():
            self.send_to_session(key, data)

    def pop_broadcast(self) -> Optional[EventData]:
        """Non-blocking pop from the global broadcast queue."""
        with self._broadcast_lock:
            try:
                return self._broadcast_queue.get_nowait()
            except queue.Empty:
                return None

    def broadcast_queue_size(self) -> int:
        """Return the current global broadcast queue depth."""
        with self._broadcast_lock:
            return self._broadcast_queue.qsize()
