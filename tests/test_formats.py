import json
from pathlib import Path
import threading

import pytest
from docx import Document
from openpyxl import Workbook, load_workbook
from pptx import Presentation
from pptx.util import Inches

from markflow.core import Cancelled, ConversionError, Converter, Options
from markflow.formats import OUTPUT_FORMATS, export_document, sheets_markdown, workbook_data


def test_spreadsheet_import_json_roundtrip_and_formula_safety(tmp_path):
    source = tmp_path / 'budget.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = '预算'
    sheet.append(['项目', '金额', '公式'])
    sheet.append(['资料', 12.5, '=1+1'])
    sheet.merge_cells('A3:B3')
    sheet['A3'] = '合计'
    book.save(source)
    book.close()

    result = Converter().convert(source, Options(tmp_path / 'out', output_format='json'))
    data = json.loads(result.export.read_text(encoding='utf-8'))
    assert data['schema'] == 'markflow.workbook.v1'
    assert data['sheets'][0]['merged_cells'] == ['A3:B3']
    assert data['sheets'][0]['rows'][1][2] == '=1+1'
    assert '| 项目 | 金额 | 公式 |' in result.markdown

    restored = tmp_path / 'restored.xlsx'
    export_document(result.markdown, restored, base=result.output.parent, structured=data)
    loaded = load_workbook(restored, data_only=False)
    assert loaded['预算']['C2'].data_type == 's'
    assert loaded['预算']['C2'].value == '=1+1'
    assert 'A3:B3' in {str(item) for item in loaded['预算'].merged_cells.ranges}
    loaded.close()


def test_html_import_strips_active_content_and_exports_formats(tmp_path):
    source = tmp_path / 'page.html'
    source.write_text('<html><script>alert(1)</script><style>.x{}</style>'
        '<h1>本地内容</h1><p>正文 <b>加粗</b></p></html>', encoding='utf-8')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert '# 本地内容' in result.markdown
    assert '**加粗**' in result.markdown
    for suffix in ('html', 'tex', 'docx', 'pptx', 'pdf', 'json'):
        destination = tmp_path / f'export.{suffix}'
        export_document(result.markdown, destination, base=result.output.parent)
        assert destination.is_file() and destination.stat().st_size > 100
    html = (tmp_path / 'export.html').read_text(encoding='utf-8')
    assert '<script' not in html and 'alert(1)' not in html


def test_presentation_extracts_slide_text_and_notes(tmp_path):
    source = tmp_path / 'talk.pptx'
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = '概览'
    slide.placeholders[1].text = '重点内容'
    slide.notes_slide.notes_text_frame.text = '演讲备注'
    presentation.save(source)

    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert '# 概览' in result.markdown
    assert '重点内容' in result.markdown
    assert '演讲备注' in result.markdown
    assert '原版式不保留' in result.warnings[0]


def test_markflow_document_json_is_readable(tmp_path):
    source = tmp_path / 'document.json'
    source.write_text(json.dumps({'schema': 'markflow.document.v1', 'blocks': [
        {'type': 'paragraph', 'style': 'Heading 1', 'text': '介绍', 'runs': []},
        {'type': 'paragraph', 'style': 'Normal', 'text': '正文', 'runs': []},
        {'type': 'table', 'rows': [['A', 'B'], ['1', '2']]}]}), encoding='utf-8')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert '# 介绍' in result.markdown
    assert '| A | B |' in result.markdown


def test_configured_output_extensions_are_unique():
    assert len(set(OUTPUT_FORMATS.values())) == len(OUTPUT_FORMATS)


def test_word_json_keeps_paragraph_style_runs_and_tables(tmp_path):
    source = tmp_path / 'document.docx'
    doc = Document()
    doc.add_heading('标题', level=1)
    doc.add_paragraph().add_run('加粗内容').bold = True
    doc.add_table(rows=1, cols=2).cell(0, 0).text = '表格内容'
    doc.save(source)
    result = Converter().convert(source, Options(tmp_path / 'out', output_format='json'))
    data = json.loads(result.export.read_text(encoding='utf-8'))
    assert data['schema'] == 'markflow.document.v1'
    assert data['blocks'][0]['style'] == 'Heading 1'
    assert data['blocks'][1]['runs'][0]['bold'] is True
    assert data['blocks'][2]['rows'][0][0] == '表格内容'


def test_latex_markdown_roundtrip_keeps_math_and_valid_image_path(tmp_path):
    from PIL import Image
    source = tmp_path / 'formula.tex'
    source.write_text(r'\section{Formula} Here is $x^2 + y^2 = z^2$.', encoding='utf-8')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert '# Formula' in result.markdown and '$x^2 + y^2 = z^2$' in result.markdown
    image = result.output.parent / 'plot.png'
    Image.new('RGB', (24, 24), 'red').save(image)
    destination = tmp_path / 'formula-export.tex'
    export_document(result.markdown + '\n![Plot](plot.png)\n', destination, base=image.parent)
    text = destination.read_text(encoding='utf-8')
    assert r'\includegraphics' in text and 'out/plot.png' in text
    assert 'data:image' not in text and 'x^2 + y^2 = z^2' in text


def test_image_ocr_and_original_asset(tmp_path, monkeypatch):
    from PIL import Image
    source = tmp_path / 'scan.png'
    Image.new('RGB', (100, 60), 'white').save(source)
    monkeypatch.setattr(Converter, 'ocr_image', lambda *args: '识别文字')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert result.ocr_pages == 1 and '识别文字' in result.markdown
    assert len(list(result.assets_dir.glob('*.png'))) == 1
    with pytest.raises(ConversionError, match='开启'):
        Converter().convert(source, Options(tmp_path / 'disabled', ocr=False))


def test_sparse_csv_padding_is_bounded(tmp_path, monkeypatch):
    from markflow import formats
    monkeypatch.setattr(formats, 'MAX_CELLS', 20)
    source = tmp_path / 'sparse.csv'
    source.write_text('A,B,C,D,E\n1\n2\n3\n4\n', encoding='utf-8')
    with pytest.raises(ConversionError, match='补齐空列'):
        Converter().convert(source, Options(tmp_path / 'out'))
    assert not list((tmp_path / 'out').iterdir())


def test_workbook_limit_cancel_and_handle_release(tmp_path, monkeypatch):
    from markflow import formats
    source = tmp_path / 'limited.xlsx'
    book = Workbook()
    book.active.append(['A', 'B'])
    book.active.append([1, 2])
    book.save(source)
    book.close()
    monkeypatch.setattr(formats, 'MAX_CELLS', 3)
    with pytest.raises(ConversionError, match='表格过大'):
        workbook_data(source, None)
    # Windows cannot rename an open workbook; check error paths close it too.
    renamed = source.rename(tmp_path / 'renamed.xlsx')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        workbook_data(renamed, cancel)
    renamed.unlink()


def test_failed_export_leaves_no_partial_conversion_or_overwrite(tmp_path, monkeypatch):
    from markflow import formats
    source = tmp_path / 'source.csv'
    source.write_text('A,B\n1,2\n', encoding='utf-8')
    output = tmp_path / 'out'
    output.mkdir()
    existing = output / 'source.xlsx'
    existing.write_bytes(b'original')
    def fail(*args, **kwargs):
        raise ConversionError('模拟导出失败')
    monkeypatch.setattr(formats, 'export_document', fail)
    with pytest.raises(ConversionError, match='模拟导出失败'):
        Converter().convert(source, Options(output, output_format='xlsx'))
    assert list(output.iterdir()) == [existing]
    monkeypatch.undo()
    result = Converter().convert(source, Options(output, output_format='xlsx'))
    assert result.export.name == 'source_2.xlsx'
    assert existing.read_bytes() == b'original'


def test_cancelled_export_preserves_existing_file(tmp_path):
    destination = tmp_path / 'existing.html'
    destination.write_text('original', encoding='utf-8')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        export_document('# changed', destination, base=tmp_path, cancel=cancel)
    assert destination.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.iterdir()) == [destination]


def test_docx_package_limit_precedes_loading_in_conversion_and_preview(tmp_path, monkeypatch):
    from markflow import core, formats
    source = tmp_path / 'oversized.docx'
    Document().save(source)
    monkeypatch.setattr(formats, 'MAX_UNPACKED_BYTES', 100)
    def must_not_load(*args):
        raise AssertionError('DOCX must be checked before unzipping/parsing')
    monkeypatch.setattr(core, 'Document', must_not_load)
    for action in (lambda: Converter().convert(source, Options(tmp_path / 'out')),
                   lambda: core.docx_preview_html(source)):
        with pytest.raises(ConversionError, match='解压后超过'):
            action()
    assert not (tmp_path / 'out').exists()


def test_document_json_bounds_padded_sparse_tables(tmp_path, monkeypatch):
    from markflow import formats
    monkeypatch.setattr(formats, 'MAX_CELLS', 20)
    source = tmp_path / 'document.json'
    source.write_text(json.dumps({'schema': 'markflow.document.v1', 'blocks': [
        {'type': 'table', 'rows': [['A', 'B', 'C', 'D', 'E'], [1], [2], [3], [4]]}]}), encoding='utf8')
    with pytest.raises(ConversionError, match='补齐空列后'):
        Converter().convert(source, Options(tmp_path / 'out'))
    assert not list((tmp_path / 'out').iterdir())

