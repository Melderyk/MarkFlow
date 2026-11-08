import pytest
from PIL import Image,ImageDraw

from markflow.code_languages import detect_language
from markflow.core import escape
from markflow.ocr_layout import ocr_markdown


@pytest.mark.parametrize('text,language',[
    ('if (0.5)\n    x=12;\nelse\n    x=18;','c'),
    ('int x, y;','c'),
    ('char *mystrcat(char *dest, const char *src)\n{\n}\n','c'),
    ('int (*func[10])(int);','c'),
    ('typedef unsigned long uintptr_t;','c'),
    ('typedef struct {\n    uint32_t value;\n} register_t;','c'),
    ('struct 结构体名 {\n    成员列表;\n};','c'),
    ('#if 常量表达式\n 程序正文\n#endif','c'),
    ('#pragma pack(push, 1)','c'),
    ('# 24 "D:/include/stdio.h" 3','c'),
    ('x+=5; // comment','c'),
    ('return &a;','c'),
    ('gcc -c 116-yuchuli.s -o 116-yuchuli.o','bash'),
    ('gcc 116-yuchuli.o -o 116-yuchuli','bash'),
    ('./116-yuchuli\nhello world!!','bash'),
    ('New-Item $PROFILE -ItemType File -Force','powershell'),
    ('$OutputEncoding = [console]::OutputEncoding','powershell'),
    ('.file "main.c"\n.text\n.globl main\nmovq %rsp, %rbp\nret','asm'),
    ('hello world!!','text'),
    ('sum=55','text'),
    ('sizeof(int)=4\nsizeof(char)=1','text'),
    ('01-hello.c','text'),
    ('-> 单击 "Windows PowerShell"\n-> 输入 gcc -v','text'),
    ('example.c:2:1: warning: unused variable\n    int x;','text'),
    ('这是彩色的说明文字','text'),
])
def test_language_fragments_and_outputs(text,language):
    assert detect_language(text,colored=True)==language


def test_context_keeps_printed_code_as_output():
    assert detect_language('int x;','运行结果')=='text'
    assert detect_language('int x;','源代码')=='c'
    assert detect_language('C语言 使用 int 和 char','说明',True)=='text'


def ocr_box(x,y,text,width=None):
    return ([[x,y],[x+(width or len(text)*10),y],[x+(width or len(text)*10),y+18],[x,y+18]],text,.99)


def test_color_ocr_panel_keeps_c_lines_indent_and_literal_symbols():
    pil=Image.new('RGB',(800,420),'white')
    d=ImageDraw.Draw(pil)
    d.rectangle((20,65,760,240),fill='#f3f3f3')
    d.rectangle((40,80,90,95),fill='blue')
    d.rectangle((95,80,130,95),fill='red')
    result=[ocr_box(30,20,'C example'),ocr_box(40,80,'#include <stdio.h>'),
            ocr_box(40,110,'int main() {'),ocr_box(80,140,'printf("hello\\n");'),
            ocr_box(80,170,'return 0;'),ocr_box(40,200,'}'),ocr_box(30,300,'Explanation follows.')]
    md=ocr_markdown(pil,result,escape)
    assert '```c\n#include <stdio.h>\nint main() {\n    printf("hello\\n");\n    return 0;\n}\n```' in md
    assert '\\<' not in md
    assert 'Explanation follows.' in md


def test_monochrome_ocr_code_and_colored_prose():
    pil=Image.new('RGB',(800,300),'white')
    d=ImageDraw.Draw(pil)
    d.rectangle((20,180,400,200),fill='blue')
    result=[ocr_box(30,20,'if (x) {'),ocr_box(70,50,'return 0;'),ocr_box(30,80,'}'),
            ocr_box(30,180,'Ordinary colored paragraph')]
    md=ocr_markdown(pil,result,escape)
    assert '```c\nif (x) {\n    return 0;\n}\n```' in md
    assert md.endswith('Ordinary colored paragraph')


def test_ocr_instructions_remain_text():
    pil=Image.new('RGB',(800,220),'#f3f3f3')
    # Leave a white border so the panel is a component within the page.
    ImageDraw.Draw(pil).rectangle((0,0,799,219),outline='white',width=15)
    result=[ocr_box(30,40,'-> Open VS Code'),ocr_box(30,70,'-> Install extension')]
    assert '```text\n-> Open VS Code\n-> Install extension\n```' in ocr_markdown(pil,result,escape)


def test_scanned_code_retains_original_for_punctuation_review(tmp_path,monkeypatch):
    import io,socket
    from PIL import ImageFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from markflow.core import Converter,Options
    pil=Image.new('RGB',(1400,1000),'white')
    draw=ImageDraw.Draw(pil)
    font=ImageFont.truetype('C:/Windows/Fonts/consola.ttf',44)
    draw.rectangle((40,100,1360,700),fill='#f3f3f3')
    for i,line in enumerate(['#include <stdio.h>','int main(void)','{','    printf("hello\\n");','    return 0;','}']):
        draw.text((70,130+i*80),line,font=font,fill='#1855a0' if i%2 else '#a03535')
    source=tmp_path/'scan-code.pdf'
    c=canvas.Canvas(str(source),pagesize=(700,500))
    c.drawImage(ImageReader(pil),0,0,700,500)
    c.save()
    def blocked(*a,**k):
        pytest.fail('OCR must remain offline')
    monkeypatch.setattr(socket.socket,'connect',blocked)
    result=Converter().convert(source,Options(tmp_path/'out'))
    assert result.ocr_pages==1
    assert '```c\n' in result.markdown
    assert 'main' in result.markdown
    assert 'OCR 代码原图（核对符号）' in result.markdown
    assert result.assets_dir and len(list(result.assets_dir.glob('*.png')))==1
    assert any('0/O' in warning for warning in result.warnings)
