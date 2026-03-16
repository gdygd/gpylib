"""Example: sse2 hello world

Simulates an SSE server with:
- 3 client sessions subscribing to events
- A producer thread broadcasting events every 0.3s
- Each session streaming received messages in SSE wire format
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from sse2 import EventData, SseManager

mgr = SseManager()


def client_session(session_id: str, duration: float) -> None:
    """Simulate an HTTP SSE client session."""
    key = mgr.get_session_key()
    if key == 0:
        print(f"[{session_id}] no session key available")
        return

    print(f"[{session_id}] connected  key={key}")
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        msg = mgr.pop(key)
        if msg:
            wire = msg.prepare_message().decode()
            for line in wire.strip().splitlines():
                print(f"[{session_id}] {line}")
        else:
            time.sleep(0.05)

    mgr.clear_session_key(key)
    print(f"[{session_id}] disconnected  key={key}")


def producer(count: int, interval: float) -> None:
    """Broadcast events to all active sessions."""
    for i in range(count):
        ev = EventData(event="update", data=f"Hello, World! seq={i}", id=str(i))
        mgr.send(ev)
        print(f"[producer] sent seq={i}  active_sessions={mgr.active_session_count()}")
        time.sleep(interval)


# Start 3 client sessions with slightly different durations
clients = [
    threading.Thread(target=client_session, args=(f"client-{i}", 1.5), daemon=True)
    for i in range(1, 4)
]
for c in clients:
    c.start()

time.sleep(0.1)  # let sessions register

# Producer broadcasts 5 events
prod = threading.Thread(target=producer, args=(5, 0.3))
prod.start()
prod.join()

for c in clients:
    c.join()

print("done.")
