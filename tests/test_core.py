from pathlib import Path
import re
import socket
import threading
from urllib.parse import unquote

import pytest
from markflow.core import Cancelled, ConversionError, Converter, Options, atomic_write, merge_results, pdf_preview, safe_name
from tests.samples import create_samples


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    return create_samples(tmp_path_factory.mktemp("samples"))


def test_docx_preserves_semantics_and_assets(samples, tmp_path):
    result = Converter().convert(samples[0], Options(tmp_path))
    assert "# 墨页 MarkFlow 使用示例" in result.markdown
    assert "# 1. 文本与格式" in result.markdown
    assert "**加粗文字**" in result.markdown
    assert "*斜体文字*" in result.markdown
    assert "[示例链接](https://example.com)" in result.markdown
    assert "- 添加 PDF" in result.markdown
    assert "1. 对照原文" in result.markdown
    assert "| 功能 | 模式 | 状态 |" in result.markdown
    assert r"图像 \| 导出" in result.markdown
    target = re.search(r"!\[[^\]]*\]\(([^)]+)\)", result.markdown).group(1)
    assert (tmp_path / unquote(target)).is_file()
    assert result.output.read_text(encoding="utf-8") == result.markdown


def test_pdf_extracts_tables_links_image_and_all_pages(samples, tmp_path):
    result = Converter().convert(samples[1], Options(tmp_path))
    assert result.pages == 2 and result.ocr_pages == 0
    assert "MarkFlow" in result.markdown
    assert "Offline conversion" in result.markdown
    assert "| Feature | Mode | Status |" in result.markdown
    assert "https://example.com" in result.markdown
    assert "Second" in result.markdown and "Page preview" in result.markdown
    assert len(list(result.assets_dir.glob("*.png"))) == 1
    image, count = pdf_preview(samples[1], 1)
    assert count == 2 and image.width > 500


def test_scanned_pdf_ocr_is_offline(samples, tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("OCR must not access network")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    result = Converter().convert(samples[2], Options(tmp_path))
    assert result.ocr_pages == 1
    assert "MarkFlow" in result.markdown
    assert "128.50" in result.markdown
    assert "文件不会上传到网络" in result.markdown


def test_mixed_pdf_ocr_is_per_page(samples, tmp_path):
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for path in [samples[1], samples[2]]:
        writer.append(PdfReader(path))
    mixed = tmp_path / "mixed.pdf"
    writer.write(mixed)
    result = Converter().convert(mixed, Options(tmp_path / "out"))
    assert result.pages == 3 and result.ocr_pages == 1
    assert "Offline conversion" in result.markdown and "128.50" in result.markdown


def test_unique_outputs_and_merge_use_current_drafts(samples, tmp_path):
    engine = Converter()
    first = engine.convert(samples[0], Options(tmp_path))
    second = engine.convert(samples[0], Options(tmp_path))
    assert first.output != second.output
    output = tmp_path / "different output"
    output.mkdir()
    destination = merge_results([first, second], output, "通用 Markdown", {str(first.output): first.markdown + "\nEDITED TEXT"})
    text = destination.read_text(encoding="utf-8")
    assert "EDITED TEXT" in text and '<a id="document-1"></a>' in text
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        assert (output / unquote(target)).resolve().is_file()
    assert "[[#文档 1" in merge_results([first], output, "Obsidian").read_text(encoding="utf-8")
    assert "[TOC]" in merge_results([first], output, "Typora").read_text(encoding="utf-8")


def test_cancellation_invalid_inputs_and_atomic_save(samples, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        Converter().convert(samples[0], Options(tmp_path), cancel=cancel)
    assert not list(tmp_path.glob(".markflow-*"))
    corrupt = tmp_path / "broken.pdf"
    corrupt.write_text("not pdf", encoding="utf-8")
    with pytest.raises(ConversionError):
        Converter().convert(corrupt, Options(tmp_path))
    assert not list(tmp_path.glob(".markflow-*"))
    assert safe_name("CON") == "_CON"
    assert "/" not in safe_name("a/b")
    target = tmp_path / "edit.md"
    atomic_write(target, "# changed\n")
    assert target.read_text(encoding="utf-8") == "# changed\n"
    assert not list(tmp_path.glob("*.tmp"))
