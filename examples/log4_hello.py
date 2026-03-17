"""Example: log4 hello world"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src/gpylib"))

from log4 import Log4, DEBUG, INFO

log = Log4("/tmp/gpylib_logs", "hello4", level=DEBUG, stdout=True)

log.info("Hello, World!")

# str.format 스타일
log.print(DEBUG, "Hello, World!{}, {}", 30, "hi")

# %-format 스타일
log.print(DEBUG, "Hello, World! %d, %s", 30, "hi")

lst = [1,2,3,4,5,6,7,8,9,10, 1,2,3,4,5,6,7,8,9,10, 1,2,3,4,5,6,7,8,9,10, 1,2,3,4,5,6,7,8,9,10]
b = bytes(lst)
log.dump(DEBUG, "raw", b)


log.close()
