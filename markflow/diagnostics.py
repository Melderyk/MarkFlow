"""Local-only hang traces; never capture document text, tokens or network data."""
import faulthandler
from pathlib import Path
import threading
import time

from PySide6.QtCore import QTimer
from shiboken6 import isValid
from . import __version__


class UiWatchdog:
    def __init__(self, window, folder):
        self.stopped=threading.Event()
        self.last=time.monotonic()
        self.timer=QTimer(window)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.heartbeat)
        self.file=None
        try:
            path=Path(folder)/'diagnostics'/'ui-watchdog.log'
            path.parent.mkdir(exist_ok=True)
            if path.exists() and path.stat().st_size>1024*1024:
                path.replace(path.with_suffix('.previous.log'))
            self.file=path.open('a',encoding='utf-8',buffering=1)
            self.file.write(f'\nMarkFlow {__version__} UI watchdog started\n')
        except OSError:
            return
        self.timer.start()
        self.thread=threading.Thread(target=self.watch,daemon=True)
        self.thread.start()

    def heartbeat(self):
        self.last=time.monotonic()

    def watch(self):
        reported=False
        while not self.stopped.wait(1):
            gap=time.monotonic()-self.last
            if gap>6 and not reported:
                try:
                    self.file.write(f'UI heartbeat delayed {gap:.1f}s\n')
                    faulthandler.dump_traceback(file=self.file,all_threads=True)
                except (OSError,ValueError):
                    pass
                reported=True
            elif gap<2:
                reported=False

    def stop(self):
        if isValid(self.timer):
            self.timer.stop()
        self.stopped.set()
        if self.file:
            self.thread.join(timeout=2)
            if not self.thread.is_alive(): self.file.close()
