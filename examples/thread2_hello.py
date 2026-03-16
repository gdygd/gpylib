"""Example: Thread2 hello world"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from thread2 import Thread, RST_OK, RST_ABNORMAL


def worker(t: Thread, kill: threading.Event, arg1, arg2, arg3):
    count = 0
    while not kill.is_set():
        t.mark_time()

        count += 1
        print(f"[{t.id}] Hello, World! count={count}  arg1={arg1}")

        kill.wait(t.interval * 4 / 1000)  # sleep (ms → sec)
        t.update_run_info()               # sleep 포함한 전체 사이클 측정


thr = Thread()
thr.init(worker, 500, "hi")  # 500ms interval, arg1="hi"
thr.start()

# Monitor for 3 seconds
for _ in range(3):
    time.sleep(1)
    ok, state = thr.is_running()
    print(f"  is_running={ok}  state={state}  elapsed={thr.elapsed_ms}ms  max={thr.max_elapsed_ms}ms")

thr.kill()
thr.join()
print("done.")
