"""Local format adapters. Markdown remains the editable working document."""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile
from xml.etree import ElementTree as ET

INPUT_SUFFIXES = frozenset({'.pdf', '.docx', '.html', '.htm', '.xlsx', '.xls',
    '.csv', '.tsv', '.pptx', '.tex', '.latex', '.md', '.markdown', '.json',
    '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'})
IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'})
OUTPUT_FORMATS = {'Markdown (.md)': 'md', 'Word (.docx)': 'docx',
    'Excel 表格 (.xlsx)': 'xlsx', 'PowerPoint (.pptx)': 'pptx',
    'HTML (.html)': 'html', 'PDF (.pdf)': 'pdf', 'LaTeX (.tex)': 'tex',
    '结构化 JSON (.json)': 'json'}
OUTPUT_SUFFIXES = frozenset(OUTPUT_FORMATS.values())
FILE_FILTER = '支持的文档 (' + ' '.join('*' + s for s in sorted(INPUT_SUFFIXES)) + ');;所有文件 (*)'
MARKDOWN_READER = 'markdown+tex_math_dollars+pipe_tables+task_lists+fenced_code_attributes-markdown_in_html_blocks'
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_SHARED_STRINGS_BYTES = 32 * 1024 * 1024
MAX_CELLS = 500_000
MAX_ROWS = 100_000
MAX_COLUMNS = 2048
MAX_TEXT_CHARS = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000


def check_input(source):
    from .core import ConversionError
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ConversionError('扩展格式输入超过 64 MB，请拆分文件。')
    if source.suffix.lower() in {'.xlsx', '.pptx'}:
        with ZipFile(source) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise ConversionError('文档解压后超过 256 MB，请拆分文件。')


def pandoc(text, reader, writer, *, output=None, cancel=None):
    """Invoke the bundled executable without a shell, filters or TeX execution."""
    from .core import Converter, ConversionError
    executable = Path(getattr(sys, '_MEIPASS', '')) / 'pandoc' / 'pandoc.exe'
    if not executable.is_file():
        try:
            import pypandoc
            executable = Path(pypandoc.get_pandoc_path())
        except (ImportError, OSError) as exc:
            raise ConversionError('此格式需要可选扩展：安装 requirements-formats.txt（原依赖版本保持锁定）。') from exc
    args = [str(executable), '--sandbox', '--from', reader, '--to', writer,
            '--wrap=none']
    if writer in {'html5', 'latex', 'docx', 'pptx'}:
        args += ['--standalone']
    if output is not None:
        args += ['--output', str(output)]
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **kwargs)
    deadline = time.monotonic() + 120
    first = True
    try:
        while True:
            Converter.check(cancel)
            if time.monotonic() > deadline:
                raise ConversionError('格式转换超过 120 秒，请拆分文档后重试。')
            try:
                out, err = process.communicate(text.encode('utf-8') if first else None, timeout=.1)
                break
            except subprocess.TimeoutExpired:
                first = False
        if process.returncode:
            # Keep useful diagnostics without exposing complete document bodies.
            raise ConversionError('格式转换失败：' + err.decode('utf-8', 'replace')[-600:])
        return out.decode('utf-8')
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def read_text(source):
    check_input(source)
    data = source.read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    from .core import ConversionError
    raise ConversionError('无法识别文本编码，请将文件另存为 UTF-8。')


def clean_html(text):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, 'html.parser')
    for element in soup(['script', 'style', 'iframe', 'object', 'embed', 'link', 'base',
                         'meta', 'form', 'input', 'button', 'svg']):
        element.decompose()
    for element in soup.find_all(True):
        element.attrs = {k: v for k, v in element.attrs.items()
                         if k in {'href', 'src', 'alt', 'title', 'colspan', 'rowspan'}}
        for key in ('href', 'src'):
            value = element.get(key, '')
            if urlsplit(value.strip()).scheme.lower() in {'javascript', 'vbscript'}:
                del element[key]
    try:
        return str(soup)
    finally:
        soup.decompose()


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def expand_html_tables(ast, cancel):
    """Parse safe HTML tables into native Pandoc tables, including merged cells."""
    def expand(blocks):
        result = []
        for block in blocks:
            if (isinstance(block, dict) and block.get('t') == 'RawBlock'
                    and block['c'][0] == 'html' and '<table' in block['c'][1].lower()):
                parsed = json.loads(pandoc(clean_html(block['c'][1]), 'html', 'json', cancel=cancel))
                result.extend(parsed['blocks'])
            else:
                result.append(block)
        return result
    ast['blocks'] = expand(ast.get('blocks', []))
    return ast


def local_image(target, base):
    """Only read media inside the input document directory; never fetch URLs."""
    from .core import ConversionError
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        raise ConversionError('远程图片和绝对文件网址不自动读取')
    path = (base / unquote(parsed.path)).resolve()
    if not path.is_relative_to(base.resolve()) or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ConversionError('图片必须位于文档目录内，并使用支持的位图格式')
    if not path.is_file():
        raise ConversionError('本地图片不存在')
    if path.stat().st_size > 40 * 1024 * 1024:
        raise ConversionError('图片超过 40 MB')
    return path


def import_markup(source, assets, warnings, cancel):
    from .core import ConversionError
    suffix = source.suffix.lower()
    reader = 'html' if suffix in {'.html', '.htm'} else 'latex' if suffix in {'.tex', '.latex'} else MARKDOWN_READER
    text = read_text(source)
    if reader == 'html':
        text = clean_html(text)
        warnings.append('HTML 按正文结构转换；不执行脚本，不加载远程资源，CSS 和网页打印版式不保留。')
    ast = expand_html_tables(json.loads(pandoc(text, reader, 'json', cancel=cancel)), cancel)
    for node in walk(ast):
        if node.get('t') in {'RawBlock', 'RawInline'}:
            # Raw HTML/TeX is not executed or propagated into generated files.
            node['c'][1] = ''
        if node.get('t') == 'Image':
            target = node['c'][-1][0]
            try:
                if target.startswith('data:image/'):
                    header, encoded = target.split(',', 1)
                    if ';base64' not in header or len(encoded) > 56 * 1024 * 1024:
                        raise ConversionError('内嵌图片编码不支持或过大')
                    data = base64.b64decode(encoded, validate=True)
                    from PIL import Image
                    with Image.open(io.BytesIO(data)) as image:
                        if image.width * image.height > MAX_IMAGE_PIXELS:
                            raise ConversionError('图片超过 2400 万像素')
                        with io.BytesIO() as buffer, image.convert('RGB') as rgb:
                            rgb.save(buffer, format='PNG')
                            link = assets.add(buffer.getvalue(), 'png')
                else:
                    path = local_image(target, source.parent)
                    link = assets.add(path.read_bytes(), path.suffix[1:], key=str(path))
                node['c'][-1][0] = unquote(link.split('](', 1)[1][:-1])
            except (ValueError, OSError, ConversionError):
                warnings.append('图片未导入（不自动下载远程资源或读取目录外文件）：' + target[:120])
                node.clear()
                node.update(t='Str', c='[图片未导入]')
    return pandoc(json.dumps(ast), 'json', 'markdown+tex_math_dollars-pipe_tables+pipe_tables', cancel=cancel)


def json_value(value):
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def workbook_merges(source, cancel):
    """Read merge metadata without loading millions of styled empty cells."""
    from .core import Converter, ConversionError
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(source) as archive:
        shared_strings = next((item.file_size for item in archive.infolist()
                               if item.filename == 'xl/sharedStrings.xml'), 0)
        if shared_strings > MAX_SHARED_STRINGS_BYTES:
            raise ConversionError('工作簿共享文本超过 32 MB，请拆分文件。')
        relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in relations}
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        result = {}
        for sheet in workbook.findall('m:sheets/m:sheet', ns):
            Converter.check(cancel)
            relation = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = targets[relation]
            import posixpath
            path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            merged = []
            parents = []
            with archive.open(path) as stream:
                for event, element in ET.iterparse(stream, events=('start', 'end')):
                    if event == 'start':
                        parents.append(element)
                        continue
                    if element.tag == '{' + ns['m'] + '}row':
                        Converter.check(cancel)
                    if element.tag == '{' + ns['m'] + '}mergeCell':
                        merged.append(element.attrib['ref'])
                        if len(merged) > MAX_CELLS:
                            raise ConversionError('合并区域过多，请拆分工作表。')
                    element.clear()
                    if element.tag == '{' + ns['m'] + '}row' and len(parents) > 1:
                        parents[-2].remove(element)
                    parents.pop()
            result[sheet.attrib['name']] = merged
        return result


def workbook_data(source, cancel):
    from .core import Converter, ConversionError
    check_input(source)
    sheets = []
    cells = chars = 0
    def add_row(rows, values):
        nonlocal cells, chars
        Converter.check(cancel)
        cells += len(values)
        chars += sum(len(v) for v in values if isinstance(v, str))
        if len(rows) >= MAX_ROWS or len(values) > MAX_COLUMNS or cells > MAX_CELLS or chars > MAX_TEXT_CHARS:
            raise ConversionError('表格过大：最多 50 万单元格、每表 10 万行 / 2048 列、文本 1600 万字符，请拆分文件。')
        rows.append(values)
    if source.suffix.lower() == '.xls':
        import xlrd
        book = xlrd.open_workbook(str(source), on_demand=True)
        try:
            for sheet_index in range(book.nsheets):
                sheet = book.sheet_by_index(sheet_index)
                rows = []
                if sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLUMNS:
                    raise ConversionError('工作表行列过多，请拆分文件。')
                for i in range(sheet.nrows):
                    Converter.check(cancel)
                    values = []
                    for cell in sheet.row(i):
                        value = cell.value
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            value = xlrd.xldate_as_datetime(value, book.datemode)
                        elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            value = bool(value)
                        elif cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                            value = None
                        values.append(json_value(value))
                    add_row(rows, values)
                sheets.append({'name': sheet.name, 'rows': rows,
                    'merged_cells': [], 'hidden': bool(sheet.visibility)})
                book.unload_sheet(sheet_index)
        finally:
            book.release_resources()
    elif source.suffix.lower() in {'.csv', '.tsv'}:
        rows = []
        with io.StringIO(read_text(source)) as stream:
            for values in csv.reader(stream, delimiter='\t' if source.suffix.lower() == '.tsv' else ','):
                add_row(rows, values)
        sheets.append({'name': source.stem, 'rows': rows, 'merged_cells': [], 'hidden': False})
    else:
        from openpyxl import load_workbook
        merges = workbook_merges(source, cancel)
        book = load_workbook(source, read_only=True, data_only=False, keep_links=False)
        try:
            for sheet in book.worksheets:
                rows = []
                if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                    raise ConversionError('工作表声明的行列过多，请移除远端空白格式或拆分文件。')
                iterator = sheet.iter_rows(values_only=True)
                try:
                    for row in iterator:
                        add_row(rows, [json_value(value) for value in row])
                finally:
                    iterator.close()
                sheets.append({'name': sheet.title, 'rows': rows,
                    'merged_cells': merges.get(sheet.title, []),
                    'hidden': sheet.sheet_state != 'visible'})
        finally:
            book.close()
    return {'schema': 'markflow.workbook.v1', 'sheets': sheets}


def cell_text(value):
    from .core import escape
    return escape('' if value is None else str(value)).replace('|', '\\|').replace('\r\n', '\n').replace('\n', '<br>')


def sheets_markdown(data, cancel=None):
    from .core import markdown_table, escape, Converter, ConversionError
    parts = []
    cells = chars = 0
    for sheet in data['sheets']:
        Converter.check(cancel)
        parts.append('# ' + escape(sheet['name']))
        rows = sheet['rows']
        if len(rows) > MAX_ROWS:
            raise ConversionError('工作表行数过多，请拆分文件。')
        # Markdown tables pad every row to the widest row. Bound the padded
        # size too, so a wide header plus many short CSV rows cannot explode.
        if len(rows) * max((len(row) for row in rows), default=0) > MAX_CELLS:
            raise ConversionError('表格补齐空列后超过 50 万单元格，请拆分文件。')
        for row in rows:
            Converter.check(cancel)
            cells += len(row)
            chars += sum(len(str(v)) for v in row if v is not None)
            if len(row) > MAX_COLUMNS or cells > MAX_CELLS or chars > MAX_TEXT_CHARS:
                raise ConversionError('工作表超过扩展格式容量限制，请拆分文件。')
        if rows:
            parts.append(markdown_table([[cell_text(v) for v in row] for row in rows]))
    return '\n\n'.join(parts)


def word_data(source, cancel):
    from docx import Document
    from docx.table import Table
    from .core import Converter
    blocks = []
    for block in Document(source).iter_inner_content():
        Converter.check(cancel)
        if isinstance(block, Table):
            blocks.append({'type': 'table', 'rows': [[c.text for c in row.cells] for row in block.rows]})
        else:
            blocks.append({'type': 'paragraph', 'style': block.style.name if block.style else '',
                'text': block.text, 'runs': [{'text': r.text, 'bold': r.bold,
                'italic': r.italic, 'underline': bool(r.underline)} for r in block.runs]})
    return {'schema': 'markflow.document.v1', 'blocks': blocks}


def presentation_markdown(source, assets, warnings, cancel):
    from pptx import Presentation
    from .core import Converter, escape, markdown_table
    parts = []
    slides = Presentation(source).slides
    def shapes(items, title):
        result = []
        for shape in items:
            Converter.check(cancel)
            if shape.shape_type == 6:  # group
                result.extend(shapes(shape.shapes, title))
            elif shape.has_table:
                result.append(markdown_table([[cell_text(c.text) for c in row.cells] for row in shape.table.rows]))
            elif shape.shape_type == 13:  # picture
                result.append(assets.add(shape.image.blob, shape.image.ext))
            elif shape.has_text_frame and shape != title:
                for paragraph in shape.text_frame.paragraphs:
                    if paragraph.text.strip():
                        result.append('  ' * min(paragraph.level, 6) + '- ' + escape(paragraph.text))
        return result
    for index, slide in enumerate(slides, 1):
        Converter.check(cancel)
        title = slide.shapes.title
        parts.append('# ' + escape(title.text if title and title.text.strip() else f'幻灯片 {index}'))
        parts.extend(shapes(slide.shapes, title))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes and notes.text.strip():
                parts.append('## 演讲者备注\n\n' + escape(notes.text))
    warnings.append('PPTX 按幻灯片提取标题、文本、表格、位图和备注；图表、动画和原版式不保留。旧 .ppt 请先另存为 .pptx。')
    return '\n\n'.join(parts), len(slides)


def import_document(converter, source, assets, options, warnings, progress, cancel):
    from .core import Converter, ConversionError
    suffix = source.suffix.lower()
    check_input(source)
    if suffix in {'.md', '.markdown'}:
        from .word_layout import import_reference
        preserved = import_reference(source, assets, cancel)
        if preserved is not None:
            warnings.append('已载入 Word 原排版参考；导出 Word 时保留原有合并、行高、列宽和页面设置。')
            return preserved, 0, 0, None
    if suffix in {'.xlsx', '.xls', '.csv', '.tsv'}:
        progress('正在读取工作表')
        data = workbook_data(source, cancel)
        warnings.append('Excel 使用第一行为表头；公式保留为公式文本，不计算公式，合并区域不展开。')
        return sheets_markdown(data, cancel), 0, 0, data
    if suffix == '.pptx':
        text, pages = presentation_markdown(source, assets, warnings, cancel)
        return text, pages, 0, None
    if suffix in IMAGE_SUFFIXES:
        if not options.ocr:
            raise ConversionError('图片转 Markdown 需要开启“扫描页自动识别”。')
        from PIL import Image, ImageSequence
        parts = []
        index = 0
        with Image.open(source) as image:
            for index, frame in enumerate(ImageSequence.Iterator(image), 1):
                Converter.check(cancel)
                if frame.width * frame.height > MAX_IMAGE_PIXELS or index > 200:
                    raise ConversionError('图片超过 2400 万像素或 200 帧，请缩小或拆分后重试。')
                progress(f'正在识别第 {index} 张图片')
                with frame.convert('RGB') as pil, io.BytesIO() as buffer:
                    text = converter.ocr_image(pil, cancel)
                    parts.append(text)
                    pil.save(buffer, format='PNG')
                    parts.append(assets.add(buffer.getvalue(), 'png', f'第 {index} 张原图（请核对 OCR）'))
        warnings.append('图片 OCR 需核对错字、表格和公式；已保留原图。')
        return '\n\n'.join(parts), index, index, None
    if suffix == '.json':
        data = json.loads(read_text(source))
        if isinstance(data, dict) and data.get('schema') == 'markflow.workbook.v1':
            return sheets_markdown(data, cancel), 0, 0, data
        if isinstance(data, dict) and data.get('schema') == 'markflow.document.v1':
            from .core import escape, markdown_table
            parts = []
            for block in data['blocks']:
                Converter.check(cancel)
                if block['type'] == 'table':
                    parts.append(markdown_table([[cell_text(c) for c in r] for r in block['rows']]))
                else:
                    text = escape(block['text'])
                    heading = re.search(r'Heading ([1-6])', block.get('style', ''))
                    parts.append('#' * int(heading[1]) + ' ' + text if heading else text)
            return '\n\n'.join(parts), 0, 0, data
        if isinstance(data, dict) and isinstance(data.get('markdown'), str):
            return data['markdown'], 0, 0, None
        raise ConversionError('JSON 需使用 MarkFlow 的 document / workbook / markdown 格式。')
    if suffix in {'.tex', '.latex'}:
        warnings.append('LaTeX 保留可解析的标题、列表、表格和数学公式；不执行宏包或读取 include/input 外部文件。')
    return import_markup(source, assets, warnings, cancel), 0, 0, None


def export_document(text, destination, *, base, structured=None, cancel=None):
    """Create output atomically. All rendered images are local or embedded."""
    from .core import Converter, ConversionError
    import uuid
    destination = Path(destination)
    suffix = destination.suffix.lower()[1:]
    if suffix not in OUTPUT_SUFFIXES:
        raise ConversionError('不支持的输出格式')
    Converter.check(cancel)
    temporary = destination.with_name('.' + destination.stem + '-' + uuid.uuid4().hex + destination.suffix)
    try:
        if suffix == 'md':
            temporary.write_text(text, encoding='utf-8')
        elif suffix == 'docx' and _export_word_reference(text, temporary, base, cancel):
            pass
        elif suffix == 'json':
            data = structured or {'schema': 'markflow.markdown.v1', 'markdown': text,
                'document': json.loads(pandoc(text, MARKDOWN_READER, 'json', cancel=cancel))}
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        elif suffix == 'xlsx':
            export_workbook(text, temporary, structured, cancel)
        else:
            ast = expand_html_tables(json.loads(pandoc(text, MARKDOWN_READER, 'json', cancel=cancel)), cancel)
            for node in walk(ast):
                if node.get('t') in {'RawBlock', 'RawInline'}:
                    # Keep only sanitized HTML line breaks produced by our table adapter.
                    node['c'][1] = '<br>' if node['c'] == ['html', '<br>'] else ''
                if node.get('t') == 'Image':
                    target = node['c'][-1][0]
                    try:
                        path = local_image(target, Path(base))
                    except ConversionError:
                        node.clear()
                        node.update(t='Str', c='[图片未嵌入]')
                        continue
                    if suffix == 'tex':
                        # TeX needs a file path; a data URI is not a valid
                        # includegraphics filename. Keep the asset reference
                        # relative to the final file, even for Save As exports.
                        node['c'][-1][0] = os.path.relpath(path, destination.parent).replace('\\', '/')
                        continue
                    # data URIs let Pandoc's sandbox embed media without filesystem access.
                    import mimetypes
                    mime = mimetypes.guess_type(path.name)[0] or 'image/png'
                    node['c'][-1][0] = 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
            payload = json.dumps(ast)
            if suffix == 'pdf':
                html = pandoc(payload, 'json', 'html5', cancel=cancel)
                print_pdf(html, temporary)
            else:
                writer = {'html': 'html5', 'tex': 'latex'}.get(suffix, suffix)
                pandoc(payload, 'json', writer, output=temporary, cancel=cancel)
        Converter.check(cancel)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _export_word_reference(text, destination, base, cancel):
    from .word_layout import export_reference
    return export_reference(text, destination, base, cancel)


def export_workbook(text, destination, structured, cancel):
    from openpyxl import Workbook
    from .core import Converter, ConversionError
    if structured and structured.get('schema') == 'markflow.workbook.v1':
        sheets = structured['sheets']
    else:
        # Let Pandoc parse escaped pipes and inline formatting instead of splitting strings.
        from bs4 import BeautifulSoup
        html = pandoc(text, MARKDOWN_READER, 'html5', cancel=cancel)
        soup = BeautifulSoup(html, 'html.parser')
        try:
            sheets = [{'name': f'表格 {i}', 'rows': [[c.get_text('\n') for c in row.find_all(['td', 'th'], recursive=False)]
                      for row in table.find_all('tr')]} for i, table in enumerate(soup.find_all('table'), 1)]
        finally:
            soup.decompose()
    if not sheets:
        raise ConversionError('没有找到可导出的表格；Excel 导出需要 Markdown 表格或工作簿 JSON。')
    book = Workbook()
    try:
        book.remove(book.active)
        cells = chars = 0
        for i, sheet in enumerate(sheets, 1):
            Converter.check(cancel)
            name = re.sub(r'[\\/*?:\[\]]', '_', sheet['name'])[:31] or f'Sheet{i}'
            ws = book.create_sheet(name)
            if len(sheet['rows']) > MAX_ROWS:
                raise ConversionError('工作表行数过多，请拆分文件。')
            for row in sheet['rows']:
                Converter.check(cancel)
                cells += len(row)
                chars += sum(len(str(v)) for v in row if v is not None)
                if len(row) > MAX_COLUMNS or cells > MAX_CELLS or chars > MAX_TEXT_CHARS:
                    raise ConversionError('工作表超过扩展格式容量限制，请拆分文件。')
                ws.append(row)
                # Imported text must never become a new active spreadsheet formula.
                for cell in ws[ws.max_row]:
                    if cell.data_type == 'f':
                        cell.data_type = 's'
            for merged in sheet.get('merged_cells', []):
                from openpyxl.utils.cell import range_boundaries
                left, top, right, bottom = range_boundaries(merged)
                area = (right - left + 1) * (bottom - top + 1)
                cells += area
                if bottom > MAX_ROWS or right > MAX_COLUMNS or cells > MAX_CELLS:
                    raise ConversionError('合并区域超过扩展格式容量限制，请缩小区域。')
                ws.merge_cells(merged)
            ws.freeze_panes = 'A2'
        book.save(destination)
    finally:
        book.close()


def print_pdf(html, destination):
    """Qt's local rich-text print engine; no browser, scripts or TeX required."""
    from PySide6.QtGui import QTextDocument, QPdfWriter, QPageSize, QFont, QImage
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QByteArray, QUrl
    from .core import ConversionError
    # Called only by the conversion/export child in the desktop application.
    app = QApplication.instance() or QApplication(['markflow-pdf', '-platform', 'offscreen'])
    class LocalDocument(QTextDocument):
        def loadResource(self, kind, url):
            value = url.toString()
            if value.startswith('data:image/') and ';base64,' in value:
                image = QImage()
                image.loadFromData(QByteArray(base64.b64decode(value.split(',', 1)[1])))
                return image
            return QByteArray()
    document = LocalDocument()
    writer = None
    try:
        document.setDefaultFont(QFont('Microsoft YaHei', 10))
        document.setDefaultStyleSheet('body { font-size: 10pt; } table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 4px; } pre { white-space: pre-wrap; } img { max-width: 600px; }')
        document.setHtml(html)
        writer = QPdfWriter(str(destination))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setResolution(96)
        document.print_(writer)
    finally:
        del writer
        del document
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ConversionError('PDF 写入失败，请检查输出目录。')
