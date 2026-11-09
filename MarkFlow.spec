# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH)
# Prevent unrelated tools on the development machine's PATH (e.g. Poppler)
# from supplying an incompatible ICU DLL in place of Windows' system ICU.
system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
os.environ['PATH'] = os.pathsep.join([str(Path(sys.executable).parent), str(system / 'System32'), str(system)])
data = collect_data_files('rapidocr_onnxruntime')
data += [(str(root / 'resources'), 'resources'), (str(root / '示例文件'), 'samples')]
# The original PDF/DOCX build still works without the optional extensions.
from importlib.util import find_spec
if find_spec('pypandoc') is not None:
    import pypandoc
    pandoc_dir = Path(pypandoc.get_pandoc_path()).parent
    data += [(str(pandoc_dir / 'pandoc.exe'), 'pandoc'),
             (str(pandoc_dir / 'COPYRIGHT.txt'), 'pandoc')]
binaries = collect_dynamic_libs('onnxruntime')
a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
             datas=data, hiddenimports=['rapidocr_onnxruntime', 'onnxruntime.capi.onnxruntime_pybind11_state'],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'reportlab', 'tkinter', 'matplotlib', 'scipy', 'IPython', 'notebook', 'PyQt5', 'PyQt6'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MarkFlow', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          version=str(root / 'resources' / 'version_info.txt'),
          icon=str(root / 'resources' / 'markflow.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MarkFlow')
