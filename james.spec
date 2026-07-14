# PyInstaller spec for building a standalone JAMES executable (no Python required).
# Usage:  pyinstaller james.spec
import os

block_cipher = None

a = Analysis(
    ["james/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("james/tools", "james/tools"),
        ("james/plugins", "james/plugins"),
    ],
    hiddenimports=[
        "james.llm.providers",
        "james.tools.browser_tools",
        "james.tools.memory_tools",
        "james.tools.scheduler_tools",
        "james.plugins",
        "playwright",
        "docx",
        "pptx",
        "reportlab",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="JAMES",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
