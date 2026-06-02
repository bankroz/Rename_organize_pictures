# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec 文件 — 将 photo_renamer.py 打包为单个 exe
用法:  pyinstaller photo_renamer.spec
输出:  dist/photo_renamer.exe  +  dist/patterns.json
"""
import sys
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['photo_renamer.py'],
    pathex=[],
    binaries=[],
    datas=[],  # patterns.json 不打入 exe，保持外部可编辑
    hiddenimports=[
        'PIL',
        'PIL._exif',
        'piexif',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='photo_renamer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,       # 如需图标: icon='icon.ico'
)
