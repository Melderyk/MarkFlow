from pathlib import Path
import io

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def create_samples(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    fontpath = Path("C:/Windows/Fonts/msyh.ttc")
    font = lambda size: ImageFont.truetype(str(fontpath), size)
    banner = Image.new("RGB", (800, 230), "#edf3ff")
    draw = ImageDraw.Draw(banner)
    draw.rounded_rectangle((25, 25, 775, 205), radius=25, fill="#e0ebff", outline="#93b3ea", width=3)
    draw.text((60, 55), "文档  →  Markdown", font=font(44), fill="#2354a1")
    draw.text((65, 125), "标题 · 表格 · 图片 · 离线 OCR", font=font(26), fill="#55739a")
    image = folder / "流程示意.png"
    banner.save(image)
    doc = Document()
    doc.add_heading("墨页 MarkFlow 使用示例", 0)
    doc.add_paragraph("这份示例用于检查 Word 转 Markdown 的基础效果。")
    doc.add_heading("1. 文本与格式", level=1)
    p = doc.add_paragraph("支持 ")
    p.add_run("加粗文字").bold = True
    p.add_run(" 和 ")
    p.add_run("斜体文字").italic = True
    p.add_run("，以及链接：")
    link = OxmlElement("w:hyperlink")
    rid = doc.part.relate_to("https://example.com", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "示例链接"
    run.append(text)
    link.append(run)
    p._p.append(link)
    doc.add_paragraph("添加 PDF 或 Word 文件", style="List Bullet")
    doc.add_paragraph("选择输出目录并开始转换", style="List Bullet")
    doc.add_paragraph("对照原文检查并保存结果", style="List Number")
    doc.add_heading("2. 表格与图片", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for c, t in zip(table.rows[0].cells, ["功能", "模式", "状态"]):
        c.text = t
    for values in [["文字提取", "本地", "支持"], ["图像 | 导出", "相对路径", "支持"], ["扫描件", "OCR", "中英文"]]:
        for c, t in zip(table.add_row().cells, values):
            c.text = t
    doc.add_picture(str(image), width=Inches(5.5))
    docx = folder / "01_Word示例.docx"
    doc.save(docx)

    pdfmetrics.registerFont(TTFont("SampleCJK", "C:/Windows/Fonts/simsun.ttc", subfontIndex=0))
    pdf = folder / "02_文字PDF示例.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(595, 842))
    c.setTitle("MarkFlow text PDF sample")
    c.bookmarkPage("intro")
    c.addOutlineEntry("MarkFlow PDF Sample", "intro", level=0)
    c.setFont("Helvetica-Bold", 23)
    c.drawString(48, 785, "MarkFlow PDF Sample")
    c.setFont("SampleCJK", 12)
    c.drawString(48, 750, "这是文字型 PDF：包含段落、表格和一张图片。")
    c.setFont("Helvetica", 12)
    c.drawString(48, 725, "Offline conversion keeps your documents on this computer.")
    c.linkURL("https://example.com", (48, 698, 180, 714), relative=0)
    c.drawString(48, 700, "Example hyperlink")
    for y in [650, 620, 590, 560]:
        c.line(48, y, 548, y)
    for x in [48, 260, 400, 548]:
        c.line(x, 560, x, 650)
    for row, values in enumerate([["Feature", "Mode", "Status"], ["PDF text", "Local", "Ready"], ["Images", "PNG", "Ready"]]):
        for col, text in enumerate(values):
            c.drawString([58, 270, 410][col], 630 - row * 30, text)
    c.drawImage(str(image), 48, 350, width=500, height=144)
    c.showPage()
    c.bookmarkPage("preview")
    c.addOutlineEntry("Second Page", "preview", level=1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(48, 780, "Second Page")
    c.setFont("Helvetica", 12)
    c.drawString(48, 745, "Page preview and batch conversion are available.")
    c.setFillColorRGB(.97,.97,.97)
    c.rect(48,570,500,145,stroke=0,fill=1)
    c.setFillColorRGB(0,0,0)
    c.setFont("Courier",11)
    for i,line in enumerate(['#include <stdio.h>','int main() {',r'    printf("Hello, MarkFlow!\n");','    return 0;','}']):
        c.drawString(58,690-i*20,line)
    c.save()

    scan = Image.new("RGB", (1400, 1900), "white")
    d = ImageDraw.Draw(scan)
    d.text((100, 110), "离线识别测试 Offline OCR", font=font(62), fill="#111111")
    d.text((100, 255), "项目名称：墨页 MarkFlow", font=font(44), fill="#111111")
    d.text((100, 350), "这是一份扫描型 PDF 示例。", font=font(44), fill="#111111")
    d.text((100, 445), "文件不会上传到网络。", font=font(44), fill="#111111")
    d.text((100, 540), "总金额：128.50 元", font=font(44), fill="#111111")
    d.text((100, 635), "Invoice number: MF-2026-001", font=font(39), fill="#111111")
    d.text((100, 730), "请对照原文核对识别结果。", font=font(44), fill="#111111")
    code_font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 44)
    d.rectangle((70, 850, 1330, 1460), fill="#f3f3f3")
    for i, line in enumerate(['#include <stdio.h>', 'int main(void)', '{',
                              '    printf("MarkFlow\\n");', '    return 0;', '}']):
        d.text((100, 880+i*80), line, font=code_font,
               fill="#1855a0" if i % 2 else "#a03535")
    scanned = folder / "03_扫描PDF示例.pdf"
    c = canvas.Canvas(str(scanned), pagesize=(595, 807.5))
    c.drawImage(ImageReader(scan), 0, 0, width=595, height=807.5)
    c.save()
    return docx, pdf, scanned


if __name__ == "__main__":
    import sys
    create_samples(Path(sys.argv[1]))
