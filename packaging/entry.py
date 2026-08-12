"""PyInstaller entry point: runs the real CLI inside the james package.

``james/__main__.py`` uses relative imports, so it cannot be executed as a
top-level script by the bootloader — this thin shim imports it properly.
"""

import sys

from james.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
