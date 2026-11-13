"""Ordered selection and background merging of existing Markdown documents."""
from pathlib import Path
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from .core import Cancelled
from .markdown_merge import merge_markdown_files


class MarkdownMergeWorker(QThread):
    progress = Signal(str)

    def __init__(self, paths, output_dir, profile, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.output_dir = output_dir
        self.profile = profile
        self.cancel_event = threading.Event()
        self.destination = None
        self.error = ''
        self.cancelled = False

    def run(self):
        try:
            self.destination = merge_markdown_files(
                self.paths, self.output_dir, self.profile,
                cancel=self.cancel_event, progress=self.progress.emit)
        except Cancelled:
            self.cancelled = True
        except Exception as exc:
            self.error = str(exc)


class MarkdownMergeDialog(QDialog):
    def __init__(self, output_dir, profile, parent=None):
        super().__init__(parent)
        self.output_dir = Path(output_dir)
        self.profile = profile
        self.worker = None
        self.destination = None
        self.merged_count = 0
        self.setWindowTitle('合并 MD 文件')
        self.resize(720, 510)
        self.setMinimumSize(560, 400)
        layout = QVBoxLayout(self)
        hint = QLabel('添加至少两个 MD 文档，按下方列表从上到下合并。\n可拖动排序，或选中文档后点击“上移 / 下移”。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.files = QListWidget()
        self.files.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.files.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.files.currentRowChanged.connect(self.refresh_enabled)
        self.files.model().rowsMoved.connect(self.refresh_enabled)
        layout.addWidget(self.files, 1)
        controls = QHBoxLayout()
        self.add_btn = QPushButton('添加 MD 文件…')
        self.add_btn.clicked.connect(self.pick_files)
        self.remove_btn = QPushButton('移除')
        self.remove_btn.clicked.connect(self.remove_selected)
        self.up_btn = QPushButton('上移')
        self.up_btn.clicked.connect(lambda: self.move_selected(-1))
        self.down_btn = QPushButton('下移')
        self.down_btn.clicked.connect(lambda: self.move_selected(1))
        for widget in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)
        output = QLabel(f'输出目录：{self.output_dir}')
        output.setWordWrap(True)
        output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(output)
        notice = QLabel('生成带目录的“合并文档.md”，重名时自动编号。\n本地图片和文件链接指向原目录，请保留原资源；合并文件需与资源一起移动。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.status = QLabel('尚未添加文档')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.clicked.connect(self.reject)
        self.start_btn = QPushButton('开始合并')
        self.start_btn.setObjectName('primary')
        self.start_btn.clicked.connect(self.start_merge)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.start_btn)
        layout.addLayout(actions)
        self.refresh_enabled()

    def paths(self):
        return [Path(self.files.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.files.count())]

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '选择要合并的 MD 文档', '',
                                               'Markdown 文档 (*.md *.markdown)')
        self.add_files(paths)

    def add_files(self, paths):
        if self.worker is not None:
            return
        existing = {str(path).casefold() for path in self.paths()}
        skipped = 0
        for value in paths:
            path = Path(value).resolve()
            if path.suffix.lower() not in {'.md', '.markdown'} or not path.is_file():
                skipped += 1
                continue
            key = str(path).casefold()
            if key in existing:
                continue
            existing.add(key)
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.files.addItem(item)
        if self.files.currentRow() < 0 and self.files.count():
            self.files.setCurrentRow(0)
        self.status.setText(f'已选择 {self.files.count()} 份文档' + (f' · 跳过 {skipped} 个无效文件' if skipped else ''))
        self.refresh_enabled()

    def remove_selected(self):
        if self.worker is None and self.files.currentRow() >= 0:
            self.files.takeItem(self.files.currentRow())
            self.status.setText(f'已选择 {self.files.count()} 份文档')
            self.refresh_enabled()

    def move_selected(self, offset):
        row = self.files.currentRow()
        target = row + offset
        if self.worker is None and 0 <= row < self.files.count() and 0 <= target < self.files.count():
            item = self.files.takeItem(row)
            self.files.insertItem(target, item)
            self.files.setCurrentRow(target)
            self.refresh_enabled()

    def refresh_enabled(self, *_):
        idle = self.worker is None
        row = self.files.currentRow()
        self.files.setEnabled(idle)
        self.add_btn.setEnabled(idle)
        self.remove_btn.setEnabled(idle and row >= 0)
        self.up_btn.setEnabled(idle and row > 0)
        self.down_btn.setEnabled(idle and 0 <= row < self.files.count() - 1)
        self.start_btn.setEnabled(idle and self.files.count() >= 2)

    def start_merge(self):
        if self.worker is not None or self.files.count() < 2:
            return
        self.merged_count = self.files.count()
        self.worker = MarkdownMergeWorker(self.paths(), self.output_dir, self.profile, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self.merge_finished)
        self.cancel_btn.setText('停止合并')
        self.refresh_enabled()
        self.worker.start()

    def merge_finished(self):
        worker = self.worker
        self.worker = None
        self.cancel_btn.setText('取消')
        self.refresh_enabled()
        worker.deleteLater()
        if worker.destination is not None:
            self.destination = worker.destination
            self.accept()
        elif worker.cancelled:
            self.status.setText('已停止合并，可调整列表后重试。')
        else:
            self.status.setText('合并失败：' + worker.error)

    def reject(self):
        if self.worker is not None:
            self.worker.cancel_event.set()
            self.status.setText('正在停止合并，请稍候。')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
