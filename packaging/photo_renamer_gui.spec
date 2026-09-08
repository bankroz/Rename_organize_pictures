import shutil
import sys
from pathlib import Path

project_root = Path(SPECPATH).parent

ffprobe = shutil.which('ffprobe')
if not ffprobe:
    raise RuntimeError('ffprobe is required to build the desktop distribution')
a = Analysis([str(project_root / 'photo_renamer_gui.py')], pathex=[str(project_root)],
             binaries=[(ffprobe, '.')], datas=[], hiddenimports=[],
             excludes=['textual', 'matplotlib', 'numpy', 'scipy', 'pytest'])
if sys.platform == 'win32':
    # Qt uses the Windows ICU API. PATH may contain an incompatible ICU from
    # Poppler/Conda (version-suffixed exports), which must not shadow System32.
    a.binaries = [item for item in a.binaries
                  if not item[0].lower().startswith(('icuuc.dll', 'icudt78.dll'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='photo_renamer_desktop', console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='photo_renamer_desktop', upx=False)
