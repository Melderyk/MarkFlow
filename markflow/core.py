from __future__ import annotations

import html
import io
import os
import re
import shutil
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote

import pdfplumber
import pypdfium2 as pdfium
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image, ImageOps

from .pdf_layout import Layout, code_boxes, code_fence, contains, fenced_code, group_lines, is_scanned, join_page_code, monospace, normalize, comparison
from .ocr_layout import ocr_markdown


PDFIUM_LOCK = threading.RLock()


class ConversionError(Exception):
    pass


class Cancelled(ConversionError):
    pass


@dataclass
class Options:
    output_dir: Path
    profile: str = "GitHub / 通用"
    ocr: bool = True
    image_mode: str = 'local'
    host: object | None = None
    split_chapters: bool = True
    output_format: str = "md"


@dataclass
class Result:
    source: Path
    output: Path
    markdown: str
    assets_dir: Path | None = None
    pages: int = 0
    ocr_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    remote_images: dict[str,str] = field(default_factory=dict)
    chapters_dir: Path | None = None
    export: Path | None = None
    embedded_images: int = 0


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:100]
    if not value:
        value = "document"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", value):
        value = "_" + value
    return value


def unique_stem(folder: Path, stem: str, output_format: str = 'md') -> str:
    base = safe_name(stem)
    candidate = base
    index = 2
    while ((folder / f"{candidate}.md").exists() or (folder / f"{candidate}.assets").exists()
           or (folder / f"{candidate}.{output_format}").exists()):
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", text)


def image_link(alt: str, target: str, profile: str) -> str:
    # Standard links also work in Obsidian and keep merged documents portable.
    return f"![{escape(alt)}]({quote(target, safe='/.:_-')})"


class Assets:
    def __init__(self, folder: Path, stem: str, profile: str, image_mode: str = 'local'):
        self.folder = folder / f"{stem}.assets"
        self.relative = self.folder.name
        self.profile = profile
        self.count = 0
        self.cache: dict[str, str] = {}
        self.image_mode = image_mode
        self.embedded_bytes = 0

    def add(self, data: bytes, extension: str, alt: str = "图片", key: str = "") -> str:
        if key and key in self.cache:
            if self.image_mode == 'embed':
                return f'![{escape(alt)}]({self.cache[key]})'
            return image_link(alt, self.cache[key], self.profile)
        if self.image_mode == 'embed':
            from .embedded_images import MAX_TOTAL_BYTES, image_data_url
            target, size = image_data_url(data)
            if self.embedded_bytes + size > MAX_TOTAL_BYTES:
                raise ConversionError('嵌入图片合计超过 16 MB，请拆分文档或选择本地图片模式。')
            self.embedded_bytes += size
            self.count += 1
            if key:
                self.cache[key] = target
            return f'![{escape(alt)}]({target})'
        self.folder.mkdir(exist_ok=True)
        self.count += 1
        extension = re.sub(r"[^a-z0-9]", "", extension.lower()) or "bin"
        filename = f"image_{self.count:03d}.{extension}"
        (self.folder / filename).write_bytes(data)
        target = f"{self.relative}/{filename}"
        if key:
            self.cache[key] = target
        return image_link(alt, target, self.profile)


class Converter:
    def __init__(self):
        self._ocr_engine = None

    @staticmethod
    def check(cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled("已取消转换")

    def convert(self, source: Path, options: Options,
                progress: Callable[[str], None] = lambda _: None,
                cancel: threading.Event | None = None) -> Result:
        source = Path(source).resolve()
        if not source.is_file():
            raise ConversionError("文件不存在或已被移动")
        from .formats import INPUT_SUFFIXES, OUTPUT_SUFFIXES, check_input
        if source.suffix.lower() not in INPUT_SUFFIXES:
            raise ConversionError("不支持此输入格式")
        if options.output_format not in OUTPUT_SUFFIXES:
            raise ConversionError("不支持的输出格式")
        if options.image_mode not in {'local', 'host', 'embed'}:
            raise ConversionError('不支持的图片模式')
        check_input(source)
        options.output_dir = Path(options.output_dir).resolve()
        options.output_dir.mkdir(parents=True, exist_ok=True)
        stem = unique_stem(options.output_dir, source.stem, options.output_format)
        staging = options.output_dir / f".markflow-{uuid.uuid4().hex}"
        staging.mkdir()
        assets = Assets(staging, stem, options.profile, options.image_mode)
        warnings: list[str] = []
        try:
            self.check(cancel)
            structured = None
            if source.suffix.lower() == ".docx":
                text = self._docx(source, assets, warnings, cancel)
                pages, ocr_pages = 0, 0
                if options.output_format == 'json':
                    from .formats import word_data
                    structured = word_data(source, cancel)
            elif source.suffix.lower() == ".pdf":
                text, pages, ocr_pages = self._pdf(source, assets, options, warnings, progress, cancel)
            else:
                from .formats import import_document
                text, pages, ocr_pages, structured = import_document(
                    self, source, assets, options, warnings, progress, cancel)
            self.check(cancel)
            if not text.strip():
                warnings.append("未提取到正文，请在原文预览中检查内容。")
            text = text.strip() + "\n"
            if options.image_mode == 'embed':
                from .embedded_images import MAX_DOCUMENT_BYTES, typora_size_warning
                markdown_bytes = len(text.encode('utf-8'))
                if markdown_bytes > MAX_DOCUMENT_BYTES:
                    raise ConversionError('嵌入后的 Markdown 超过 32 MB，请拆分文档或选择本地图片模式。')
                compatibility = typora_size_warning(markdown_bytes)
                if compatibility:
                    warnings.insert(0, compatibility)
                if assets.count:
                    warnings.append('图片已嵌入 Markdown（Base64）；文件会增大，部分阅读器或网站可能不显示。')
                if assets.folder.exists():
                    warnings.append('复杂 Word 的原排版参考仍保存在 .assets 中；需原排版恢复时请一起保留。')
            (staging / f"{stem}.md").write_text(text, encoding="utf-8", newline="\n")
            staged_export = None
            if options.output_format != "md":
                from .formats import export_document
                staged_export = staging / f"{stem}.{options.output_format}"
                export_document(text, staged_export, base=staging,
                                structured=structured, cancel=cancel)
            self.check(cancel)
            final_assets = None
            if assets.folder.exists():
                final_assets = options.output_dir / assets.folder.name
                assets.folder.rename(final_assets)
            destination = options.output_dir / f"{stem}.md"
            committed_markdown = False
            try:
                (staging / f"{stem}.md").rename(destination)
                committed_markdown = True
                if staged_export is not None:
                    staged_export.rename(options.output_dir / staged_export.name)
            except Exception:
                if committed_markdown:
                    destination.unlink(missing_ok=True)
                if final_assets:
                    shutil.rmtree(final_assets)
                raise
            result=Result(source, destination, text, final_assets, pages, ocr_pages, warnings)
            result.embedded_images = assets.count if options.image_mode == 'embed' else 0
            if staged_export is not None:
                result.export = options.output_dir / staged_export.name
            if options.image_mode=='host' and final_assets:
                from .image_host import ImageUploader, upload_images
                try:
                    if options.host is None:
                        raise ValueError('尚未配置图床')
                    uploader=ImageUploader(options.host,options.output_dir/'.markflow-upload-cache.json')
                    hosted,mapping,upload_warnings=upload_images(text,options.output_dir,uploader,progress,cancel)
                    result.remote_images=mapping
                    warnings.extend(upload_warnings)
                    if mapping:
                        atomic_write(destination,hosted)
                        result.markdown=hosted
                        atomic_write(destination.with_suffix('.images.json'),__import__('json').dumps({
                            'provider':options.host.provider,'images':mapping,
                            'note':'本地 assets 是备份；此记录不含密钥。'
                        },ensure_ascii=False,indent=2))
                except Cancelled:
                    warnings.append('图片上传已停止，完整文档与本地图片已保留。')
                except Exception:
                    warnings.append('图床配置或上传失败，已保留本地图片与完整文档，请检查图床设置。')
            if options.split_chapters and options.image_mode != 'embed':
                from .markdown_tools import export_chapters
                try:
                    result.chapters_dir=export_chapters(result.markdown,destination)
                except OSError:
                    warnings.append('分章阅读版生成失败，完整 Markdown 已保存。')
            return result
        except Cancelled:
            raise
        except ConversionError:
            raise
        except PermissionError as exc:
            raise ConversionError("无法读写文件，请检查文件占用和输出目录权限") from exc
        except Exception as exc:
            raise ConversionError(f"转换失败：{type(exc).__name__}: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _docx(self, source: Path, assets: Assets, warnings: list[str], cancel) -> str:
        doc = Document(source)
        blocks = []
        code = []
        preserve_layout = False
        layout_positions = None
        def flush_code():
            if code:
                blocks.append(fenced_code("\n".join(code)))
                code.clear()
        for block in doc.iter_inner_content():
            self.check(cancel)
            if isinstance(block, Paragraph):
                style = block.style
                code_style = False
                visited = set()
                while style is not None and style.style_id not in visited:
                    visited.add(style.style_id)
                    if re.search(r"^(?:source\s*code|code(?:\s*block)?|代码|源代码)$",style.name,re.I):
                        code_style = True
                    style = style.base_style
                if code_style:
                    code.append(block.text.replace('\u00a0',' '))
                    continue
                flush_code()
                text = self._paragraph(block, doc, assets, warnings)
                if text.strip():
                    blocks.append(text)
            elif isinstance(block, Table):
                flush_code()
                from .word_layout import complex_table, table_html, text_positions
                if complex_table(block):
                    if layout_positions is None:
                        layout_positions = text_positions(doc, cancel)
                    blocks.append(table_html(block, doc, assets, cancel, layout_positions))
                    preserve_layout = True
                    continue
                rows = []
                for row in block.rows:
                    cells = []
                    for cell in row.cells:
                        content = "<br>".join(self._paragraph(p, doc, assets, warnings, table=True)
                                              for p in cell.paragraphs).strip()
                        cells.append(content.replace("|", "\\|").replace("\n", "<br>"))
                    rows.append(cells)
                if rows:
                    blocks.append(markdown_table(rows))
        flush_code()
        if not blocks:
            warnings.append("正文为空；文本框、批注和修订标记不属于第一版提取范围。")
        text = "\n\n".join(blocks)
        if preserve_layout:
            from .word_layout import save_reference
            text = save_reference(source, text, assets)
            warnings.append('检测到复杂合并表格，已保留 HTML 合并结构及 Word 原排版参考；'
                            '往返转换请同时保留 Markdown 和 .assets 文件夹。')
        return text

    def _paragraph(self, p: Paragraph, doc, assets: Assets, warnings: list[str], table=False) -> str:
        def inline(node) -> str:
            tag = node.tag.split("}")[-1]
            if tag == "hyperlink":
                label = "".join(inline(child) for child in node)
                rid = node.get(qn("r:id"))
                rel = doc.part.rels.get(rid) if rid else None
                anchor = node.get(qn("w:anchor"))
                target = str(rel.target_ref) if rel else (f"#{anchor}" if anchor else "")
                return f"[{label}]({quote(target, safe='/:#?=&%+@_-')})" if target else label
            if tag != "r":
                return ""
            chunks = []
            for child in node:
                name = child.tag.split("}")[-1]
                if name == "t":
                    chunks.append(escape(child.text or ""))
                elif name == "tab":
                    chunks.append("    ")
                elif name in {"br", "cr"}:
                    chunks.append("<br>" if table else "  \n")
                elif name in {"drawing", "pict"}:
                    embeds = child.xpath('.//*[@r:embed]')
                    for element in embeds:
                        rid = element.get(qn("r:embed"))
                        part = doc.part.related_parts.get(rid)
                        if part:
                            ext = Path(str(part.partname)).suffix.lstrip(".")
                            if ext in {"emf", "wmf"}:
                                if "包含 EMF/WMF 图片，部分 Markdown 阅读器无法显示。" not in warnings:
                                    warnings.append("包含 EMF/WMF 图片，部分 Markdown 阅读器无法显示。")
                            chunks.append(assets.add(part.blob, ext, key=rid))
            value = "".join(chunks)
            props = node.find(qn("w:rPr"))
            if props is not None and value.strip() and "![" not in value:
                bold = props.find(qn("w:b"))
                italic = props.find(qn("w:i"))
                if bold is not None and bold.get(qn("w:val"), "1") not in {"0", "false", "off"}:
                    value = f"**{value.strip()}**" + (" " if value.endswith(" ") else "")
                if italic is not None and italic.get(qn("w:val"), "1") not in {"0", "false", "off"}:
                    value = f"*{value.strip()}*" + (" " if value.endswith(" ") else "")
            return value

        text = "".join(inline(node) for node in p._p).strip()
        if not text or table:
            return text
        style_id = p.style.style_id if p.style else ""
        style_name = p.style.name if p.style else ""
        heading = re.search(r"(?:Heading|标题)\s*([1-6])", style_id + " " + style_name, re.I)
        if heading:
            return "#" * int(heading.group(1)) + " " + text
        if style_id.lower() in {"title", "subtitle"}:
            return ("# " if style_id.lower() == "title" else "## ") + text
        ppr = p._p.pPr
        numpr = ppr.numPr if ppr is not None else None
        style = p.style
        visited = set()
        while numpr is None and style is not None and style.style_id not in visited:
            visited.add(style.style_id)
            spr = style.element.pPr
            numpr = spr.numPr if spr is not None else None
            style = style.base_style
        if numpr is not None:
            level = int(numpr.ilvl.val) if numpr.ilvl is not None else 0
            fmt = "bullet"
            if numpr.numId is not None:
                numbering = doc.part.numbering_part.element
                nums = numbering.xpath(f'./w:num[@w:numId="{numpr.numId.val}"]/w:abstractNumId')
                if nums:
                    aid = nums[0].get(qn("w:val"))
                    formats = numbering.xpath(f'./w:abstractNum[@w:abstractNumId="{aid}"]/w:lvl[@w:ilvl="{level}"]/w:numFmt')
                    if formats:
                        fmt = formats[0].get(qn("w:val"))
            return "    " * min(level, 8) + ("- " if fmt == "bullet" else "1. ") + text
        if "quote" in style_id.lower():
            return "> " + text
        return text

    def _pdf(self, source: Path, assets: Assets, options: Options, warnings, progress, cancel):
        blocks = []
        ocr_count = 0
        try:
            pdf = pdfplumber.open(source)
        except Exception as exc:
            raise ConversionError("PDF 无法打开，可能已损坏或需要密码") from exc
        renderer = None
        try:
            pages = len(pdf.pages)
            progress("读取 PDF 书签与文档字体")
            layout = Layout(source, pdf, warnings)
            with PDFIUM_LOCK:
                renderer = pdfium.PdfDocument(str(source))
            previous_tail = False
            for index, page in enumerate(pdf.pages):
                self.check(cancel)
                progress(f"第 {index + 1}/{pages} 页 · 分析版面")
                raw = page.extract_text() or ""
                scanned = is_scanned(page, raw, layout.has_outline)
                if scanned and options.ocr:
                    progress(f"第 {index + 1}/{pages} 页 · 本地 OCR 识别")
                    with closing(render_page(renderer, index, scale=2.2)) as pil:
                        text = self.ocr_image(pil, cancel)
                        ocr_count += 1
                        if not text:
                            warnings.append(f"第 {index + 1} 页 OCR 未识别出文字。")
                        elif re.search(r"(?m)^`{3,}(?:c|asm|bash|powershell)\s*$",text):
                            with io.BytesIO() as buffer:
                                pil.save(buffer,format="PNG")
                                reference=assets.add(buffer.getvalue(),"png",f"第 {index+1} 页 OCR 代码原图（核对符号）")
                            text += "\n\n"+reference
                            warnings.append(f"第 {index+1} 页包含 OCR 代码：括号、引号、0/O 和缩进需核对，已附原页图。")
                    blocks.append(text)
                    previous_tail = False
                else:
                    if scanned:
                        warnings.append(f"第 {index + 1} 页疑似扫描件，OCR 已关闭。")
                    text = self._pdf_page(page, renderer, index, assets, warnings, layout)
                    if previous_tail and layout.page_head_code and blocks:
                        blocks[-1],text = join_page_code(blocks[-1],text)
                    if text.strip():
                        blocks.append(text)
                    previous_tail = layout.page_tail_code
                page.close()
            layout.report(warnings)
            return "\n\n".join(part for part in blocks if part.strip()), pages, ocr_count
        finally:
            pdf.close()
            if renderer is not None:
                with PDFIUM_LOCK:
                    renderer.close()

    def _pdf_page(self, page, renderer, index, assets, warnings, layout):
        panels = code_boxes(page)
        noncode_lines = group_lines([c for c in page.chars if not any(contains(b,c) for b in panels)])
        def context_before(top):
            before=[l for l in noncode_lines if l.bottom<=top+2 and l.text.strip()]
            return before[-1].text if before else ''
        events = []
        code_bounds = {}
        good_tables = []
        # Filled HTML page backgrounds and code shadows are not table rules.
        ruled = page.filter(lambda obj: not (obj.get("object_type")=="rect"
                            and obj.get("fill") and not obj.get("stroke")))
        for table in ruled.find_tables():
            # Code panels can contain box drawings; never flatten their text
            # into a guessed table before code recognition gets to see it.
            if any(b[0] <= table.bbox[0]+1 and b[1] <= table.bbox[1]+1
                   and b[2] >= table.bbox[2]-1 and b[3] >= table.bbox[3]-1 for b in panels):
                continue
            rows = table.extract()
            if rows and len(rows)>1 and max(len(r) for r in rows)>1:
                cleaned = [[escape(normalize(str(cell or ""))).replace("|", "\\|").replace("\n", "<br>")
                            for cell in row] for row in rows]
                events.append((table.bbox[1], table.bbox[0], "block", markdown_table(cleaned)))
                good_tables.append(table)
        for box in panels:
            lines = group_lines([c for c in page.chars if contains(box,c)])
            if lines:
                events.append((box[1], box[0], "block", code_fence(lines,context=context_before(box[1]))))
                code_bounds[(box[1],box[0])] = box[3]
        chars = [c for c in page.chars if not any(contains(b,c) for b in panels)
                 and not any(contains(t.bbox,c) for t in good_tables)]
        lines = group_lines(chars)
        headings, consumed = layout.match_headings(index, lines)
        fallback = []
        def flush_code():
            if fallback:
                events.append((fallback[0].top, fallback[0].x0, "block", code_fence(fallback,context=context_before(fallback[0].top))))
                code_bounds[(fallback[0].top,fallback[0].x0)] = fallback[-1].bottom
                fallback.clear()
        for i,line in enumerate(lines):
            if i in headings:
                flush_code()
                heading = headings[i]
                events.append((line.top,line.x0,"block","#"*heading.level+" "+escape(heading.title)))
                continue
            if i in consumed:
                continue
            if (line.top<32 or line.bottom>page.height-40) and comparison(line.text) in layout.margin_text:
                continue
            meaningful = [c for c in line.chars if c["text"].strip()]
            is_code = meaningful and sum(monospace(c) for c in meaningful)/len(meaningful) >= .55
            if is_code:
                if fallback and line.top-fallback[-1].top > line.size*3:
                    flush_code()
                fallback.append(line)
                continue
            flush_code()
            level = layout.inferred_level(line)
            text = "#"*level+" "+escape(line.text) if level else self._pdf_inline(line,page)
            text = re.sub(r"^[•●▪◦]\s*", "- ", text)
            if not text.strip():
                continue
            events.append((line.top,line.x0,"block" if level else "line",text))
        flush_code()
        raster = None
        try:
            for pic in page.images:
                width, height = pic["x1"] - pic["x0"], pic["bottom"] - pic["top"]
                if width < 24 or height < 24:
                    continue
                if raster is None:
                    raster = render_page(renderer, index, scale=2)
                sx, sy = raster.width / page.width, raster.height / page.height
                box = (max(0, int(pic["x0"] * sx)), max(0, int(pic["top"] * sy)),
                       min(raster.width, int(pic["x1"] * sx)), min(raster.height, int(pic["bottom"] * sy)))
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                with closing(raster.crop(box)) as cropped, io.BytesIO() as buffer:
                    cropped.save(buffer, format="PNG")
                    link = assets.add(buffer.getvalue(), "png", f"第 {index + 1} 页图片")
                events.append((pic["top"], pic["x0"], "block", link))
        finally:
            if raster is not None:
                raster.close()
        parts = []
        paragraph = []
        previous_y = None
        def flush():
            if paragraph:
                # Chinese PDF line wraps do not imply spaces within a word.
                joined = paragraph[0]
                for value in paragraph[1:]:
                    separator = "" if re.search(r"[\u3400-\u9fff]$",joined) and re.match(r"[\u3400-\u9fff]",value) else " "
                    joined += separator+value
                parts.append(joined)
                paragraph.clear()
        for top, x, kind, text in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == "block" or text.startswith(("#", "- ", "> ")) or re.match(r"^\d+[.)] ", text):
                flush()
                parts.append(text)
                previous_y = None
            else:
                if previous_y is not None and top - previous_y > layout.base * 1.85:
                    flush()
                paragraph.append(text)
                previous_y = top
        flush()
        ordered = sorted(events,key=lambda e:(e[0],e[1]))
        layout.page_head_code = bool(ordered and ordered[0][:2] in code_bounds and ordered[0][0]<40)
        layout.page_tail_code = bool(ordered and code_bounds.get(ordered[-1][:2],0)>page.height-60)
        return "\n\n".join(parts)

    @staticmethod
    def _pdf_inline(line, page):
        runs = []
        previous = None
        for char in line.chars:
            font = char.get("fontname", "").lower()
            link = next((h.get("uri") for h in page.hyperlinks if h.get("uri")
                         and contains((h["x0"],h["top"],h["x1"],h["bottom"]),char)),None)
            key = ("bold" in font or "black" in font, "italic" in font or "oblique" in font,monospace(char),link)
            text = normalize(char["text"])
            gap = char["x0"]-previous["x1"] if previous else 0
            if previous and gap > min(char["size"],previous["size"])*.18 and not text.startswith(" ") and not runs[-1][1].endswith(" "):
                runs[-1][1] += " "
            if runs and runs[-1][0]==key:
                runs[-1][1] += text
            else:
                runs.append([key,text])
            previous = char
        output = []
        for (bold,italic,mono,link),text in runs:
            leading = text[:len(text)-len(text.lstrip())]
            trailing = text[len(text.rstrip()):]
            value = text.strip()
            if not value:
                output.append(text)
                continue
            if mono:
                fence = "`"*max(1,max((len(x)+1 for x in re.findall(r"`+",value)),default=1))
                value = fence+" "+value+" "+fence
            else:
                value = escape(value)
                if bold:
                    value = "**"+value+"**"
                if italic:
                    value = "*"+value+"*"
            if link:
                value = f"[{value}]({quote(link,safe='/:#?=&%+@_-')})"
            output.append(leading+value+trailing)
        text = "".join(output).strip()
        return re.sub(r"^(#{1,6})(?=\s)",r"\\\1",text)

    def ocr_image(self, pil: Image.Image, cancel=None) -> str:
        self.check(cancel)
        if self._ocr_engine is None:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
        import numpy as np
        array = np.asarray(ImageOps.exif_transpose(pil).convert("RGB"))[:, :, ::-1].copy()
        result, _ = self._ocr_engine(array)
        self.check(cancel)
        if not result:
            return ""
        return ocr_markdown(pil,result,escape)


def markdown_table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    result = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    result.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(result)


def render_page(document, index: int, scale: float = 1.3) -> Image.Image:
    with PDFIUM_LOCK:
        return _render_page(document, index, scale)


def _render_page(document, index: int, scale: float = 1.3) -> Image.Image:
    page = document[index]
    bitmap = None
    try:
        # Cap large format scans to avoid exhausting memory.
        width, height = page.get_size()
        scale = min(scale, 4500 / max(width, height))
        bitmap = page.render(scale=scale)
        with bitmap.to_pil() as image:
            return image.convert("RGB")
    finally:
        if bitmap is not None:
            bitmap.close()
        page.close()


def pdf_preview(source: Path, index: int = 0) -> tuple[Image.Image, int]:
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(str(source))
        try:
            return _render_page(document, index, 1.4), len(document)
        finally:
            document.close()


def merge_results(results: list[Result], output_dir: Path, profile: str,
                  markdowns: dict[str, str] | None = None) -> Path:
    if not results:
        raise ConversionError("请先成功转换至少一个文件")
    stem = unique_stem(output_dir, "合并文档")
    destination = output_dir / f"{stem}.md"
    # Relative assets remain valid even when source results are from another directory.
    lines = ["# 合并文档", "", "## 目录", ""]
    for index, result in enumerate(results, 1):
        if profile == "Obsidian":
            lines.append(f"- [[#文档 {index}：{result.source.stem}]]")
        elif profile == "Typora":
            if index == 1:
                lines.append("[TOC]")
        else:
            lines.append(f"- [{escape(result.source.stem)}](#document-{index})")
    for index, result in enumerate(results, 1):
        markdown = (markdowns or {}).get(str(result.output), result.markdown)
        source_parent = result.output.parent
        def rewrite(match):
            target = unquote(match.group(2))
            if "://" in target or target.startswith("data:"):
                return match.group(0)
            absolute = (source_parent / target).resolve()
            relative = os.path.relpath(absolute, output_dir).replace("\\", "/")
            return f"![{match.group(1)}]({quote(relative, safe='/.:_-')})"
        markdown = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", rewrite, markdown)
        if profile not in {"Obsidian", "Typora"}:
            lines.extend(["", f'<a id="document-{index}"></a>'])
        lines.extend(["", f"# 文档 {index}：{escape(result.source.stem)}", "", markdown.strip(), "", "---"])
    atomic_write(destination, "\n".join(lines).rstrip() + "\n")
    return destination


def docx_preview_html(source: Path) -> str:
    """Semantic source preview; does not claim to reproduce Word pagination."""
    from .formats import check_input
    check_input(source)
    doc = Document(source)
    def para(p):
        chunks = []
        for item in p.iter_inner_content():
            # python-docx exposes hyperlinks separately from ordinary runs.
            if hasattr(item, "url"):
                chunks.append(f'<a href="{html.escape(item.url, quote=True)}">{html.escape(item.text)}</a>')
                continue
            run = item
            value = html.escape(run.text).replace("\n", "<br>")
            if run.bold:
                value = f"<b>{value}</b>"
            if run.italic:
                value = f"<i>{value}</i>"
            chunks.append(value)
            for blip in run._r.xpath('.//a:blip'):
                rid = blip.get(qn("r:embed"))
                part = doc.part.related_parts.get(rid)
                if part:
                    import base64
                    data = base64.b64encode(part.blob).decode("ascii")
                    chunks.append(f'<img width="420" src="data:{part.content_type};base64,{data}">')
        if not chunks or not "".join(chunks).strip():
            return html.escape(p.text)
        return "".join(chunks)
    blocks = []
    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            sid = block.style.style_id if block.style else ""
            match = re.search(r"Heading([1-6])", sid, re.I)
            tag = f"h{match.group(1)}" if match else ("h1" if sid == "Title" else "p")
            blocks.append(f"<{tag}>{para(block)}</{tag}>")
        else:
            rows = ["<tr>" + "".join("<td>" + "<br>".join(para(p) for p in c.paragraphs) + "</td>" for c in row.cells) + "</tr>" for row in block.rows]
            blocks.append('<table border="1" cellpadding="7" cellspacing="0">' + "".join(rows) + "</table>")
    return '<html><head><style>body{font-family:Microsoft YaHei;font-size:14px;color:#253348;}p{line-height:1.7}table{border-collapse:collapse;border-color:#ccd5df}</style></head><body>' + "".join(blocks) + "</body></html>"
