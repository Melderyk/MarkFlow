import io
import re
import shutil
import socket
from zipfile import ZipFile

import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument

from markflow.core import Assets, ConversionError, Converter, Options
from markflow.embedded_images import decode_image_url, image_data_url
from markflow.embedded_preview import EmbeddedPreviewBrowser
from markflow.formats import export_document
from tests.samples import create_samples
from tests.test_ui import qt_app, wait_for
from tests.test_word_layout import form


def bitmap(size=(120, 60)):
    with Image.new('RGB', size, '#336699') as image, io.BytesIO() as buffer:
        image.save(buffer, format='PNG')
        return buffer.getvalue()


def test_docx_single_md_carries_repeated_images_after_moving_alone(tmp_path, monkeypatch):
    source = tmp_path / 'source.docx'
    doc = Document()
    doc.add_heading('Chapter one', 1)
    doc.add_picture(io.BytesIO(bitmap()), width=Inches(1))
    doc.add_heading('Chapter two', 1)
    doc.add_picture(io.BytesIO(bitmap()), width=Inches(1))
    doc.save(source)
    def no_network(*args, **kwargs):
        raise AssertionError('Embedding must not use network')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    output = tmp_path / 'output'
    result = Converter().convert(source, Options(output, image_mode='embed'))
    assert list(output.iterdir()) == [result.output]
    assert result.assets_dir is None and result.chapters_dir is None
    assert result.embedded_images == 1
    targets = re.findall(r'!\[[^\]]*\]\((data:[^)]+)\)', result.markdown)
    assert len(targets) == 2 and targets[0] == targets[1]
    assert decode_image_url(targets[0])[0] == bitmap()
    moved = tmp_path / 'alone' / 'portable.md'
    moved.parent.mkdir()
    shutil.copyfile(result.output, moved)
    shutil.rmtree(output)
    reloaded = Converter().convert(moved, Options(tmp_path / 'reloaded', image_mode='embed'))
    assert 'data:image/png;base64,' in reloaded.markdown and reloaded.assets_dir is None


def test_embedded_pdf_and_image_ocr_have_no_picture_directory(tmp_path, monkeypatch):
    samples = create_samples(tmp_path / 'samples')
    result = Converter().convert(samples[1], Options(tmp_path / 'pdf', image_mode='embed'))
    assert result.pages == 2 and result.embedded_images == 1
    assert result.assets_dir is None and 'data:image/png;base64,' in result.markdown
    image = tmp_path / 'scan.png'
    image.write_bytes(bitmap())
    monkeypatch.setattr(Converter, 'ocr_image', lambda *args: 'Recognized content')
    result = Converter().convert(image, Options(tmp_path / 'ocr', image_mode='embed'))
    assert 'Recognized content' in result.markdown and result.embedded_images == 1
    assert list(result.output.parent.iterdir()) == [result.output]


def test_complex_word_embeds_html_pictures_and_preserves_reference(tmp_path):
    source = form(tmp_path / 'form.docx')
    doc = Document(source)
    doc.tables[0].cell(1, 1).paragraphs[0].add_run().add_picture(io.BytesIO(bitmap()), width=Inches(1))
    doc.save(source)
    result = Converter().convert(source, Options(tmp_path / 'out', image_mode='embed', output_format='docx'))
    assert '<img' in result.markdown and 'src="data:image/png;base64,' in result.markdown
    assert result.embedded_images == 1
    assert result.export.read_bytes() == source.read_bytes()
    assert {p.name for p in result.assets_dir.iterdir()} == {'word-original.docx', 'word-layout.json'}
    assert any('原排版参考仍' in text for text in result.warnings)
    export_document(result.markdown.replace('Editable value', 'Updated value'), tmp_path / 'updated.docx', base=result.output.parent)
    assert Document(tmp_path / 'updated.docx').tables[0].cell(1, 1).text == 'Updated value'


@pytest.mark.parametrize('suffix', ['html', 'docx', 'pptx', 'pdf', 'tex'])
def test_embedded_images_survive_further_exports(tmp_path, suffix):
    uri, _ = image_data_url(bitmap())
    destination = tmp_path / ('portable.' + suffix)
    export_document('# Portable\n\n![picture](' + uri + ')\n', destination, base=tmp_path)
    assert destination.stat().st_size > 100
    if suffix == 'html':
        assert uri in destination.read_text(encoding='utf8')
    elif suffix in {'docx', 'pptx'}:
        with ZipFile(destination) as archive:
            assert any('/media/' in name for name in archive.namelist())
    elif suffix == 'tex':
        path = next(destination.with_suffix('.assets').glob('embedded-*.png'))
        assert path.read_bytes() == bitmap() and path.name in destination.read_text(encoding='utf8')


def test_embedding_limits_abort_without_partial_output(tmp_path, monkeypatch):
    from markflow import embedded_images
    source = tmp_path / 'scan.png'
    source.write_bytes(bitmap())
    monkeypatch.setattr(Converter, 'ocr_image', lambda *args: 'Text')
    monkeypatch.setattr(embedded_images, 'MAX_TOTAL_BYTES', 1)
    with pytest.raises(ConversionError, match='合计超过'):
        Converter().convert(source, Options(tmp_path / 'out', image_mode='embed'))
    assert not list((tmp_path / 'out').iterdir())
    monkeypatch.setattr(embedded_images, 'MAX_TOTAL_BYTES', 1024 * 1024)
    monkeypatch.setattr(embedded_images, 'MAX_DOCUMENT_BYTES', 10)
    with pytest.raises(ConversionError, match='Markdown 超过'):
        Converter().convert(source, Options(tmp_path / 'out', image_mode='embed'))
    assert not list((tmp_path / 'out').iterdir())


def test_failed_tex_export_removes_only_new_picture_resources(tmp_path, monkeypatch):
    from markflow import formats
    uri, _ = image_data_url(bitmap())
    original = formats.pandoc
    def fail_writer(text, reader, writer, **kwargs):
        if writer == 'latex':
            raise ConversionError('Simulated export failure')
        return original(text, reader, writer, **kwargs)
    monkeypatch.setattr(formats, 'pandoc', fail_writer)
    destination = tmp_path / 'existing.tex'
    destination.write_text('Original', encoding='utf8')
    with pytest.raises(ConversionError, match='Simulated'):
        export_document(f'![image]({uri})', destination, base=tmp_path)
    assert list(tmp_path.iterdir()) == [destination]
    assert destination.read_text(encoding='utf8') == 'Original'


def test_embedding_single_image_limit_and_invalid_payloads(tmp_path, monkeypatch):
    from markflow import embedded_images
    monkeypatch.setattr(embedded_images, 'MAX_IMAGE_BYTES', 10)
    with pytest.raises(ConversionError, match='单张嵌入图片'):
        Assets(tmp_path, 'test', '', 'embed').add(bitmap(), 'png')
    for value in ('data:image/png;base64,AAAA', 'data:image/svg+xml;base64,PHN2Zz4=', 'https://example.test/a.png'):
        with pytest.raises(ConversionError):
            decode_image_url(value)
    assert not list(tmp_path.iterdir())


def test_inline_preview_scales_images_and_releases_documents(qt_app):
    browser = EmbeddedPreviewBrowser()
    browser.resize(420, 400)
    browser.show()
    for i in range(25):
        uri, _ = image_data_url(bitmap((1400 + i, 900)))
        browser.setMarkdown(f'# Section {i}\n\n![picture]({uri})\n')
        qt_app.processEvents()
        image = browser.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(uri))
        assert not image.isNull() and image.width() <= 1000
        assert len(browser.images) == 1
        assert len(browser.findChildren(QTextDocument)) == 1
    browser.setMarkdown('# Empty\n')
    assert not browser.images
    browser.close()


def test_embedding_mode_persists_without_host_validation(qt_app, tmp_path, monkeypatch):
    from markflow import app as app_module
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    source = tmp_path / 'scan.png'
    source.write_bytes(bitmap())
    window = app_module.MainWindow()
    window.image_mode.setCurrentIndex(window.image_mode.findData('embed'))
    window.ocr.setChecked(False)  # Worker will report the expected OCR-disabled error.
    window.output_edit.setText(str(tmp_path / 'out'))
    window.host_config.endpoint = 'not-a-url'
    window.add_files([source])
    window.start_batch()
    assert window.running and window.worker.options.image_mode == 'embed'
    wait_for(qt_app, lambda: not window.running)
    assert '需要开启' in window.entries[0].error
    assert not window.chapters.isEnabled()
    window.close()
    other = app_module.MainWindow()
    assert other.image_mode.currentData() == 'embed' and not other.chapters.isEnabled()
    other.close()
