"""Embedded image support without changing the existing hosting/preview module."""
import hashlib

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QTextDocument

from .embedded_images import decode_image_url
from .ui_helpers import PreviewBrowser
from .core import ConversionError


class EmbeddedPreviewBrowser(PreviewBrowser):
    def loadResource(self, kind, url):
        if kind != QTextDocument.ResourceType.ImageResource or url.scheme().lower() != 'data':
            return super().loadResource(kind, url)
        value = url.toString()
        key = ('embedded', hashlib.sha256(value.encode('ascii', 'replace')).digest())
        if key in self.images:
            self.images.move_to_end(key)
            return self.images[key]
        try:
            data, _ = decode_image_url(value)
        except (ValueError, ConversionError):
            return QImage()
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        try:
            reader = QImageReader(buffer)
            size = reader.size()
            if size.isValid() and max(size.width(), size.height()) > 1000:
                reader.setScaledSize(size.scaled(QSize(1000, 1400), Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
        finally:
            buffer.close()
        self.images[key] = image
        while len(self.images) > 20:
            self.images.popitem(last=False)
        return image
