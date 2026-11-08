from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QSize, QEvent
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QPixmap, QTextDocument, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QTreeWidget, QTreeWidgetItem, QDialog,
)

from .core import Cancelled, Converter, Options, Result, atomic_write, docx_preview_html, merge_results, pdf_preview
from . import __version__
from .image_host import HostConfig, ImageUploader, upload_images, protect_token, unprotect_token
from .markdown_tools import document_index, change_language, preview_sections, export_chapters, rewrite_images
from .ui_helpers import HostDialog, PreviewBrowser


ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
PROFILES = ["GitHub / 通用", "Obsidian", "Typora"]


@dataclass
class Entry:
    source: Path
    result: Result | None = None
    draft: str = ""
    dirty: bool = False
    status: str = "等待转换"
    error: str = ""


class BatchWorker(QThread):
    progress = Signal(int, str)
    converted = Signal(int, object)
    failed = Signal(int, str)
    stopped = Signal(int)

    def __init__(self, jobs, options, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.options = options
        self.cancel_event = threading.Event()

    def run(self):
        converter = Converter()
        for index, source in self.jobs:
            if self.cancel_event.is_set():
                break
            self.progress.emit(index, "转换中")
            try:
                result = converter.convert(source, self.options,
                                           lambda text, i=index: self.progress.emit(i, text),
                                           self.cancel_event)
                self.converted.emit(index, result)
            except Cancelled:
                self.stopped.emit(index)
                break
            except Exception as exc:
                self.failed.emit(index, str(exc))


class PublishWorker(QThread):
    progress=Signal(str)
    published=Signal(object)
    failed=Signal(str)
    def __init__(self,text,base,config,parent=None):
        super().__init__(parent)
        self.text,self.base,self.config=text,base,config
        self.cancel_event=threading.Event()

    def run(self):
        try:
            uploader=ImageUploader(self.config,self.base/'.markflow-upload-cache.json')
            self.published.emit(upload_images(self.text,self.base,uploader,self.progress.emit,self.cancel_event))
        except Cancelled:
            self.failed.emit('图片上传已停止，已有文档与图片保留。')
        except Exception:
            self.failed.emit('图片上传失败，请检查图床设置。已有文档保留。')


class OriginalPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.source = None
        self.page_count = 0
        self.pil = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.description = QLabel("原文预览")
        self.description.setObjectName("sectionTitle")
        layout.addWidget(self.description)
        self.controls = QWidget()
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.previous = QPushButton("‹ 上一页")
        self.next = QPushButton("下一页 ›")
        self.page = QSpinBox()
        self.page.setMinimum(1)
        self.total = QLabel("/ 0 页")
        controls.addWidget(self.previous)
        controls.addStretch()
        controls.addWidget(self.page)
        controls.addWidget(self.total)
        controls.addStretch()
        controls.addWidget(self.next)
        layout.addWidget(self.controls)
        self.stack = QStackedWidget()
        self.placeholder = QLabel("添加 PDF 或 Word 文件\n在这里查看原文内容")
        self.placeholder.setObjectName("placeholder")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.image)
        self.word = QTextBrowser()
        self.word.setAcceptDrops(False)
        self.word.setOpenExternalLinks(False)
        for widget in (self.placeholder, self.scroll, self.word):
            self.stack.addWidget(widget)
        layout.addWidget(self.stack, 1)
        self.controls.hide()
        self.page.valueChanged.connect(self.load_page)
        self.previous.clicked.connect(lambda: self.page.setValue(self.page.value() - 1))
        self.next.clicked.connect(lambda: self.page.setValue(self.page.value() + 1))

    def show_source(self, source: Path | None):
        if source == self.source:
            return
        self.source = source
        self.pil = None
        self.controls.hide()
        if source is None:
            self.stack.setCurrentWidget(self.placeholder)
            self.description.setText("原文预览")
            return
        try:
            if source.suffix.lower() == ".pdf":
                self.description.setText("PDF 原文 · 页面预览")
                self.page.blockSignals(True)
                self.page.setValue(1)
                self.pil, self.page_count = pdf_preview(source, 0)
                self.page.setMaximum(max(1, self.page_count))
                self.page.blockSignals(False)
                self.total.setText(f"/ {self.page_count} 页")
                self.controls.show()
                self.stack.setCurrentWidget(self.scroll)
                self.update_image()
                self.update_controls()
            else:
                self.description.setText("Word 原文 · 内容预览（不含分页）")
                self.word.setHtml(docx_preview_html(source))
                self.stack.setCurrentWidget(self.word)
        except Exception as exc:
            self.page.blockSignals(False)
            self.placeholder.setText(f"原文暂时无法预览\n{str(exc)[:180]}")
            self.stack.setCurrentWidget(self.placeholder)

    def load_page(self, value):
        if self.source is None or self.source.suffix.lower() != ".pdf":
            return
        try:
            self.pil, _ = pdf_preview(self.source, value - 1)
            self.update_image()
            self.update_controls()
        except Exception as exc:
            self.placeholder.setText(f"页面加载失败\n{str(exc)[:180]}")
            self.stack.setCurrentWidget(self.placeholder)

    def update_controls(self):
        self.previous.setEnabled(self.page.value() > 1)
        self.next.setEnabled(self.page.value() < self.page_count)

    def update_image(self):
        if self.pil is None:
            return
        pil = self.pil.convert("RGB")
        qimage = QImage(pil.tobytes(), pil.width, pil.height, pil.width * 3, QImage.Format.Format_RGB888).copy()
        width = max(260, self.scroll.viewport().width() - 28)
        pixmap = QPixmap.fromImage(qimage).scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(pixmap)
        self.image.setMinimumHeight(pixmap.height() + 16)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_image()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.entries: list[Entry] = []
        self.current_index = -1
        self.worker = None
        self.running = False
        self.completed_count = 0
        self.batch_total = 0
        self.host_config=HostConfig()
        self.remember_token=False
        self.index=document_index('')
        self.sections=[]
        self.preview_section=0
        self._render_key=None
        self.analysis_timer=QTimer(self)
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.setInterval(450)
        self.analysis_timer.timeout.connect(self.rebuild_navigation)
        self.setWindowTitle("墨页 MarkFlow · PDF / Word 转 Markdown")
        self.resize(1440, 920)
        self.setMinimumSize(1120, 720)
        self.setAcceptDrops(True)
        icon_path = RESOURCE_ROOT / "resources" / "markflow.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._build()
        self._load_config()
        # Child viewports and line edits otherwise consume Explorer URL drops.
        for widget in self.findChildren(QWidget):
            widget.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(310)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(24, 28, 24, 24)
        side.setSpacing(15)
        brand = QLabel("墨页 <span style='color:#a9bfde;font-size:18px'>MarkFlow</span>")
        brand.setObjectName("brand")
        side.addWidget(brand)
        tagline = QLabel("让文档流入你的知识库")
        tagline.setObjectName("sideCaption")
        side.addWidget(tagline)
        side.addSpacing(12)
        self.add_btn = QPushButton("＋  添加 PDF / Word")
        self.add_btn.setObjectName("addButton")
        self.add_btn.setMinimumHeight(46)
        self.add_btn.clicked.connect(self.pick_files)
        side.addWidget(self.add_btn)
        hint = QLabel("也可以将文件拖到窗口中\n支持 .pdf / .docx，扫描件自动 OCR")
        hint.setObjectName("sideCaption")
        hint.setWordWrap(True)
        side.addWidget(hint)
        queue_head = QHBoxLayout()
        self.queue_count = QLabel("文件队列  0")
        self.queue_count.setObjectName("sideHeading")
        queue_head.addWidget(self.queue_count)
        queue_head.addStretch()
        self.remove_btn = QPushButton("移除")
        self.remove_btn.setObjectName("sideSmall")
        self.remove_btn.clicked.connect(self.remove_selected)
        queue_head.addWidget(self.remove_btn)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("sideSmall")
        self.clear_btn.clicked.connect(self.clear_queue)
        queue_head.addWidget(self.clear_btn)
        side.addLayout(queue_head)
        self.queue = QListWidget()
        self.queue.setObjectName("queue")
        self.queue.setSpacing(5)
        self.queue.setWordWrap(True)
        self.queue.currentRowChanged.connect(self.select_entry)
        side.addWidget(self.queue, 1)
        side.addWidget(self._side_label("输出目录"))
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(ROOT / "导出"))
        self.output_edit.setObjectName("outputEdit")
        self.output_edit.setToolTip("文件保存在此目录；已有文件会自动编号")
        self.browse_btn = QPushButton("…")
        self.browse_btn.setFixedWidth(36)
        self.browse_btn.setObjectName("sideSmall")
        self.browse_btn.clicked.connect(self.pick_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.browse_btn)
        side.addLayout(output_row)
        side.addWidget(self._side_label("Markdown 格式"))
        self.profile = QComboBox()
        self.profile.setObjectName("profile")
        self.profile.addItems(PROFILES)
        self.profile.setToolTip("三种模式均使用相对图片链接；合并目录按阅读器适配")
        side.addWidget(self.profile)
        image_row=QHBoxLayout()
        self.image_mode=QComboBox()
        self.image_mode.setObjectName('profile')
        self.image_mode.addItems(['本地图片','图床链接＋本地备份'])
        self.host_btn=QPushButton('图床设置')
        self.host_btn.setObjectName('sideSmall')
        self.host_btn.clicked.connect(self.configure_host)
        image_row.addWidget(self.image_mode,1)
        image_row.addWidget(self.host_btn)
        side.addLayout(image_row)
        self.chapters=QCheckBox('长文档同时生成分章阅读版')
        self.chapters.setObjectName('sideCheck')
        self.chapters.setChecked(True)
        side.addWidget(self.chapters)
        self.ocr = QCheckBox("扫描页自动识别（中文 / 英文）")
        self.ocr.setObjectName("sideCheck")
        self.ocr.setChecked(True)
        side.addWidget(self.ocr)
        self.start_btn = QPushButton("开始转换")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumHeight(47)
        self.start_btn.clicked.connect(self.start_batch)
        side.addWidget(self.start_btn)
        foot = QLabel(f"●  文本转换本地完成 · 图床按所选模式上传\nv{__version__}  /  Windows 免安装版")
        foot.setObjectName("sideCaption")
        side.addWidget(foot)
        outer.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(28, 26, 28, 22)
        content.setSpacing(16)
        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("文档，轻松变成 Markdown。")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        subtitle = QLabel("导入 · 转换 · 对照编辑 · 带走你的内容")
        subtitle.setObjectName("muted")
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box, 1)
        badge = QLabel("  LOCAL FIRST  ")
        badge.setObjectName("badge")
        title_row.addWidget(badge)
        content.addLayout(title_row)
        self.file_title = QLabel("从一份文档开始")
        self.file_title.setObjectName("fileTitle")
        self.file_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        content.addWidget(self.file_title)
        self.card = QFrame()
        self.card.setObjectName("card")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        toolbar = QHBoxLayout()
        self.detail = QLabel("选择文件后，可在两侧对照查看和编辑")
        self.detail.setObjectName("muted")
        toolbar.addWidget(self.detail, 1)
        self.source_btn = QPushButton("打开原文件")
        self.source_btn.clicked.connect(self.open_source)
        self.save_btn = QPushButton("保存修改  Ctrl+S")
        self.save_btn.clicked.connect(self.save_current)
        self.publish_btn=QPushButton('上传 / 重试图片')
        self.publish_btn.setToolTip('使用当前图床配置上传当前文档的本地图片，并保存链接与当前编辑内容')
        self.publish_btn.clicked.connect(self.publish_current)
        toolbar.addWidget(self.source_btn)
        toolbar.addWidget(self.publish_btn)
        toolbar.addWidget(self.save_btn)
        card_layout.addLayout(toolbar)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.original = OriginalPreview()
        self.splitter.addWidget(self.original)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(10)
        label = QLabel("Markdown 结果")
        label.setObjectName("sectionTitle")
        right_layout.addWidget(label)
        code_row=QHBoxLayout()
        self.navigation_btn=QPushButton('大纲 / 检查')
        self.navigation_btn.setCheckable(True)
        self.navigation_btn.setChecked(True)
        self.navigation_btn.clicked.connect(lambda checked:self.navigation.setVisible(checked))
        code_row.addWidget(self.navigation_btn)
        self.code_select=QComboBox()
        self.code_select.setMinimumWidth(90)
        self.code_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.code_select.activated.connect(self.select_code)
        code_row.addWidget(self.code_select,1)
        self.language=QComboBox()
        self.language.setEditable(True)
        self.language.addItems(['text','c','cpp','bash','asm','powershell','python','javascript','json','sql','java','html','css'])
        self.language.setMaximumWidth(110)
        code_row.addWidget(self.language)
        self.apply_language_btn=QPushButton('应用语言')
        self.apply_language_btn.clicked.connect(self.apply_language)
        code_row.addWidget(self.apply_language_btn)
        right_layout.addLayout(code_row)
        work_split=QSplitter(Qt.Orientation.Horizontal)
        self.navigation=QTabWidget()
        self.navigation.tabBar().setUsesScrollButtons(False)
        self.outline=QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.setMinimumWidth(130)
        self.outline.itemClicked.connect(lambda item,column:self.navigate_line(item.data(0,Qt.ItemDataRole.UserRole)))
        self.issues=QListWidget()
        self.issues.itemClicked.connect(self.select_issue)
        self.navigation.addTab(self.outline,'大纲')
        self.navigation.addTab(self.issues,'检查')
        self.navigation.setTabToolTip(1,'质量提示：点击定位需要核对的内容')
        self.navigation.setStyleSheet('QTabBar::tab {padding:9px 8px; margin-right:3px;}')
        self.navigation.setMinimumWidth(145)
        work_split.addWidget(self.navigation)
        self.tabs = QTabWidget()
        self.editor = QPlainTextEdit()
        self.editor.setAcceptDrops(False)
        self.editor.setPlaceholderText("转换完成后，Markdown 会出现在这里。\n\n你可以直接编辑，再点击“保存修改”。")
        font = QFont("Cascadia Code", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(font)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.textChanged.connect(self.editor_changed)
        self.rendered = PreviewBrowser()
        self.rendered.setAcceptDrops(False)
        self.rendered.setOpenExternalLinks(False)
        self.tabs.addTab(self.editor, "Markdown 编辑")
        preview=QWidget()
        preview_layout=QVBoxLayout(preview)
        preview_layout.setContentsMargins(0,0,0,0)
        pager=QHBoxLayout()
        self.preview_previous=QPushButton('‹')
        self.preview_next=QPushButton('›')
        self.preview_position=QLabel('预览分段 0 / 0')
        self.preview_previous.clicked.connect(lambda:self.turn_preview(-1))
        self.preview_next.clicked.connect(lambda:self.turn_preview(1))
        pager.addWidget(self.preview_previous)
        pager.addWidget(self.preview_position,1)
        pager.addWidget(self.preview_next)
        preview_layout.addLayout(pager)
        preview_layout.addWidget(self.rendered,1)
        self.tabs.addTab(preview, "渲染预览")
        self.tabs.currentChanged.connect(self.refresh_render)
        work_split.addWidget(self.tabs)
        work_split.setSizes([165,500])
        right_layout.addWidget(work_split,1)
        self.splitter.addWidget(right)
        self.splitter.setSizes([500, 550])
        self.splitter.setChildrenCollapsible(False)
        card_layout.addWidget(self.splitter, 1)
        self.notice = QLabel("提示：扫描件识别后建议对照原文校对；Word 提供内容预览。")
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        card_layout.addWidget(self.notice)
        content.addWidget(self.card, 1)
        bottom = QHBoxLayout()
        self.status = QLabel("就绪 · 本地模式不上传图片")
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        self.merge_btn = QPushButton("合并已转换文档")
        self.merge_btn.clicked.connect(self.merge_documents)
        self.open_btn = QPushButton("打开输出目录 ↗")
        self.open_btn.clicked.connect(self.open_output)
        self.chapter_btn=QPushButton('分章阅读版')
        self.chapter_btn.clicked.connect(self.open_chapters)
        bottom.addWidget(self.chapter_btn)
        bottom.addWidget(self.merge_btn)
        bottom.addWidget(self.open_btn)
        content.addLayout(bottom)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        content.addWidget(self.progress)
        outer.addLayout(content, 1)
        action = QAction(self)
        action.setShortcut(QKeySequence.StandardKey.Save)
        action.triggered.connect(self.save_current)
        self.addAction(action)
        self.refresh_enabled()

    @staticmethod
    def _side_label(text):
        label = QLabel(text)
        label.setObjectName("sideHeading")
        return label

    def _load_config(self):
        try:
            data = json.loads((ROOT / "markflow-settings.json").read_text(encoding="utf-8"))
            self.output_edit.setText(data.get("output", str(ROOT / "导出")))
            self.profile.setCurrentText(data.get("profile", PROFILES[0]))
            self.ocr.setChecked(data.get("ocr", True))
            self.image_mode.setCurrentIndex(1 if data.get('image_mode')=='host' else 0)
            self.chapters.setChecked(data.get('chapters',True))
            self.host_config=HostConfig(**data.get('host',{}))
            self.remember_token=bool(data.get('encrypted_token'))
            if self.remember_token:
                try: self.host_config.token=unprotect_token(data['encrypted_token'])
                except (OSError,ValueError):
                    self.status.setText('已载入图床配置，请重新填写密钥。')
        except (OSError, ValueError, TypeError):
            pass

    def _save_config(self):
        encrypted=''
        credential_saved=True
        if self.remember_token:
            try: encrypted=protect_token(self.host_config.token)
            except OSError:
                credential_saved=False
        try:
            atomic_write(ROOT / "markflow-settings.json", json.dumps({
                "output": self.output_edit.text(), "profile": self.profile.currentText(), "ocr": self.ocr.isChecked(),
                'image_mode':'host' if self.image_mode.currentIndex() else 'local',
                'chapters':self.chapters.isChecked(),'host':self.host_config.public(),
                'encrypted_token':encrypted
            }, ensure_ascii=False, indent=2))
            if not credential_saved:
                self.status.setText('接口配置已保存；密钥加密存储不可用，仅在当前会话保留。')
            return credential_saved
        except OSError:
            self.status.setText('配置未能保存；当前会话仍可使用，请检查目录权限。')
            return False

    def configure_host(self):
        dialog=HostDialog(self.host_config,self.remember_token,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.host_config=dialog.value
            self.remember_token=dialog.remember.isChecked()
            if self._save_config():
                self.status.setText('图床配置已更新；选择图床模式后，转换时会上传图片。')

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加文档", "", "PDF / Word (*.pdf *.docx);;所有文件 (*)")
        self.add_files([Path(path) for path in paths])

    def add_files(self, paths):
        if self.running:
            return
        existing = {str(e.source).casefold() for e in self.entries}
        rejected = []
        for path in paths:
            path = Path(path).resolve()
            if not path.is_file() or path.suffix.lower() not in {".pdf", ".docx"}:
                rejected.append(path.name)
                continue
            if str(path).casefold() in existing:
                continue
            existing.add(str(path).casefold())
            self.entries.append(Entry(path))
            self.queue.addItem(QListWidgetItem())
            self.update_item(len(self.entries) - 1)
        self.queue_count.setText(f"文件队列  {len(self.entries)}")
        if self.current_index < 0 and self.entries:
            self.queue.setCurrentRow(0)
        self.status.setText(f"已添加 {len(self.entries)} 份文档" + (f" · 跳过 {len(rejected)} 个不支持的文件" if rejected else ""))
        self.refresh_enabled()

    def update_item(self, index):
        entry = self.entries[index]
        modified = " · 未保存" if entry.dirty else ""
        name = entry.source.name
        if len(name) > 25:
            name = name[:22] + "…" + entry.source.suffix
        self.queue.item(index).setText(f"{name}\n{entry.status}{modified}")
        self.queue.item(index).setSizeHint(QSize(240, 76))
        self.queue.item(index).setToolTip(str(entry.source) + ("\n" + entry.error if entry.error else ""))

    def select_entry(self, index):
        self.analysis_timer.stop()
        self._render_key=None
        self.preview_section=0
        self.current_index = index
        self.editor.blockSignals(True)
        if 0 <= index < len(self.entries):
            entry = self.entries[index]
            self.file_title.setText(entry.source.name)
            self.file_title.setToolTip(str(entry.source))
            self.original.show_source(entry.source)
            self.editor.setPlainText(entry.draft)
            self.detail.setText(entry.status)
            self.show_notice(entry)
        else:
            self.file_title.setText("从一份文档开始")
            self.original.show_source(None)
            self.editor.clear()
            self.detail.setText("选择文件后，可在两侧对照查看和编辑")
        self.editor.blockSignals(False)
        self.rebuild_navigation()
        self.refresh_enabled()
        self.refresh_render()

    def show_notice(self, entry):
        if entry.error:
            self.notice.setText(entry.error)
        elif entry.result:
            result = entry.result
            summary = f"已保存：{result.output.name}"
            if result.pages:
                summary += f" · {result.pages} 页 / OCR {result.ocr_pages} 页"
            if result.warnings:
                summary += "\n" + "；".join(result.warnings[:4])
            if result.remote_images:
                summary+=f' · {len(result.remote_images)} 张图床链接（本地副本保留）'
            if result.chapters_dir:
                summary+=' · 已生成分章阅读版'
            self.notice.setText(summary)
            self.notice.setToolTip(str(result.output) + "\n" + "\n".join(result.warnings))
        else:
            self.notice.setText("提示：扫描件识别后建议对照原文校对；Word 提供内容预览。")

    def editor_changed(self):
        if 0 <= self.current_index < len(self.entries):
            entry = self.entries[self.current_index]
            entry.draft = self.editor.toPlainText()
            entry.dirty = bool(entry.result and entry.draft != entry.result.markdown)
            self.update_item(self.current_index)
            self.refresh_enabled()
            self._render_key=None
            self.sections=[]
            self.analysis_timer.start()

    def rebuild_navigation(self):
        entry=self.entries[self.current_index] if 0<=self.current_index<len(self.entries) else None
        text=entry.draft if entry else ''
        base=entry.result.output.parent if entry and entry.result else None
        warnings=entry.result.warnings if entry and entry.result else ()
        collapsed={self.outline.topLevelItem(i).text(0):not self.outline.topLevelItem(i).isExpanded()
                   for i in range(self.outline.topLevelItemCount())}
        self.index=document_index(text,base,warnings)
        self.sections=preview_sections(text)
        self.outline.clear()
        parents=[]
        for heading in self.index.headings:
            while parents and parents[-1][0]>=heading.level: parents.pop()
            item=QTreeWidgetItem([heading.title])
            item.setData(0,Qt.ItemDataRole.UserRole,heading.line)
            if parents: parents[-1][1].addChild(item)
            else: self.outline.addTopLevelItem(item)
            item.setExpanded(not collapsed.get(heading.title,False))
            parents.append((heading.level,item))
        self.issues.clear()
        for issue in self.index.issues:
            item=QListWidgetItem(f'[{issue.kind}] '+issue.message)
            item.setToolTip(issue.message+f'\nMarkdown 第 {issue.line+1} 行'+(f' / 原文第 {issue.page} 页' if issue.page else ''))
            item.setData(Qt.ItemDataRole.UserRole,issue)
            self.issues.addItem(item)
        if not self.index.issues:
            item=QListWidgetItem('未发现可自动检测的问题。\nOCR 内容仍建议对照原文。')
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.issues.addItem(item)
        self.navigation.setTabText(1,f'检查 {len(self.index.issues)}')
        selected=self.code_select.currentIndex()
        self.code_select.clear()
        for i,block in enumerate(self.index.codes):
            sample=next((s.strip() for s in block.text.splitlines() if s.strip()),'空代码')
            self.code_select.addItem(f'{i+1}. {block.language or "未指定"} · {sample[:30]}',block.line)
        self.code_select.setCurrentIndex(min(max(0,selected),len(self.index.codes)-1))
        self.code_select.setToolTip('选择代码块，或将编辑光标放入代码后应用语言；只修改语言标签。')
        self.refresh_enabled()
        self.refresh_render()

    def navigate_line(self,line):
        if line is None: return
        block=self.editor.document().findBlockByNumber(line)
        if block.isValid():
            self.editor.setTextCursor(QTextCursor(block))
            self.editor.centerCursor()
        if self.tabs.currentIndex()==1:
            self.preview_section=next((i for i,(start,end,_) in enumerate(self.sections) if start<=line<end),0)
            self.refresh_render()
        else: self.editor.setFocus()

    def select_issue(self,item):
        issue=item.data(Qt.ItemDataRole.UserRole)
        if not issue: return
        self.tabs.setCurrentIndex(0)
        self.navigate_line(issue.line)
        if issue.page and self.original.source and self.original.source.suffix.lower()=='.pdf':
            self.original.page.setValue(min(issue.page,self.original.page_count))

    def select_code(self,index):
        if not 0<=index<len(self.index.codes): return
        block=self.index.codes[index]
        self.language.setCurrentText(block.language or 'text')
        self.tabs.setCurrentIndex(0)
        self.navigate_line(block.line)

    def apply_language(self):
        if self.running or not self.index.codes: return
        # Re-index immediately so a pending edit cannot leave stale line numbers.
        text=self.editor.toPlainText()
        index=document_index(text)
        cursor_line=self.editor.textCursor().blockNumber()
        block=next((b for b in index.codes if b.line<=cursor_line<=b.end),None)
        if block is None:
            selected=self.code_select.currentIndex()
            if not 0<=selected<len(index.codes): return
            block=index.codes[selected]
        try: changed=change_language(text,block,self.language.currentText().strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if changed!=text:
            cursor=QTextCursor(self.editor.document().findBlockByNumber(block.line))
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.insertText(changed.splitlines()[block.line])
            self.editor.setTextCursor(cursor)
            self.rebuild_navigation()
            self.status.setText('代码语言已修改；点击保存修改或按 Ctrl+S 写入文件。')

    def turn_preview(self,delta):
        self.preview_section=max(0,min(len(self.sections)-1,self.preview_section+delta))
        self.refresh_render()

    def refresh_render(self, *_):
        if self.tabs.currentIndex() != 1:
            return
        entry = self.entries[self.current_index] if 0 <= self.current_index < len(self.entries) else None
        if entry and entry.result:
            if not self.sections: self.sections=preview_sections(entry.draft)
            self.preview_section=max(0,min(self.preview_section,len(self.sections)-1))
            key=(self.current_index,entry.draft,self.preview_section)
            if key==self._render_key: return
            self.rendered.document().setBaseUrl(QUrl.fromLocalFile(str(entry.result.output.parent) + os.sep))
            text=self.sections[self.preview_section][2] if self.sections else ''
            if entry.result.remote_images:
                reverse={url:local for local,url in entry.result.remote_images.items()}
                text=rewrite_images(text,lambda m:f'![{m[1]}]({reverse[m[2]]})' if m[2] in reverse else m[0])
            self.rendered.setMarkdown(text)
            self._render_key=key
            self.preview_position.setText(f'预览分段 {self.preview_section+1} / {max(1,len(self.sections))}')
            self.preview_previous.setEnabled(self.preview_section>0)
            self.preview_next.setEnabled(self.preview_section<len(self.sections)-1)
        else:
            self.rendered.setPlainText("转换完成后可查看渲染结果。")
            self.preview_position.setText('预览分段 0 / 0')
            self.preview_previous.setEnabled(False)
            self.preview_next.setEnabled(False)

    def refresh_enabled(self):
        selected = 0 <= self.current_index < len(self.entries)
        entry = self.entries[self.current_index] if selected else None
        for widget in (self.add_btn, self.remove_btn, self.clear_btn, self.output_edit, self.browse_btn, self.profile, self.ocr,self.image_mode,self.host_btn,self.chapters):
            widget.setEnabled(not self.running)
        self.source_btn.setEnabled(selected)
        self.editor.setReadOnly(not bool(entry and entry.result) or self.running)
        self.save_btn.setEnabled(bool(entry and entry.result and entry.dirty) and not self.running)
        self.merge_btn.setEnabled(any(e.result for e in self.entries) and not self.running)
        self.start_btn.setEnabled(self.running or any(not e.result for e in self.entries))
        self.apply_language_btn.setEnabled(bool(entry and entry.result and self.index.codes) and not self.running)
        self.chapter_btn.setEnabled(bool(entry and entry.result and entry.result.chapters_dir) and not self.running)
        self.publish_btn.setEnabled(bool(entry and entry.result) and not self.running)

    def publish_current(self):
        if self.running or not 0<=self.current_index<len(self.entries): return
        entry=self.entries[self.current_index]
        if not entry.result: return
        try: self.host_config.validate()
        except ValueError as exc:
            QMessageBox.warning(self,'请配置图床',str(exc))
            return
        self._save_config()
        self.running=True
        self.publish_index=self.current_index
        self.start_btn.setText('停止上传')
        self.worker=PublishWorker(entry.draft,entry.result.output.parent,self.host_config,self)
        self.worker.progress.connect(self.status.setText)
        self.worker.published.connect(self.on_published)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self.on_publish_finished)
        self.refresh_enabled()
        self.worker.start()

    def on_published(self,value):
        entry=self.entries[self.publish_index]
        text,mapping,warnings=value
        try:
            atomic_write(entry.result.output,text.rstrip()+'\n')
            entry.result.remote_images.update(mapping)
            atomic_write(entry.result.output.with_suffix('.images.json'),json.dumps({
                'provider':self.host_config.provider,'images':entry.result.remote_images
            },ensure_ascii=False,indent=2))
            entry.draft=entry.result.markdown=text
            entry.dirty=False
            entry.result.warnings=[w for w in entry.result.warnings if not ('上传失败' in w or '图床配置' in w)] + warnings
            self.update_chapters(entry)
            self.update_item(self.publish_index)
            if self.current_index==self.publish_index: self.select_entry(self.publish_index)
            self.status.setText(f'图片处理完成：本次 {len(mapping)} 个链接，失败 {len(warnings)} 项；已保存。')
        except OSError:
            self.status.setText('图片已处理，但文档保存失败，请检查目录权限后重试。')

    def on_publish_finished(self):
        self.running=False
        self.start_btn.setText('转换完成' if all(e.result for e in self.entries) else '继续 / 重试')
        self.refresh_enabled()

    def pick_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def start_batch(self):
        if self.running:
            self.worker.cancel_event.set()
            self.start_btn.setEnabled(False)
            self.start_btn.setText("正在停止…")
            self.status.setText("正在停止 · 等待当前页面或图片请求结束")
            return
        jobs = [(i, e.source) for i, e in enumerate(self.entries) if not e.result]
        if not jobs:
            return
        output = self.output_edit.text().strip()
        if not output:
            QMessageBox.warning(self, "请选择目录", "请先设置输出目录。")
            return
        if self.image_mode.currentIndex():
            try: self.host_config.validate()
            except ValueError as exc:
                QMessageBox.warning(self,'请配置图床',str(exc))
                return
        self._save_config()
        self.completed_count = 0
        self.batch_total = len(jobs)
        self.running = True
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.start_btn.setText("停止转换")
        for index, _ in jobs:
            self.entries[index].error = ""
            self.entries[index].status = "等待转换"
            self.update_item(index)
        self.worker = BatchWorker(jobs, Options(Path(output), self.profile.currentText(), self.ocr.isChecked(),
            'host' if self.image_mode.currentIndex() else 'local',self.host_config,self.chapters.isChecked()), self)
        self.worker.progress.connect(self.on_progress)
        self.worker.converted.connect(self.on_converted)
        self.worker.failed.connect(self.on_failed)
        self.worker.stopped.connect(self.on_stopped)
        self.worker.finished.connect(self.on_finished)
        self.refresh_enabled()
        self.worker.start()

    def on_progress(self, index, text):
        self.entries[index].status = text
        self.update_item(index)
        self.status.setText(f"{self.completed_count}/{self.batch_total} 已处理 · {self.entries[index].source.name} · {text}")
        if self.current_index == index:
            self.detail.setText(text)

    def on_converted(self, index, result):
        entry = self.entries[index]
        entry.result, entry.draft = result, result.markdown
        entry.dirty = False
        entry.status = "转换完成" + (f" · OCR {result.ocr_pages} 页" if result.ocr_pages else "")
        self.completed_count += 1
        self.progress.setValue(self.completed_count)
        self.update_item(index)
        if index == self.current_index:
            self.select_entry(index)

    def on_failed(self, index, error):
        entry = self.entries[index]
        entry.error, entry.status = error, "转换失败 · 可重试"
        self.completed_count += 1
        self.progress.setValue(self.completed_count)
        self.update_item(index)
        if self.current_index == index:
            self.select_entry(index)

    def on_stopped(self, index):
        self.entries[index].status = "已停止 · 可继续"
        self.update_item(index)

    def on_finished(self):
        cancelled = self.worker.cancel_event.is_set()
        self.running = False
        self.start_btn.setText("继续 / 重试" if any(not e.result for e in self.entries) else "转换完成")
        successes = sum(e.result is not None for e in self.entries)
        errors = sum(bool(e.error) for e in self.entries)
        self.status.setText(f"{'已停止' if cancelled else '批量完成'} · 成功 {successes} 份 · 失败 {errors} 份")
        self.refresh_enabled()

    def save_current(self):
        if self.running or not 0 <= self.current_index < len(self.entries):
            return
        entry = self.entries[self.current_index]
        if not entry.result:
            return
        try:
            atomic_write(entry.result.output, entry.draft.rstrip() + "\n")
            self.update_chapters(entry)
            entry.result.markdown = entry.draft
            entry.dirty = False
            self.update_item(self.current_index)
            self.status.setText(f"已保存修改 · {entry.result.output.name}")
            self.refresh_enabled()
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def save_all_dirty(self):
        for entry in self.entries:
            if entry.result and entry.dirty:
                atomic_write(entry.result.output, entry.draft.rstrip() + "\n")
                self.update_chapters(entry)
                entry.result.markdown = entry.draft
                entry.dirty = False

    def merge_documents(self):
        try:
            results = [e.result for e in self.entries if e.result]
            # Merge contains every current draft, including unsaved edits.
            folder = Path(self.output_edit.text()).resolve()
            folder.mkdir(parents=True, exist_ok=True)
            destination = merge_results(results, folder, self.profile.currentText(),
                                        {str(e.result.output): e.draft for e in self.entries if e.result})
            self.status.setText(f"已合并 {len(results)} 份文档（包含当前编辑内容） · {destination.name}")
            QMessageBox.information(self, "合并完成", f"已生成：\n{destination}\n\n合并文件包含当前编辑内容；图片链接指向各文档的图片文件夹，请一并保留。")
        except Exception as exc:
            QMessageBox.warning(self, "合并失败", str(exc))

    def confirm_unsaved(self, entries):
        if not any(e.dirty for e in entries):
            return True
        answer = QMessageBox.question(self, "有尚未保存的修改", "是否先保存 Markdown 修改？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            try:
                for entry in entries:
                    if entry.result and entry.dirty:
                        atomic_write(entry.result.output, entry.draft.rstrip() + "\n")
                        self.update_chapters(entry)
                        entry.result.markdown = entry.draft
                        entry.dirty = False
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return False
        return True

    def remove_selected(self):
        index = self.current_index
        if self.running or index < 0 or not self.confirm_unsaved([self.entries[index]]):
            return
        self.queue.blockSignals(True)
        self.entries.pop(index)
        self.queue.takeItem(index)
        self.queue.blockSignals(False)
        new_index = min(index, len(self.entries) - 1)
        self.queue.setCurrentRow(new_index)
        self.select_entry(new_index)
        self.queue_count.setText(f"文件队列  {len(self.entries)}")

    def clear_queue(self):
        if self.running or not self.confirm_unsaved(self.entries):
            return
        self.entries.clear()
        self.queue.clear()
        self.select_entry(-1)
        self.queue_count.setText("文件队列  0")
        self.start_btn.setText("开始转换")
        self.progress.setValue(0)

    def open_source(self):
        if 0 <= self.current_index < len(self.entries):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.entries[self.current_index].source)))

    def open_output(self):
        try:
            path = Path(self.output_edit.text()).resolve()
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except OSError as exc:
            QMessageBox.warning(self, "无法打开目录", str(exc))

    def update_chapters(self,entry):
        if entry.result.chapters_dir:
            entry.result.chapters_dir=export_chapters(entry.draft,entry.result.output) or entry.result.chapters_dir

    def open_chapters(self):
        if 0<=self.current_index<len(self.entries):
            entry=self.entries[self.current_index]
            if entry.result and entry.result.chapters_dir:
                if entry.dirty:
                    self.status.setText('请先保存修改，再打开同步后的分章阅读版。')
                    return
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.result.chapters_dir/'目录.md')))

    def eventFilter(self,obj,event):
        if event.type() in {QEvent.Type.DragEnter,QEvent.Type.DragMove,QEvent.Type.Drop} and isinstance(obj,QWidget) and (obj is self or self.isAncestorOf(obj)):
            mime=event.mimeData()
            if mime.hasUrls():
                paths=[Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
                if not self.running and any(p.suffix.lower() in {'.pdf','.docx'} and p.is_file() for p in paths):
                    if event.type()==QEvent.Type.Drop: self.add_files(paths)
                    event.acceptProposedAction()
                else: event.ignore()
                return True
        return False

    def dragEnterEvent(self, event):
        if not self.running and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_files([Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self.running:
            self.worker.cancel_event.set()
            self.status.setText("正在停止转换，请等待当前页面结束后再关闭。")
            event.ignore()
            return
        if not self.confirm_unsaved(self.entries):
            event.ignore()
            return
        self._save_config()
        QApplication.instance().removeEventFilter(self)
        self.analysis_timer.stop()
        event.accept()


STYLE = """
* { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 12px; }
QMainWindow, QWidget { background: #f2f5fa; color: #24334b; }
QFrame#sidebar { background: #142942; }
QLabel#brand { background: transparent; color: #ffffff; font-size: 30px; font-weight: 700; }
QLabel#sideCaption { background: transparent; color: #9eb2cd; line-height: 1.7; font-size: 11px; }
QLabel#sideHeading { background: transparent; color: #c1d0e4; font-weight: 600; font-size: 12px; }
QLabel#pageTitle { font-size: 25px; font-weight: 700; color: #172d49; }
QLabel#fileTitle { font-size: 17px; font-weight: 600; color: #243b58; }
QLabel#muted { color: #75849a; font-size: 12px; }
QLabel#badge { background: #e0f1ec; color: #287b62; border-radius: 7px; padding: 8px; font-size: 10px; font-weight: 700; }
QFrame#card { background: white; border: 1px solid #e0e7f1; border-radius: 12px; }
QFrame#card QWidget, QFrame#card QLabel { background: white; }
QLabel#sectionTitle { font-size: 13px; font-weight: 600; padding-bottom: 5px; color: #3c5575; }
QLabel#notice { color: #7b8797; font-size: 11px; padding-top: 9px; }
QLabel#placeholder { background: #f8fafd; color: #8c9db3; font-size: 15px; border: 1px dashed #d6dfeb; border-radius: 8px; }
QPushButton { background: white; border: 1px solid #dce4ef; border-radius: 7px; padding: 8px 12px; font-weight: 500; }
QPushButton:hover { background: #edf4ff; border-color: #9dbbe7; }
QPushButton:disabled { background: #edf1f6; color: #a5afbd; border-color: #e6eaf0; }
QPushButton#primary { background: #447df5; color: white; border: 0; font-size: 14px; font-weight: 700; }
QPushButton#primary:hover { background: #558cff; }
QPushButton#primary:disabled { background: #314e75; color: #8099bc; }
QPushButton#addButton { background: #244469; color: white; border: 1px solid #46658c; font-size: 13px; }
QPushButton#addButton:hover { background: #305780; }
QPushButton#sideSmall { background: #203b5a; color: #c2d0e2; border: 0; padding: 5px 7px; }
QLineEdit#outputEdit, QComboBox#profile { background: #203b5a; color: #e7edf7; border: 1px solid #355171; border-radius: 6px; padding: 8px; }
QComboBox#profile QAbstractItemView { background: #203b5a; color: #e7edf7; selection-background-color: #376293; }
QCheckBox#sideCheck { background: transparent; color: #b8cbe3; font-size: 11px; spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QListWidget#queue { background: #142942; border: 0; outline: 0; color: #cbd8e9; }
QListWidget#queue::item { padding: 13px 10px; border-radius: 8px; background: #1b3452; }
QListWidget#queue::item:selected { background: #2c4d75; color: #ffffff; border: 1px solid #567aa7; }
QListWidget#queue::item:hover { background: #254364; }
QPlainTextEdit, QTextBrowser { background: #ffffff; color: #273a53; border: 1px solid #e1e7f0; border-radius: 7px; padding: 12px; selection-background-color: #dce9ff; }
QTabWidget::pane { border: 0; padding-top: 7px; }
QTabBar::tab { background: #f2f5fa; color: #7b8ba2; padding: 9px 17px; border-radius: 5px; margin-right: 6px; }
QTabBar::tab:selected { background: #e9f0ff; color: #3468cc; font-weight: 600; }
QScrollArea { border: 1px solid #e5ebf3; border-radius: 7px; background: #f5f7fb; }
QSplitter::handle { background: #edf1f7; width: 2px; margin: 0 5px; }
QProgressBar { border: 0; background: #e2e9f3; border-radius: 2px; }
QProgressBar::chunk { background: #447df5; border-radius: 2px; }
QSpinBox { border: 1px solid #dce4ef; border-radius: 5px; padding: 4px; min-width: 45px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #bdcada; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def configure_application(app):
    # Qt's offscreen platform has no system font database on Windows.
    # Explicitly load installed fonts for headless screenshots and self-checks.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "consola.ttf", "arial.ttf", "seguisym.ttf"):
            path = fonts / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
    app.setApplicationName("MarkFlow")
    app.setOrganizationName("MarkFlow")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)


def main():
    app = QApplication(sys.argv)
    configure_application(app)
    window = MainWindow()
    if len(sys.argv) > 1:
        window.add_files([Path(arg) for arg in sys.argv[1:]])
    window.show()
    return app.exec()
