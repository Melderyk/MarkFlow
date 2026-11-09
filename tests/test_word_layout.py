import shutil
from zipfile import ZipFile

import pytest
from docx import Document
from docx.shared import Cm, Pt

from markflow.core import ConversionError, Converter, Options
from markflow.formats import export_document
from markflow.word_layout import MARKER


def form(path):
    doc = Document()
    doc.sections[0].page_width = Cm(21)
    doc.sections[0].left_margin = Cm(1.7)
    doc.sections[0].header.paragraphs[0].text = 'Form header'
    table = doc.add_table(rows=5, cols=3)
    table.autofit = False
    table.columns[0].width = Cm(2)
    table.columns[1].width = Cm(7)
    table.columns[2].width = Cm(3)
    table.rows[0].height = Cm(1.2)
    table.cell(0, 0).merge(table.cell(0, 2)).text = 'Merged title'
    table.cell(1, 0).merge(table.cell(3, 0)).text = 'Vertical label'
    table.cell(1, 1).text = 'Editable value'
    run = table.cell(1, 1).paragraphs[0].runs[0]
    run.bold = True
    run.font.size = Pt(13)
    table.cell(4, 1).add_table(rows=1, cols=1).cell(0, 0).text = 'Nested table'
    doc.save(path)
    return path


def test_complex_form_keeps_merges_without_repeated_text_and_restores_exact_package(tmp_path):
    source = form(tmp_path / 'form.docx')
    original = source.read_bytes()
    result = Converter().convert(source, Options(tmp_path / 'out', output_format='docx'))
    assert '<table' in result.markdown
    assert 'colspan="3"' in result.markdown and 'rowspan="3"' in result.markdown
    assert result.markdown.count('Merged title') == 1
    assert result.markdown.count('Vertical label') == 1
    assert result.export.read_bytes() == original
    assert any('复杂合并表格' in message for message in result.warnings)


def test_portable_markdown_reimports_after_original_deleted(tmp_path):
    source = form(tmp_path / '含 空格%原文.docx')
    expected = source.read_bytes()
    first = Converter().convert(source, Options(tmp_path / 'first'))
    portable = tmp_path / 'portable'
    portable.mkdir()
    markdown = portable / first.output.name
    shutil.copy2(first.output, markdown)
    shutil.copytree(first.assets_dir, portable / first.assets_dir.name)
    source.unlink()
    shutil.rmtree(first.output.parent)
    second = Converter().convert(markdown, Options(tmp_path / 'second', output_format='docx'))
    assert second.export.read_bytes() == expected


def test_cell_text_edits_keep_original_table_geometry_runs_and_headers(tmp_path):
    source = form(tmp_path / 'form.docx')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    destination = tmp_path / 'edited.docx'
    export_document(result.markdown.replace('Editable value', 'Updated value'), destination, base=result.output.parent)
    doc = Document(destination)
    assert doc.tables[0].cell(1, 1).text == 'Updated value'
    assert doc.tables[0].cell(1, 1).paragraphs[0].runs[0].bold
    assert doc.tables[0].cell(1, 1).paragraphs[0].runs[0].font.size == Pt(13)
    assert doc.sections[0].header.paragraphs[0].text == 'Form header'
    original = Document(source)
    assert doc.sections[0]._sectPr.xml == original.sections[0]._sectPr.xml
    for xpath in ('./w:tblGrid', './w:tr/w:trPr', './/w:tcPr'):
        assert [node.xml for node in doc.tables[0]._tbl.xpath(xpath)] == [node.xml for node in original.tables[0]._tbl.xpath(xpath)]


def test_structure_edits_fail_before_overwriting_existing_word(tmp_path):
    source = form(tmp_path / 'form.docx')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    destination = tmp_path / 'existing.docx'
    destination.write_bytes(b'keep existing file')
    with pytest.raises(ConversionError, match='结构'):
        export_document(result.markdown.replace('colspan="3"', 'colspan="2"'), destination, base=result.output.parent)
    assert destination.read_bytes() == b'keep existing file'
    assert not list(tmp_path.glob('.existing-*'))


def test_filling_originally_empty_form_cell_keeps_merged_layout(tmp_path):
    from bs4 import BeautifulSoup
    source = form(tmp_path / 'form.docx')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    soup = BeautifulSoup(result.markdown, 'html.parser')
    blank = soup.select_one('span[data-mf-empty]')
    blank.string = 'Filled field'
    destination = tmp_path / 'filled.docx'
    export_document(str(soup), destination, base=result.output.parent)
    doc = Document(destination)
    assert 'Filled field' in ''.join(doc.element.body.itertext())
    assert doc.tables[0]._tbl.xpath('.//w:vMerge')
    soup.decompose()


def test_missing_or_tampered_reference_is_reported(tmp_path):
    source = form(tmp_path / 'form.docx')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    snapshot = result.assets_dir / 'word-original.docx'
    snapshot.unlink()
    with pytest.raises(ConversionError, match='缺少 Word 排版参考'):
        export_document(result.markdown, tmp_path / 'missing.docx', base=result.output.parent)


def test_plain_markdown_still_supports_original_text_encodings(tmp_path):
    source = tmp_path / 'encoded.md'
    source.write_bytes('# 原编码标题\n\n正文'.encode('gb18030'))
    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert '# 原编码标题' in result.markdown and '正文' in result.markdown


def test_standalone_html_table_exports_merges_without_layout_reference(tmp_path):
    source = form(tmp_path / 'form.docx')
    result = Converter().convert(source, Options(tmp_path / 'out'))
    destination = tmp_path / 'generic.docx'
    export_document(MARKER.sub('', result.markdown), destination, base=result.output.parent)
    doc = Document(destination)
    assert doc.tables[0]._tbl.xpath('.//w:gridSpan[@w:val="3"]')
    assert doc.tables[0]._tbl.xpath('.//w:vMerge')
    with ZipFile(destination) as archive:
        assert archive.read('word/document.xml').count(b'Merged title') == 1


def test_many_merged_tables_share_one_document_position_map(tmp_path, monkeypatch):
    from markflow import word_layout
    doc = Document()
    for i in range(20):
        doc.add_table(rows=1, cols=2).cell(0, 0).merge(doc.tables[-1].cell(0, 1)).text = f'Table {i}'
    source = tmp_path / 'tables.docx'
    doc.save(source)
    original = word_layout.text_positions
    calls = []
    def positions(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(word_layout, 'text_positions', positions)
    result = Converter().convert(source, Options(tmp_path / 'out'))
    assert result.markdown.count('<table ') == 20 and len(calls) == 1
