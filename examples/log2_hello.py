"""Example: Log2 hello world"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from log3 import Log2, DEBUG

log = Log2("/tmp/gpylib_logs", "hello", level=DEBUG)
log.info("Hello, World!")
log.print(3, "Hello, World!{}, {}", 30, "hi")
log.close()
