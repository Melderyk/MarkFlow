import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from markflow import app as app_module
from tests.samples import create_samples


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app_module.configure_application(app)
    return app


def wait_for(app, condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition():
        app.processEvents()
        time.sleep(.015)
        assert time.monotonic() < deadline, "UI task timed out"
    app.processEvents()


def test_batch_preview_edit_and_save(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    sources = create_samples(tmp_path / "samples")
    window = app_module.MainWindow()
    window.output_edit.setText(str(tmp_path / "output"))
    window.add_files(list(sources) + [sources[0]])
    assert len(window.entries) == 3
    assert "Word" in window.original.description.text()
    window.start_batch()
    wait_for(qt_app, lambda: not window.running)
    assert all(entry.result for entry in window.entries)
    window.queue.setCurrentRow(0)
    window.editor.setPlainText(window.editor.toPlainText() + "\n\n# UI EDIT")
    assert window.entries[0].dirty and window.save_btn.isEnabled()
    window.tabs.setCurrentIndex(1)
    qt_app.processEvents()
    assert "UI EDIT" in window.rendered.toPlainText()
    window.save_current()
    assert not window.entries[0].dirty
    assert "UI EDIT" in window.entries[0].result.output.read_text(encoding="utf-8")
    window.queue.setCurrentRow(1)
    assert window.original.page_count == 2
    window.original.page.setValue(2)
    assert window.original.page.value() == 2
    window.remove_selected()
    assert len(window.entries) == 2
    window.clear_queue()
    assert not window.entries and window.current_index == -1
    window.close()


def test_batch_continues_after_invalid_pdf(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    bad = tmp_path / "bad.pdf"
    bad.write_text("broken", encoding="utf-8")
    source = create_samples(tmp_path / "samples")[0]
    window = app_module.MainWindow()
    window.output_edit.setText(str(tmp_path / "out"))
    window.add_files([bad, source])
    window.start_batch()
    wait_for(qt_app, lambda: not window.running)
    assert window.entries[0].error and window.entries[1].result
    assert window.start_btn.isEnabled()
    window.close()
