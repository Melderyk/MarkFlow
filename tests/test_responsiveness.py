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


def test_format_export_runs_outside_ui_process(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    source = create_samples(tmp_path / 'samples')[1]
    output = tmp_path / 'sample.md'
    output.write_text('# Export check\n\n' + ('正文保持界面响应。\n\n' * 500), encoding='utf-8')
    window = app_module.MainWindow()
    window.add_files([source])
    window.on_converted(0, Result(source, output, output.read_text(encoding='utf-8')))
    window.output_format.setCurrentText('HTML (.html)')
    monkeypatch.setattr(app_module.QFileDialog, 'getSaveFileName',
        lambda *args: (str(tmp_path / 'export.html'), 'HTML'))
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    window.export_current()
    wait_for(qt_app, lambda: not window.running)
    timer.stop()
    assert (tmp_path / 'export.html').is_file()
    assert len(ticks) >= 3
    window.close()


def stalled_conversion_with_native_child(source, options, messages, cancel):
    """Simulate a native parser that ignores cooperative cancellation."""
    import os
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    time.sleep(.2)  # Let Windows publish the parent-child relation before cancellation.
    messages.put(('progress', f'native-child:{child.pid}'))
    time.sleep(60)


def test_forced_cancel_reaps_native_process_tree(qt_app, tmp_path, monkeypatch):
    import ctypes
    import os
    import pytest
    from markflow import conversion_worker
    if os.name != 'nt':
        pytest.skip('Windows process tree cleanup')
    monkeypatch.setattr(conversion_worker, 'convert_document', stalled_conversion_with_native_child)
    worker = app_module.BatchWorker([(0, tmp_path / 'ignored.pdf')], Options(tmp_path))
    native = []
    stopped = []
    errors = []
    def started(index, text):
        if text.startswith('native-child:'):
            native.append(int(text.split(':')[1]))
            worker.cancel_event.set()
    worker.progress.connect(started)
    worker.stopped.connect(stopped.append)
    worker.failed.connect(lambda i, error: errors.append(error))
    worker.start()
    # Spawn/pickle failures must fail promptly rather than waiting the default
    # minute for a progress message that the child can never send.
    wait_for(qt_app, lambda: bool(native) or not worker.isRunning())
    assert native, f'worker exited before native child started: {errors}'
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, native[0])  # SYNCHRONIZE
    assert handle
    try:
        started_at = time.monotonic()
        wait_for(qt_app, lambda: not worker.isRunning(), seconds=15)
        worker.wait()
        assert not errors and stopped == [0]
        assert time.monotonic() - started_at < 10
        assert kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


def test_finished_batch_workers_are_released(qt_app, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    import multiprocessing
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    source = tmp_path / 'source.csv'
    source.write_text('A,B\n1,2\n', encoding='utf-8')
    window = app_module.MainWindow()
    window.output_edit.setText(str(tmp_path / 'out'))
    try:
        for _ in range(6):
            window.add_files([source])
            window.start_batch()
            wait_for(qt_app, lambda: not window.running)
            assert window.entries[0].result
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert window.worker is None
            assert not window.findChildren(app_module.BatchWorker)
            assert not multiprocessing.active_children()
            window.clear_queue()
    finally:
        window.close()
