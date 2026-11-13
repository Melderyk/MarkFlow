from PySide6.QtWidgets import QDialog

from markflow import app as app_module
from markflow.merge_dialog import MarkdownMergeDialog
from tests.test_markdown_merge import document
from tests.test_ui import qt_app, wait_for


def test_merge_menu_available_without_converted_documents(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    window = app_module.MainWindow()
    assert window.merge_btn.isEnabled()
    assert window.merge_md_action.isEnabled()
    assert not window.merge_converted_action.isEnabled()
    window.running = True
    window.refresh_enabled()
    assert not window.merge_btn.isEnabled()
    window.running = False
    window.close()


def test_dialog_orders_and_merges_files_in_background(qt_app, tmp_path):
    first = document(tmp_path, 'a.md', '# FIRST')
    second = document(tmp_path, 'b.markdown', '# SECOND')
    bad = document(tmp_path, 'bad.txt', 'skip')
    dialog = MarkdownMergeDialog(tmp_path / 'out', 'GitHub / 通用')
    dialog.add_files([first, second, first, bad])
    assert dialog.paths() == [first, second]
    dialog.files.setCurrentRow(1)
    dialog.move_selected(-1)
    assert dialog.paths() == [second, first]
    dialog.start_merge()
    assert not dialog.start_btn.isEnabled() and not dialog.files.isEnabled()
    wait_for(qt_app, lambda: dialog.worker is None)
    assert dialog.result() == QDialog.DialogCode.Accepted
    text = dialog.destination.read_text(encoding='utf-8')
    assert text.index('# SECOND') < text.index('# FIRST')
    dialog.close()


def test_dialog_failure_can_be_corrected_and_retried(qt_app, tmp_path):
    first = document(tmp_path, 'a.md', '# FIRST')
    second = document(tmp_path, 'b.md', '```text\nunclosed')
    dialog = MarkdownMergeDialog(tmp_path / 'out', 'GitHub / 通用')
    dialog.add_files([first, second])
    dialog.start_merge()
    wait_for(qt_app, lambda: dialog.worker is None)
    assert '代码围栏未闭合' in dialog.status.text()
    assert dialog.destination is None and dialog.start_btn.isEnabled()
    second.write_text('# FIXED', encoding='utf-8')
    dialog.start_merge()
    wait_for(qt_app, lambda: dialog.worker is None)
    assert dialog.destination.is_file()
    dialog.close()


def test_main_window_opens_md_merge_option(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    first = document(tmp_path, 'a.md', '# FIRST')
    second = document(tmp_path, 'b.md', '# SECOND')
    messages = []
    def execute(dialog):
        dialog.add_files([first, second])
        dialog.start_merge()
        wait_for(qt_app, lambda: dialog.worker is None)
        return dialog.result()
    monkeypatch.setattr(MarkdownMergeDialog, 'exec', execute)
    monkeypatch.setattr(app_module.QMessageBox, 'information', lambda *args: messages.append(args[2]))
    window = app_module.MainWindow()
    window.output_edit.setText(str(tmp_path / 'out'))
    window.merge_md_action.trigger()
    assert (tmp_path / 'out' / '合并文档.md').is_file()
    assert '已合并 2 份 MD 文档' in window.status.text()
    assert messages and not window.entries
    window.close()
