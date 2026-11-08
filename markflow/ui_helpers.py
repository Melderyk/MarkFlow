from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
import json
import tempfile

from PySide6.QtCore import QEvent, QSize, Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QImage, QImageReader, QTextDocument, QTextCursor
from PySide6.QtWidgets import (QDialog,QDialogButtonBox,QFormLayout,QVBoxLayout,
    QHBoxLayout,QLabel,QLineEdit,QComboBox,QPushButton,QCheckBox,QTextBrowser)

from .image_host import HostConfig, ImageUploader, PRESETS


class UploadProbe(QThread):
    done=Signal(bool,str)
    def __init__(self,config,parent=None):
        super().__init__(parent)
        self.config=config

    def run(self):
        from PIL import Image
        try:
            self.config.retries=0
            with tempfile.TemporaryDirectory(prefix='markflow-probe-') as folder:
                path=Path(folder)/'connection-test.png'
                Image.new('RGB',(16,16),'#447df5').save(path)
                url,_=ImageUploader(self.config).upload(path,use_cache=False)
            self.done.emit(True,'测试图片上传成功，接口可用。')
        except Exception as exc:
            # No response bodies, auth headers or raw connection errors in UI.
            from .image_host import UploadError
            message=str(exc) if isinstance(exc,(UploadError,ValueError)) else '测试失败，请检查接口和网络。'
            self.done.emit(False,message)


class HostDialog(QDialog):
    def __init__(self,config,remember,parent=None):
        super().__init__(parent)
        self.setWindowTitle('图床设置')
        self.resize(680,560)
        self.worker=None
        layout=QVBoxLayout(self)
        hint=QLabel('选择图床导出时，仅上传提取的图片；本地 assets 副本仍会保留。\nPicGo 使用本机上传服务，可由 PicGo 配置 GitHub、七牛、腾讯云、阿里云等。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form=QFormLayout()
        self.provider=QComboBox()
        self.provider.addItems(PRESETS)
        self.provider.setCurrentText(config.provider)
        form.addRow('接口类型',self.provider)
        self.fields={}
        for key,label in [('endpoint','上传 API 地址'),('token','密钥 / Token'),('field_name','文件表单字段'),
                          ('url_path','返回链接路径'),('auth_header','鉴权请求头'),('auth_prefix','密钥前缀（保留空格）'),
                          ('token_field','密钥表单字段（可留空）')]:
            editor=QLineEdit(str(getattr(config,key)))
            if key=='token': editor.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[key]=editor
            form.addRow(label,editor)
        self.fields['endpoint'].setPlaceholderText('例如 https://你的域名/api/v1/upload')
        self.fields['url_path'].setToolTip('JSON 中的网址路径，例如 data.url、data.links.url 或 result.0')
        self.extra=QLineEdit(json.dumps(config.extra_fields,ensure_ascii=False))
        self.extra.setPlaceholderText('{"strategy_id":"1"}')
        form.addRow('其他表单字段（JSON）',self.extra)
        self.remember=QCheckBox('在本机加密记住密钥（当前 Windows 用户）')
        self.remember.setChecked(remember)
        form.addRow('',self.remember)
        layout.addLayout(form)
        self.status=QLabel('各图床版本可能有差异，字段可修改。测试连接会实际上传一张 16×16 测试图片。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row=QHBoxLayout()
        self.test_btn=QPushButton('上传测试图片')
        self.test_btn.clicked.connect(self.probe)
        row.addWidget(self.test_btn)
        row.addStretch()
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        layout.addLayout(row)
        self.provider.activated.connect(self.preset)

    def preset(self,*_):
        values=PRESETS[self.provider.currentText()]
        for key,editor in self.fields.items():
            editor.setText(str(values.get(key,'Authorization' if key=='auth_header' else '')))
        self.extra.setText('{}')
        self.status.setText('已载入接口模板。切换服务后请重新填写密钥和上传地址。')

    def config(self):
        extra=json.loads(self.extra.text() or '{}')
        if not isinstance(extra,dict) or any(not isinstance(v,(str,int,float,bool)) for v in extra.values()):
            raise ValueError('其他表单字段应为 JSON 对象，值为文本或数字。')
        config=HostConfig(provider=self.provider.currentText(),extra_fields=extra,
                          **{k:e.text() for k,e in self.fields.items()})
        config.endpoint=config.endpoint.strip()
        config.validate()
        return config

    def save(self):
        try: self.value=self.config()
        except (ValueError,TypeError):
            self.status.setText('配置无效：请检查上传地址、JSON、文件字段和返回链接路径。')
            return
        self.accept()

    def probe(self):
        try: config=self.config()
        except (ValueError,TypeError):
            self.status.setText('请先填写有效的接口配置。')
            return
        self.test_btn.setEnabled(False)
        self.buttons.setEnabled(False)
        self.status.setText('正在上传测试图片…')
        self.worker=UploadProbe(config,self)
        self.worker.done.connect(lambda ok,text:self.status.setText(text))
        self.worker.finished.connect(self.probe_finished)
        self.worker.start()

    def probe_finished(self):
        self.test_btn.setEnabled(True)
        self.buttons.setEnabled(True)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.status.setText('测试上传尚未结束，请稍候。')
            return
        super().reject()

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning(): event.ignore()
        else: super().closeEvent(event)


class PreviewBrowser(QTextBrowser):
    """Use bounded image decoding, retaining full resolution export files."""
    def __init__(self):
        super().__init__()
        self.images=OrderedDict()
        self.setAcceptDrops(False)
        self.setOpenExternalLinks(False)
        self.fit_timer=QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.setInterval(100)
        self.fit_timer.timeout.connect(self.fit_images)

    def setMarkdown(self,text):
        super().setMarkdown(text)
        self.fit_images()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fit_timer.start()

    def fit_images(self):
        width=max(100,self.viewport().width()-40)
        block=self.document().begin()
        while block.isValid():
            iterator=block.begin()
            while not iterator.atEnd():
                fragment=iterator.fragment()
                if fragment.charFormat().isImageFormat():
                    fmt=fragment.charFormat().toImageFormat()
                    image=self.document().resource(QTextDocument.ResourceType.ImageResource,QUrl(fmt.name()))
                    if isinstance(image,QImage) and image.width()>0:
                        fitted=min(width,image.width())
                        height=image.height()*fitted/image.width()
                        if abs(fmt.width()-fitted)>.5 or abs(fmt.height()-height)>.5:
                            fmt.setWidth(fitted)
                            fmt.setHeight(height)
                            cursor=QTextCursor(self.document())
                            cursor.setPosition(fragment.position())
                            cursor.setPosition(fragment.position()+fragment.length(),QTextCursor.MoveMode.KeepAnchor)
                            cursor.setCharFormat(fmt)
                iterator+=1
            block=block.next()

    def loadResource(self,kind,url):
        if kind==QTextDocument.ResourceType.ImageResource:
            resolved=self.document().baseUrl().resolved(url)
            if not resolved.isLocalFile():
                return QImage()  # Preview does not download remote images.
            path=resolved.toLocalFile()
            try: key=(path,Path(path).stat().st_mtime_ns)
            except OSError: return QImage()
            if key in self.images:
                self.images.move_to_end(key)
                return self.images[key]
            reader=QImageReader(path)
            size=reader.size()
            if size.isValid() and max(size.width(),size.height())>1000:
                reader.setScaledSize(size.scaled(QSize(1000,1400),Qt.AspectRatioMode.KeepAspectRatio))
            value=reader.read()
            self.images[key]=value
            while len(self.images)>20: self.images.popitem(last=False)
            return value
        return super().loadResource(kind,url)
