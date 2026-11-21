"""python3 -m pool 入口（相对导入在 -m 模式下正常工作）。"""

import sys

from .cli import main

sys.exit(main())
