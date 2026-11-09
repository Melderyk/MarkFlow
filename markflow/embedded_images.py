"""Bounded bitmap data URLs for portable Markdown; never fetch remote media."""
import base64
import binascii
import io
import re

from PIL import Image

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 24_000_000
MIME_TYPES = {'PNG': 'image/png', 'JPEG': 'image/jpeg',
              'GIF': 'image/gif', 'WEBP': 'image/webp'}
DATA_IMAGE = re.compile(r'^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/]*={0,2})$', re.I)


def image_data_url(data):
    from .core import ConversionError
    if len(data) > MAX_IMAGE_BYTES:
        raise ConversionError('单张嵌入图片超过 8 MB，请缩小图片或选择本地图片模式。')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ConversionError('嵌入图片超过 2400 万像素，请缩小图片。')
            mime = MIME_TYPES.get(image.format)
            if mime is None:
                with image.convert('RGB') as rgb, io.BytesIO() as buffer:
                    rgb.save(buffer, format='PNG')
                    data = buffer.getvalue()
                mime = 'image/png'
                if len(data) > MAX_IMAGE_BYTES:
                    raise ConversionError('转为 PNG 后的嵌入图片超过 8 MB，请选择本地图片模式。')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ConversionError('此图片无法安全嵌入，请选择本地图片模式。') from exc
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii'), len(data)


def decode_image_url(value):
    from .core import ConversionError
    # Check the length before parsing/decoding attacker-supplied Markdown.
    if len(value) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 64:
        raise ConversionError('嵌入图片超过 8 MB，请缩小图片。')
    match = DATA_IMAGE.fullmatch(value)
    if match is None:
        raise ConversionError('仅支持 PNG、JPEG、GIF、WebP 的 Base64 嵌入图片。')
    try:
        data = base64.b64decode(match[2], validate=True)
        with Image.open(io.BytesIO(data)) as image:
            if (len(data) > MAX_IMAGE_BYTES or image.width * image.height > MAX_PIXELS
                    or MIME_TYPES.get(image.format) != match[1].lower()):
                raise ConversionError('嵌入图片格式或尺寸无效。')
            image.verify()
    except (OSError, ValueError, binascii.Error, Image.DecompressionBombError) as exc:
        raise ConversionError('嵌入图片数据已损坏或不受支持。') from exc
    return data, match[1].lower()
