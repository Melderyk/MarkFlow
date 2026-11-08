# Third-party notices

MarkFlow original code, resources/markflow.ico and generated examples under 示例文件/ are MIT-licensed. This does not relicense third-party software, fonts or OCR model assets. Existing copyright and license notices remain in THIRD_PARTY_LICENSES/ and in the portable package's _internal directory.

The distributed binaries use unmodified Qt / PySide6 / Shiboken6 via dynamic shared libraries under _internal/PySide6 and _internal/shiboken6. Compatible rebuilt libraries may replace them. Reverse engineering for debugging modifications to these LGPL components is not restricted by MarkFlow's license. See THIRD_PARTY_LICENSES/Qt-LGPL/ for LGPLv3, GPLv3 and corresponding upstream source references for version 6.12.0.

Upstream sources and notices:

- Qt / PySide6: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/
- RapidOCR and included ONNX models: https://github.com/RapidAI/RapidOCR — Apache-2.0 notices in THIRD_PARTY_LICENSES/rapidocr-onnxruntime/
- ONNX Runtime: https://github.com/microsoft/onnxruntime — notices in THIRD_PARTY_LICENSES/onnxruntime/
- PDFium and pypdfium2: see THIRD_PARTY_LICENSES/pypdfium2/ for component licenses.
- All other installed component versions and preserved notices: THIRD_PARTY_LICENSES/INDEX.md and components.json.

Generated sample documents contain example.com links and synthetic data. System fonts used to render sample documents are not copied as standalone font files. No personal source PDFs, converted course content, settings, image-host credentials or upload caches are included.
