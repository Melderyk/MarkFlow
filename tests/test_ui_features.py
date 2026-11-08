import json
from pathlib import Path
import time

import pytest
from PySide6.QtCore import QMimeData,QPoint,QPointF,Qt,QUrl
from PySide6.QtGui import QDragEnterEvent,QDropEvent
from PySide6.QtWidgets import QApplication

from markflow import app as app_module
from markflow.core import Result
from tests.samples import create_samples
from tests.test_ui import qt_app,wait_for
from tests.test_export_features import server,config


@pytest.mark.parametrize('surface',['queue','editor','rendered','output'])
def test_explorer_file_drop_on_child_widgets(qt_app,tmp_path,monkeypatch,surface):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    source=create_samples(tmp_path/'samples')[0]
    window=app_module.MainWindow()
    window.show()
    qt_app.processEvents()
    target={'queue':window.queue.viewport(),'editor':window.editor.viewport(),
            'rendered':window.rendered.viewport(),'output':window.output_edit}[surface]
    mime=QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    enter=QDragEnterEvent(QPoint(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target,enter)
    assert enter.isAccepted()
    drop=QDropEvent(QPointF(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target,drop)
    assert drop.isAccepted() and len(window.entries)==1
    assert window.entries[0].source==source
    assert str(source) not in window.editor.toPlainText()
    wait_for(qt_app,lambda:not window.original.is_loading())
    window.close()


def attach(window,tmp_path,source,text):
    out=tmp_path/'sample.md'
    out.write_text(text,encoding='utf8')
    window.add_files([source])
    window.on_converted(0,Result(source,out,text,pages=2,warnings=['第 2 页 OCR 代码需核对']))
    wait_for(QApplication.instance(),lambda:not window.original.is_loading())


def test_outline_quality_navigation_language_undo_and_save(qt_app,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    source=create_samples(tmp_path/'samples')[1]
    window=app_module.MainWindow()
    text='# Chapter\n\n## Section\n\n```text\nint x;\n```\n\n![第 2 页 OCR 代码原图](missing.png)\n'
    attach(window,tmp_path,source,text)
    window.running=False
    window.refresh_enabled()
    assert window.outline.topLevelItemCount()==1
    root=window.outline.topLevelItem(0)
    assert root.childCount()==1
    root.setExpanded(False)
    assert not root.isExpanded()
    root.setExpanded(True)
    window.select_code(0)
    window.language.setCurrentText('c')
    window.apply_language()
    assert '```c\nint x;' in window.entries[0].draft
    assert window.entries[0].dirty
    window.editor.undo()
    assert window.editor.toPlainText()==text
    window.rebuild_navigation()
    window.select_code(0)
    window.language.setCurrentText('c')
    window.apply_language()
    window.save_current()
    assert '```c' in window.entries[0].result.output.read_text(encoding='utf8')
    issue=next(window.issues.item(i) for i in range(window.issues.count())
               if getattr(window.issues.item(i).data(Qt.ItemDataRole.UserRole),'page',0)==2)
    window.select_issue(issue)
    wait_for(qt_app,lambda:not window.original.is_loading())
    assert window.original.page.value()==2
    assert window.editor.textCursor().blockNumber()==8
    window.close()


def test_preview_does_not_relayout_unchanged_text_and_edits_refresh(qt_app,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    source=create_samples(tmp_path/'samples')[0]
    window=app_module.MainWindow()
    text=''.join(f'# Heading {i}\n\n'+('Some text.\n\n'*200) for i in range(20))
    attach(window,tmp_path,source,text)
    calls=[]
    actual=window.rendered.setMarkdown
    monkeypatch.setattr(window.rendered,'setMarkdown',lambda value:(calls.append(value),actual(value))[-1])
    window.tabs.setCurrentIndex(1)
    assert len(window.sections)>1 and len(calls)==1
    window.refresh_render()
    window.refresh_render()
    assert len(calls)==1 and len(calls[0])<16000
    window.turn_preview(1)
    assert len(calls)==2
    window.tabs.setCurrentIndex(0)
    window.editor.setPlainText('# Changed\n')
    window.tabs.setCurrentIndex(1)
    assert 'Changed' in window.rendered.toPlainText()
    window.save_current()
    window.close()


def test_host_settings_roundtrip_encrypted(qt_app,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    window=app_module.MainWindow()
    window.host_config.token='PRIVATE-TOKEN'
    window.remember_token=True
    window.image_mode.setCurrentIndex(1)
    window._save_config()
    settings=(tmp_path/'markflow-settings.json').read_text(encoding='utf8')
    assert 'PRIVATE-TOKEN' not in settings
    other=app_module.MainWindow()
    assert other.host_config.token=='PRIVATE-TOKEN' and other.image_mode.currentIndex()==1
    other.close()
    window.close()


def test_existing_document_upload_and_local_preview(server,qt_app,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    source=create_samples(tmp_path/'samples')[0]
    from PIL import Image
    Image.new('RGB',(20,20)).save(tmp_path/'image.png')
    window=app_module.MainWindow()
    attach(window,tmp_path,source,'# Title\n\n![图](image.png)\n')
    window.host_config=config(server)
    window.publish_current()
    wait_for(qt_app,lambda:not window.running)
    entry=window.entries[0]
    assert 'https://images.example.test/photo.png' in entry.draft
    assert entry.result.remote_images and not entry.dirty
    assert 'https://images.example.test/photo.png' in entry.result.output.read_text(encoding='utf8')
    window.tabs.setCurrentIndex(1)
    assert window.rendered.document().resource(2,QUrl('image.png')) is not None
    window.close()
