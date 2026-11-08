"""Opt-in local stress check. Input path/content never enters the JSON report."""
import ctypes
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest


def memory_mb():
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in
            ['peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile']]
    value=Counters()
    value.cb=ctypes.sizeof(value)
    kernel=ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype=ctypes.c_void_p
    fn=ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    if fn(kernel.GetCurrentProcess(),ctypes.byref(value),value.cb):
        return round(value.working/1024**2,1)
    return None


def exercise(app,window,source,output):
    from .markdown_tools import document_index
    window.clear_queue()
    window.output_edit.setText(str(output/'stress-export'))
    window.add_files([source])
    pulses=[]
    last=[time.monotonic()]
    moves=[0]
    peak=[memory_mb() or 0]
    timer=QTimer()
    timer.setInterval(20)
    def pulse():
        now=time.monotonic()
        pulses.append(now-last[0])
        last[0]=now
        peak[0]=max(peak[0],memory_mb() or 0)
        if window.running:
            i=moves[0]
            window.resize(1280+(i%3)*40,820+(i%2)*30)
            window.move(30+(i%2)*8,30+(i%3)*8)
            moves[0]+=1
    timer.timeout.connect(pulse)
    timer.start()
    start=time.monotonic()
    window.start_batch()
    try:
        while window.running or window.original.is_loading():
            app.processEvents()
            time.sleep(.003)
            if time.monotonic()-start>180:
                window.worker.cancel_event.set()
                raise RuntimeError('Stress conversion exceeded 180 seconds')
        conversion_seconds=time.monotonic()-start
        assert window.entries[0].result, 'Stress conversion did not produce a document'
        index=window.index
        assert index.headings and index.codes
        latencies=[]
        toggles=0
        count=0
        for tab in (0,1,0,1):
            window.tabs.setCurrentIndex(tab)
            app.processEvents()
            for heading in index.headings[::3]:
                matches=window.outline.findItems(heading.title,
                    Qt.MatchFlag.MatchExactly|Qt.MatchFlag.MatchRecursive,0)
                item=next(x for x in matches if x.data(0,Qt.ItemDataRole.UserRole)==heading.line)
                window.outline.scrollToItem(item)
                app.processEvents()
                started=time.monotonic()
                QTest.mouseClick(window.outline.viewport(),Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,window.outline.visualItemRect(item).center())
                app.processEvents()
                latencies.append(time.monotonic()-started)
                count+=1
                if tab==1:
                    assert len(window.rendered.findChildren(QTextDocument))==1
                if count%10==0:
                    QTest.mouseClick(window.navigation_btn,Qt.MouseButton.LeftButton)
                    app.processEvents()
                    QTest.mouseClick(window.navigation_btn,Qt.MouseButton.LeftButton)
                    app.processEvents()
                    toggles+=2
        timer.stop()
        actual=document_index(window.entries[0].draft)
        assert [(h.level,h.title) for h in actual.headings]==[(h.level,h.title) for h in index.headings]
        assert [b.text for b in actual.codes]==[b.text for b in index.codes]
        assert max(latencies)<2 and max(pulses)<2, 'UI response exceeded stress-check limit'
        assert peak[0]<900, 'UI process memory exceeded stress-check limit'
        return {'pages':window.entries[0].result.pages,'headings':len(index.headings),'code_blocks':len(index.codes),
            'outline_mouse_clicks':count,'navigation_toggles':toggles,
            'resize_move_during_conversion':moves[0], 'conversion_seconds':round(conversion_seconds,2),
            'max_outline_action_seconds':round(max(latencies),3),'max_ui_heartbeat_gap_seconds':round(max(pulses),3),
            'peak_ui_working_set_mb':peak[0],'text_and_code_unchanged':True,
            'passed':True}
    finally:
        timer.stop()
