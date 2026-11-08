"""Regressions for mixed fonts, real bookmark levels and code-heavy pages."""
from pathlib import Path
import re

import pytest
from reportlab.pdfgen import canvas
from PIL import Image

from markflow.core import Converter, Options
from markflow.pdf_layout import code_fence, comparison, group_lines, is_scanned, join_page_code, normalize


def glyph(text,x,top,font="OpenSans-Bold",size=12.2):
    return dict(text=text,x0=x,x1=x+7,top=top,bottom=top+size,size=size,fontname=font)


def test_mixed_cjk_latin_ascenders_stay_on_one_line():
    chars=[glyph('4.',38,103.57),glyph('安装',52,100,'Noto-Sans-SC-Bold'),
           glyph('Mingw64',77,103.57),glyph('编译器',134,100,'Noto-Sans-SC-Bold'),
           glyph('next',38,118)]
    rows=group_lines(chars)
    assert len(rows)==2
    assert comparison(rows[0].text)=='4.安装Mingw64编译器'
    assert normalize('语⾔、⻔、对⻬')=='语言、门、对齐'


def test_code_reconstruction_keeps_indentation_blank_lines_and_literals():
    chars=[]
    for y,text in [(10,'int main() {'),(24,'    printf("C:\\test\\n");'),(52,'}')]:
        for i,c in enumerate(text):
            char=glyph(c,40+i*6,y,'Courier',10)
            char['x1']=char['x0']+6
            chars.append(char)
    output=code_fence(group_lines(chars))
    assert output.startswith('```c\nint main() {\n    printf("C:\\test\\n");\n\n}')
    assert '\\<' not in output


def test_code_continued_at_pdf_page_boundary_keeps_one_fence():
    previous='# Chapter\n\n```c\nint main() {\n    int n = 2;\n```'
    current='```text\n    return n;\n}\n```\n\n## Next section'
    left,right=join_page_code(previous,current)
    assert left=='# Chapter\n\n```c\nint main() {\n    int n = 2;\n    return n;\n}\n```'
    assert right=='## Next section'
    assert join_page_code('ordinary paragraph',current)==('ordinary paragraph',current)


def test_docx_code_style_preserves_literal_lines(tmp_path):
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    doc=Document()
    style=doc.styles.add_style('Source Code',WD_STYLE_TYPE.PARAGRAPH)
    doc.add_heading('Example',1)
    for text in ['#include <stdio.h>','int main() {','    printf("hello\\n");','}']:
        doc.add_paragraph(text,style=style)
    doc.add_paragraph('Explanation follows.')
    source=tmp_path/'code.docx'
    doc.save(source)
    result=Converter().convert(source,Options(tmp_path/'out'))
    assert '```c\n#include <stdio.h>\nint main() {\n    printf("hello\\n");\n}\n```' in result.markdown
    assert '\\<' not in result.markdown
    assert '\n\nExplanation follows.' in result.markdown


@pytest.fixture
def structured_pdf(tmp_path):
    source=tmp_path/'layout.pdf'
    c=canvas.Canvas(str(source),pagesize=(600,800))
    # Background rectangles used by browser PDF export must not form a table.
    c.setFillColorRGB(1,1,1)
    c.rect(0,0,600,800,stroke=0,fill=1)
    for level,title,y in [(0,'Chapter',750),(1,'Section',710),(2,'Install compiler',670),(3,'Details',630)]:
        c.bookmarkHorizontalAbsolute(str(level),y)
        c.addOutlineEntry(title,str(level),level=level,closed=False)
        c.setFillColorRGB(0,0,0)
        c.setFont('Helvetica-Bold',16 if level==0 else 12)
        c.drawString(40,y,title)
    c.setFont('Helvetica',12)
    c.drawString(40,596,'Ordinary prose on a code-heavy page.')
    c.setFillColorRGB(.97,.97,.97)
    c.rect(40,340,520,230,stroke=0,fill=1)
    c.setFillColorRGB(0,0,0)
    c.setFont('Courier',9)
    code=['#include <stdio.h>','int main() {','    int n = 0;',
          '',r'    printf("C:\test\n");','    return n;','}']
    for i,line in enumerate(code):
        c.drawString(46,550-i*14,line)
    # Unboxed monospace fallback must still preserve literal operators.
    c.drawString(46,300,'-> choose "x86_64-posix-seh"')
    c.drawString(46,286,r'-> open D:\Software\mingw64\bin')
    c.showPage()
    # Sparse embedded screenshots are not a full-page scanned document.
    image=tmp_path/'screen.png'
    Image.new('RGB',(500,400),'#cce0ff').save(image)
    c.drawImage(str(image),40,350,520,416)
    c.setFont('Helvetica',9)
    c.drawString(40,20,'Repeated tutorial footer')
    c.save()
    return source


def test_bookmarks_code_panels_and_screenshot_page(structured_pdf,tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('Embedded screenshots must not trigger OCR')
    converter=Converter()
    monkeypatch.setattr(converter,'ocr_image',forbidden)
    result=converter.convert(structured_pdf,Options(tmp_path/'out'))
    text=result.markdown
    assert re.findall(r'^#{1,6} .+$',text,re.M)==['# Chapter','## Section','### Install compiler','#### Details']
    assert 'Ordinary prose on a code-heavy page.' in text
    assert '```c\n#include <stdio.h>\nint main() {\n    int n = 0;\n\n' in text
    assert r'    printf("C:\test\n");' in text
    assert '```text\n-> choose "x86_64-posix-seh"\n-> open D:\\Software\\mingw64\\bin\n```' in text
    assert result.pages==2 and result.ocr_pages==0
    assert not result.warnings
    assert '![第 2 页图片]' in text
    assert '| --- |' not in text


def test_scan_detection_uses_largest_image_not_summed_screenshots():
    class Page:
        width=600
        height=800
        images=[dict(x0=40,x1=560,top=20,bottom=420),dict(x0=40,x1=560,top=430,bottom=750)]
    assert not is_scanned(Page(),'footer',True)
    Page.images=[dict(x0=0,x1=600,top=0,bottom=800)]
    assert is_scanned(Page(),'footer',True)
