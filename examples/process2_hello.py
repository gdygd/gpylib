"""Example: Process2 hello world

Demonstrates start() vs start_detach().
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from process2 import Process, RST_OK, RST_UNEXIST, RST_ABNORMAL

STATE_LABEL = {RST_OK: "OK", RST_UNEXIST: "UNEXIST", RST_ABNORMAL: "ABNORMAL"}

# --- start_detach: 부모와 독립된 새 세션으로 실행 ---
print("=== start_detach ===")
proc_d = Process("detached", [sys.executable, "-c", "import time; time.sleep(10)"])
ok, pid = proc_d.start_detach()
print(f"start_detach: ok={ok}  pid={pid}  sid={os.getsid(pid)}")
time.sleep(0.5)
print(f"  exist={proc_d.is_exist()}")
proc_d.kill()
proc_d.wait(timeout=2.0)
print(f"  after kill: exist={proc_d.is_exist()}")

print()

# --- start: 일반 자식 프로세스 ---
print("=== start (normal) ===")
proc = Process("sleep10", [sys.executable, "-c", "import time; time.sleep(10)"])
ok, pid = proc.start()
print(f"start: ok={ok}  pid={pid}")

# Monitor for 3 seconds
for i in range(3):
    time.sleep(1)
    is_ok, state = proc.is_running()
    print(
        f"  [{i+1}s] is_running={is_ok}  state={STATE_LABEL[state]}"
        f"  exist={proc.is_exist()}  pid={proc.get_pid()}"
    )

# Kill and verify
print("sending SIGTERM...")
proc.kill()
proc.wait(timeout=3.0)
time.sleep(0.1)

proc._check_timer = 0.0          # force past throttle
is_ok, state = proc.is_running()
print(f"after kill: is_running={is_ok}  state={STATE_LABEL[state]}  exist={proc.is_exist()}")
print("done.")
