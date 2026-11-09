"""Merged Word tables and portable, bounded layout references for round trips."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote, unquote
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

MARKER = re.compile(r'<!-- markflow-word-layout: ([^\r\n]+?) -->')
MAX_PACKAGE = 64 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024


def complex_table(table):
    return bool(table._tbl.xpath('.//w:gridSpan[@w:val!="1"]|.//w:vMerge|./w:tr/w:tc/w:tbl'))


def text_positions(doc, cancel):
    from .core import Converter
    maps = []
    for tag in ('w:t', 'w:p'):
        positions = {}
        for i, node in enumerate(doc.element.body.iter(qn(tag))):
            if i % 1024 == 0:
                Converter.check(cancel)
            positions[node] = i
        maps.append(positions)
    return maps


def table_html(table, doc, assets, cancel, positions=None):
    """Walk physical cells once; row.cells repeats horizontally/vertically merged cells."""
    from .core import Converter
    text_ids, paragraph_ids = positions if positions is not None else text_positions(doc, cancel)

    def paragraph(p):
        parts = []
        for node in p._p.iter():
            if node.tag == qn('w:t'):
                run = node.getparent()
                props = run.find(qn('w:rPr'))
                style = []
                if props is not None:
                    for name, css in [('b', 'font-weight:bold'), ('i', 'font-style:italic'),
                                      ('u', 'text-decoration:underline')]:
                        value = props.find(qn('w:' + name))
                        if value is not None and value.get(qn('w:val'), '1') not in {'0', 'false', 'off', 'none'}:
                            style.append(css)
                attrs = f' data-mf-text="{text_ids[node]}"'
                if style:
                    attrs += ' style="' + ';'.join(style) + '"'
                parts.append('<span' + attrs + '>' + html.escape(node.text or '') + '</span>')
            elif node.tag in {qn('w:br'), qn('w:cr')}:
                parts.append('<br>')
            elif node.tag == qn('w:tab'):
                parts.append('&#9;')
            elif node.tag.endswith('}blip'):
                rid = node.get(qn('r:embed'))
                part = doc.part.related_parts.get(rid)
                if part is not None:
                    link = assets.add(part.blob, Path(str(part.partname)).suffix[1:], key=rid)
                    target = unquote(link.split('](', 1)[1][:-1])
                    parts.append('<img alt="图片" src="' + html.escape(target, quote=True) + '">')
        if not parts:
            parts.append(f'<span data-mf-empty="{paragraph_ids[p._p]}"></span>')
        align = p._p.xpath('./w:pPr/w:jc/@w:val')
        css = 'white-space:pre-wrap'
        if align and align[0] in {'left', 'right', 'center', 'both'}:
            css += ';text-align:' + ('justify' if align[0] == 'both' else align[0])
        return '<p style="' + css + '">' + ''.join(parts) + '</p>'

    def render(current):
        rows, active = [], {}
        for row in current._tbl.tr_lst:
            Converter.check(cancel)
            before = row.xpath('./w:trPr/w:gridBefore/@w:val')
            col = int(before[0]) if before else 0
            cells, continuing = [], {}
            if col:
                cells.append({'span': col, 'rows': 1, 'html': '', 'width': None})
            for tc in row.tc_lst:
                spans = tc.xpath('./w:tcPr/w:gridSpan/@w:val')
                span = int(spans[0]) if spans else 1
                merges = tc.xpath('./w:tcPr/w:vMerge')
                key = (col, span)
                if merges and merges[0].get(qn('w:val'), 'continue') == 'continue' and key in active:
                    active[key]['rows'] += 1
                    continuing[key] = active[key]
                else:
                    cell = _Cell(tc, current)
                    contents = []
                    for block in cell.iter_inner_content():
                        contents.append(render(block) if isinstance(block, Table) else paragraph(block))
                    width = tc.xpath('./w:tcPr/w:tcW[@w:type="dxa"]/@w:w')
                    item = {'span': span, 'rows': 1, 'html': ''.join(contents),
                            'width': int(width[0]) / 20 if width else None}
                    cells.append(item)
                    if merges:
                        continuing[key] = item
                col += span
            heights = row.xpath('./w:trPr/w:trHeight/@w:val')
            rows.append((cells, int(heights[0]) / 20 if heights else None))
            active = continuing
        result = ['<table style="border-collapse:collapse">']
        widths = current._tbl.xpath('./w:tblGrid/w:gridCol/@w:w')
        if widths:
            result.append('<colgroup>' + ''.join(f'<col style="width:{int(w)/20:g}pt">' for w in widths) + '</colgroup>')
        for cells, height in rows:
            result.append(f'<tr style="height:{height:g}pt">' if height else '<tr>')
            for cell in cells:
                attrs = (f' colspan="{cell["span"]}"' if cell['span'] > 1 else '')
                attrs += (f' rowspan="{cell["rows"]}"' if cell['rows'] > 1 else '')
                style = 'border:1px solid #777;vertical-align:middle;padding:2px'
                if cell['width']:
                    style += f';width:{cell["width"]:g}pt'
                result.append('<td' + attrs + ' style="' + style + '">' + cell['html'] + '</td>')
            result.append('</tr>')
        result.append('</table>')
        return '\n'.join(result)

    return render(table)


def _package_ok(path):
    from .core import ConversionError
    if path.stat().st_size > MAX_PACKAGE:
        raise ConversionError('Word 排版参考超过 64 MB，请拆分文件。')
    with ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_UNPACKED:
            raise ConversionError('Word 排版参考解压后超过 256 MB，请拆分文件。')


def save_reference(source, text, assets):
    _package_ok(source)
    assets.folder.mkdir(exist_ok=True)
    snapshot = assets.folder / 'word-original.docx'
    shutil.copyfile(source, snapshot)
    data = {'schema': 'markflow.word-layout.v1', 'markdown': text.strip(),
            'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            'images': [p.name for p in assets.folder.iterdir() if p.name.startswith('image_')]}
    manifest = assets.folder / 'word-layout.json'
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    target = quote(assets.relative + '/' + manifest.name, safe='/._-')
    return text.rstrip() + '\n\n<!-- markflow-word-layout: ' + target + ' -->\n'


def load_reference(text, base):
    from .core import ConversionError
    matches = list(MARKER.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise ConversionError('文档包含多个 Word 排版参考，请分别导出原文档。')
    target = (Path(base) / unquote(matches[0][1])).resolve()
    base = Path(base).resolve()
    if (not target.is_relative_to(base) or target.name != 'word-layout.json'
            or not target.parent.name.endswith('.assets')):
        raise ConversionError('Word 排版参考路径无效。')
    snapshot = target.with_name('word-original.docx')
    if not target.is_file() or not snapshot.is_file():
        raise ConversionError('缺少 Word 排版参考，请将 Markdown 和同名 .assets 文件夹一起移动。')
    if target.stat().st_size > MAX_PACKAGE:
        raise ConversionError('Word 排版参考数据过大。')
    data = json.loads(target.read_text(encoding='utf-8'))
    _package_ok(snapshot)
    if (data.get('schema') != 'markflow.word-layout.v1'
            or hashlib.sha256(snapshot.read_bytes()).hexdigest() != data.get('sha256')):
        raise ConversionError('Word 排版参考已损坏，请从原 Word 重新转换。')
    return target, snapshot, data


def import_reference(source, assets, cancel):
    from .core import Converter, ConversionError
    from .formats import read_text
    text = read_text(source)
    reference = load_reference(text, source.parent)
    if reference is None:
        return None
    target, snapshot, data = reference
    assets.folder.mkdir(exist_ok=True)
    shutil.copyfile(snapshot, assets.folder / snapshot.name)
    total = snapshot.stat().st_size
    for name in data.get('images', []):
        Converter.check(cancel)
        if not isinstance(name, str) or not re.fullmatch(r'image_\d+\.[a-z0-9]+', name):
            raise ConversionError('Word 图片参考路径无效。')
        image = target.parent / name
        if not image.is_file():
            raise ConversionError('缺少原 Word 图片参考，请同时复制完整 .assets 文件夹。')
        total += image.stat().st_size
        if total > MAX_UNPACKED:
            raise ConversionError('Word 排版参考和图片超过 256 MB。')
        shutil.copyfile(image, assets.folder / name)
    old, new = target.parent.name + '/', assets.relative + '/'
    # Generated image URLs are percent-encoded, HTML img paths are not.
    for before, after in [(quote(old, safe='/._-'), quote(new, safe='/._-')), (old, new)]:
        text = text.replace(before, after)
        data['markdown'] = data['markdown'].replace(before, after)
    (assets.folder / target.name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return text


def export_reference(text, destination, base, cancel):
    """Restore unchanged packages exactly; patch mapped text without resetting runs/styles."""
    from .core import Converter, ConversionError
    reference = load_reference(text, base)
    if reference is None:
        return False
    _, snapshot, data = reference
    current = MARKER.sub('', text).strip()
    baseline = data['markdown'].strip()
    Converter.check(cancel)
    if current == baseline:
        shutil.copyfile(snapshot, destination)
        return True
    from bs4 import BeautifulSoup
    before = BeautifulSoup(baseline, 'html.parser')
    after = BeautifulSoup(current, 'html.parser')
    try:
        def mapped(soup):
            result = {}
            for span in soup.select('span[data-mf-text], span[data-mf-empty]'):
                key = ('t:' + span['data-mf-text'] if span.has_attr('data-mf-text')
                       else 'p:' + span['data-mf-empty'])
                if key in result or span.find(True) is not None:
                    raise ConversionError('Word 表格文本标记结构已改变，请保留表格结构后重试。')
                result[key] = span.get_text()
                span.string = 'MARKFLOW_TEXT_' + key
            return result
        old_text, new_text = mapped(before), mapped(after)
        if old_text.keys() != new_text.keys() or str(before) != str(after):
            raise ConversionError('当前修改改变了原 Word 的表格、样式或正文结构，无法安全保留原排版。'
                                  '请保持表格结构，仅修改单元格文字后重试；普通 Markdown 可另建文档常规导出。')
        doc = Document(snapshot)
        nodes = list(doc.element.body.iter(qn('w:t')))
        paragraphs = list(doc.element.body.iter(qn('w:p')))
        for key, value in new_text.items():
            Converter.check(cancel)
            kind, position = key.split(':')
            index = int(position)
            items = nodes if kind == 't' else paragraphs
            if index < 0 or index >= len(items):
                raise ConversionError('Word 排版文本位置无效。')
            if value != old_text[key]:
                if kind == 't':
                    nodes[index].text = value
                    nodes[index].set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                else:
                    paragraph = Paragraph(paragraphs[index], doc._body)
                    runs = paragraph.runs
                    # Keep any empty styled run supplied by the original form.
                    (runs[0] if runs else paragraph.add_run()).add_text(value)
        doc.save(destination)
        return True
    finally:
        before.decompose()
        after.decompose()
