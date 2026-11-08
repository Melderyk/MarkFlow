"""Exercise event-loop responsiveness, stale previews and actual outline clicks."""
from pathlib import Path
import threading
import time

from PIL import Image
from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest

from markflow import app as app_module
from markflow.core import Options, Result
from markflow.ui_helpers import PreviewBrowser
from tests.test_ui import qt_app, wait_for
from tests.samples import create_samples


def test_slow_source_preview_keeps_ui_alive_and_ignores_old_request(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    entered=threading.Event()
    release=threading.Event()
    calls=[]
    def slow_preview(source, page):
        calls.append((source.name,page))
        if source.name=='first.pdf':
            entered.set()
            assert release.wait(5)
        return Image.new('RGB',(100,120),'red' if source.name=='first.pdf' else 'blue'), 4
    monkeypatch.setattr(app_module,'pdf_preview',slow_preview)
    widget=app_module.OriginalPreview()
    widget.show()
    ticks=[]
    timer=QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda:ticks.append(time.monotonic()))
    timer.start()
    try:
        widget.show_source(tmp_path/'first.pdf')
        wait_for(qt_app,entered.is_set)
        widget.show_source(tmp_path/'second.pdf')
        widget.show_page(3)
        wait_for(qt_app,lambda:len(ticks)>=5)
        assert widget._image is None
        release.set()
        wait_for(qt_app,lambda:not widget.is_loading())
        assert widget.source.name=='second.pdf' and widget.page.value()==3
        assert widget._image.pixelColor(0,0).blue()==255
        assert calls==[('first.pdf',0),('second.pdf',2)]
    finally:
        release.set()
        wait_for(qt_app,lambda:not widget.is_loading())
        timer.stop()
        widget.close()


def test_outline_mouse_navigation_in_preview_does_not_layout_hidden_editor(qt_app,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    source=create_samples(tmp_path/'samples')[0]
    window=app_module.MainWindow()
    text=''.join(f'# Chapter {i}\n\n'+('正文与 code text.\n\n'*50) for i in range(100))
    output=tmp_path/'result.md'
    window.add_files([source])
    window.on_converted(0,Result(source,output,text))
    window.show()
    wait_for(qt_app,lambda:not window.original.is_loading())
    window.tabs.setCurrentIndex(1)
    cursor=window.editor.textCursor().position()
    def must_not_layout():
        raise AssertionError('Hidden editor should not be centered from preview navigation')
    monkeypatch.setattr(window.editor,'centerCursor',must_not_layout)
    latencies=[]
    for i in range(0,100,3):
        item=window.outline.topLevelItem(i)
        window.outline.scrollToItem(item)
        qt_app.processEvents()
        start=time.monotonic()
        QTest.mouseClick(window.outline.viewport(),Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,window.outline.visualItemRect(item).center())
        qt_app.processEvents()
        latencies.append(time.monotonic()-start)
        line=item.data(0,Qt.ItemDataRole.UserRole)
        assert window.sections[window.preview_section][0]<=line<window.sections[window.preview_section][1]
    assert max(latencies)<2
    assert window.editor.textCursor().position()==cursor
    window.close()


def test_preview_resource_cache_is_released_when_section_changes(qt_app,tmp_path):
    browser=PreviewBrowser()
    browser.resize(400,400)
    browser.show()
    for i in range(30):
        name=f'image-{i}.png'
        Image.new('RGB',(1000,1000),'#336699').save(tmp_path/name)
        browser.document().setBaseUrl(QUrl.fromLocalFile(str(tmp_path)+'/' ))
        browser.setMarkdown(f'# Section {i}\n\n![sample]({name})\n')
        qt_app.processEvents()
        assert len(browser.images)==1
        assert len(browser.findChildren(QTextDocument))==1
    browser.close()


def test_conversion_runs_in_child_process_and_keeps_parent_converter_unused(qt_app,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    def forbidden(*args,**kwargs):
        raise AssertionError('Document conversion must run outside UI process')
    monkeypatch.setattr(app_module.Converter,'convert',forbidden)
    source=create_samples(tmp_path/'samples')[1]
    worker=app_module.BatchWorker([(0,source)],Options(tmp_path/'output'))
    results=[]
    errors=[]
    worker.converted.connect(lambda i,r:results.append(r))
    worker.failed.connect(lambda i,e:errors.append(e))
    ticks=[]
    timer=QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda:ticks.append(time.monotonic()))
    timer.start()
    worker.start()
    wait_for(qt_app,lambda:not worker.isRunning())
    timer.stop()
    assert not errors and len(results)==1
    assert 'MarkFlow PDF Sample' in results[0].markdown
    assert len(ticks)>=3
    assert max(b-a for a,b in zip(ticks,ticks[1:]))<1


def test_stop_reaches_spawned_converter_and_skips_remaining_jobs(qt_app,tmp_path):
    sources=create_samples(tmp_path/'samples')
    output=tmp_path/'output'
    worker=app_module.BatchWorker(list(enumerate(sources)),Options(output))
    stopped=[]
    converted=[]
    errors=[]
    started=[]
    def cancel_first(index,text):
        started.append(index)
        worker.cancel_event.set()
    worker.progress.connect(cancel_first)
    worker.stopped.connect(stopped.append)
    worker.converted.connect(lambda index,result:converted.append(index))
    worker.failed.connect(lambda index,error:errors.append(error))
    worker.start()
    wait_for(qt_app,lambda:not worker.isRunning())
    worker.wait()
    assert stopped==[0] and set(started)=={0}
    assert not converted and not errors
    assert not list(output.iterdir())
