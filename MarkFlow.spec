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
binaries = collect_dynamic_libs('onnxruntime')
a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
             datas=data, hiddenimports=['rapidocr_onnxruntime', 'onnxruntime.capi.onnxruntime_pybind11_state'],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'reportlab', 'tkinter', 'matplotlib', 'scipy', 'IPython', 'notebook', 'PyQt5', 'PyQt6'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MarkFlow', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'resources' / 'markflow.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MarkFlow')
