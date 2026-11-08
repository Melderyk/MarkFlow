import json
import socket
import sys
import traceback
import time
from pathlib import Path

from .core import Converter, Options, merge_results, pdf_preview
from . import __version__


def run(destination):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    samples = root / ("samples" if getattr(sys, "frozen", False) else "示例文件")
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "checks": []}
    original_connect = socket.socket.connect
    def offline_only(*args, **kwargs):
        raise RuntimeError("Self-check blocks all network connections")
    socket.socket.connect = offline_only
    try:
        converter = Converter()
        results = []
        for path in sorted(samples.glob("*")):
            if path.suffix.lower() not in {".pdf", ".docx"}:
                continue
            result = converter.convert(path, Options(output))
            assert "MarkFlow" in result.markdown
            if path.name.startswith("03"):
                assert result.ocr_pages == 1 and "128.50" in result.markdown
                assert "```c\n" in result.markdown and "main" in result.markdown
                assert "OCR 代码原图（核对符号）" in result.markdown
                assert result.assets_dir and any(result.assets_dir.glob("*.png"))
            if path.name.startswith("02"):
                assert "# MarkFlow PDF Sample\n" in result.markdown
                assert "## Second Page\n" in result.markdown
                assert "```c\n#include <stdio.h>\nint main() {\n    printf" in result.markdown
            results.append(result)
            report["checks"].append({"file": path.name, "pages": result.pages, "ocr_pages": result.ocr_pages, "passed": True})
        assert len(results) == 3
        from .image_host import protect_token,unprotect_token
        assert unprotect_token(protect_token('MarkFlow selfcheck'))=='MarkFlow selfcheck'
        report['windows_secret_store']=True
        merge_results(results, output, "通用 Markdown")
        pdf = next(path for path in samples.glob("02*.pdf"))
        image, count = pdf_preview(pdf, 0)
        assert count == 2 and image.width > 400
        # Load and instantiate Qt widgets to also verify bundled Qt plugins.
        from PySide6.QtWidgets import QApplication
        from . import app as app_module
        app_module.ROOT = output
        app = QApplication.instance() or QApplication([])
        app_module.configure_application(app)
        window = app_module.MainWindow()
        window.output_edit.setText(str(output / "ui-exports"))
        window.show()
        app.processEvents()
        from PySide6.QtCore import QMimeData,QPoint,QPointF,Qt,QUrl
        from PySide6.QtGui import QDragEnterEvent,QDropEvent
        mime=QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(results[0].source))])
        target=window.editor.viewport()
        enter=QDragEnterEvent(QPoint(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target,enter)
        drop=QDropEvent(QPointF(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target,drop)
        assert enter.isAccepted() and drop.isAccepted() and len(window.entries)==1
        window.add_files([result.source for result in results[1:]])
        window.start_batch()
        deadline = time.monotonic() + 90
        while window.running:
            app.processEvents()
            time.sleep(.01)
            if time.monotonic() > deadline:
                window.worker.cancel_event.set()
                window.worker.wait()
                raise RuntimeError("GUI batch self-check timed out")
        assert all(entry.result for entry in window.entries)
        window.queue.setCurrentRow(1)
        assert window.outline.topLevelItemCount()==1
        assert window.outline.topLevelItem(0).childCount()==1
        assert window.index.codes
        window.select_code(0)
        window.language.setCurrentText('cpp')
        window.apply_language()
        assert '```cpp\n' in window.entries[1].draft
        window.save_current()
        assert not window.entries[1].dirty
        window.language.setCurrentText('c')
        window.apply_language()
        window.save_current()
        window.tabs.setCurrentIndex(1)
        assert 'MarkFlow' in window.rendered.toPlainText()
        window.outline.collapseAll()
        app.processEvents()
        window.grab().save(str(output / "selfcheck-ui.png"))
        window.close()
        app.processEvents()
        report['ui_features']={'child_viewport_drop':True,'outline_navigation':True,
            'manual_code_language_and_save':True,'bounded_preview':True}
        report["passed"] = True
        return_code = 0
    except Exception:
        report["passed"] = False
        report["error"] = traceback.format_exc()
        return_code = 1
    finally:
        socket.socket.connect = original_connect
    (output / "selfcheck.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return return_code
