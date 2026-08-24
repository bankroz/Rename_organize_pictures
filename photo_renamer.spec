# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — 将 photo_renamer.py（含 TUI）打包为单个 exe
用法:  pyinstaller photo_renamer.spec
输出:  dist/photo_renamer.exe  +  dist/patterns.json

Textual 组件（含 CSS / WIDGET 静态资源）通过 collect_data_files 自动收集。
"""
import sys
import shutil
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 收集 Textual 和 rich 的静态资源（CSS / tcss / widget templates 等）
textual_datas   = collect_data_files('textual')
rich_datas      = collect_data_files('rich')
ffprobe_path    = shutil.which('ffprobe')
ffprobe_bins    = [(ffprobe_path, '.')] if ffprobe_path else []

a = Analysis(
    ['photo_renamer.py'],
    pathex=[],
    binaries=ffprobe_bins,
    datas=textual_datas + rich_datas,   # patterns.json 保持外部可编辑，不打包
    hiddenimports=[
        # 图片 EXIF 支持
        'PIL',
        'PIL._exif',
        'piexif',
        # Textual 运行时（部分 import 是动态的）
        'textual',
        'textual.app',
        'textual.widgets',
        'textual.widgets._select',
        'textual.widgets._checkbox',
        'textual.widgets._data_table',
        'textual.widgets._input',
        'textual.widgets._button',
        'textual.widgets._label',
        'textual.widgets._static',
        'textual.widgets._header',
        'textual.widgets._footer',
        'textual.containers',
        'textual.theme',
        'textual.css',
        'textual.color',
        'rich',
        'rich.markup',
        'rich.text',
        'rich.console',
        # TUI 入口
        'photo_renamer_tui',
    ] + collect_submodules('textual') + collect_submodules('rich'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pytest'],
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
    console=True,          # 保持控制台窗口（TUI 需要终端）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,             # 如需图标: icon='icon.ico'
)
