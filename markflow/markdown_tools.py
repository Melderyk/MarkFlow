"""Fence-aware document navigation, diagnostics and portable chapter exports."""
from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

from .code_languages import detect_language


@dataclass
class Heading:
    level: int
    title: str
    line: int


@dataclass
class CodeBlock:
    line: int
    end: int
    marker: str
    language: str
    text: str
    context: str = ''


@dataclass
class Issue:
    message: str
    line: int = 0
    page: int = 0
    kind: str = '提示'


@dataclass
class DocumentIndex:
    headings: list = field(default_factory=list)
    codes: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    images: list = field(default_factory=list)
    outside: set = field(default_factory=set)


IMAGE = re.compile(r'!\[((?:\\.|[^\]\\])*)\]\(([^\n)]+)\)')


def document_index(markdown, base=None, warnings=()):
    result=DocumentIndex()
    lines=markdown.splitlines()
    opened=None
    context=''
    previous_level=0
    for i,line in enumerate(lines):
        if opened:
            if re.fullmatch(r' {0,3}'+re.escape(opened[1][0])+r'{'+str(len(opened[1]))+r',}\s*',line):
                start,marker,lang,preceding=opened
                result.codes.append(CodeBlock(start,i,marker,lang,'\n'.join(lines[start+1:i]),preceding))
                opened=None
            continue
        m=re.match(r'^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^\n]*$',line)
        if m:
            opened=(i,m[1],m[2],context)
            continue
        result.outside.add(i)
        m=re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$',line)
        if m:
            level=len(m[1])
            result.headings.append(Heading(level,m[2],i))
            if previous_level and level>previous_level+1:
                result.issues.append(Issue('标题跳级：请核对层级',i))
            previous_level=level
        for image in IMAGE.finditer(line):
            result.images.append((i,image[1],image[2]))
            target=unquote(image[2].strip('<>'))
            if base and not urlsplit(target).scheme and not target.startswith('#') and not (Path(base)/target).is_file():
                result.issues.append(Issue('本地图片文件不存在',i,kind='图片'))
        if line.strip():
            context=line.strip()
    if opened:
        result.issues.append(Issue('代码围栏未闭合',opened[0],kind='代码'))
    for block in result.codes:
        guessed=detect_language(block.text,block.context)
        if block.language in {'','text','plaintext'} and guessed!='text':
            result.issues.append(Issue(f'代码语言待确认：可改为 {guessed}',block.line,kind='代码'))
    for warning in warnings:
        m=re.search(r'第\s*(\d+)\s*页',warning)
        page=int(m[1]) if m else 0
        # OCR reference images give a precise Markdown location as well.
        line=next((i for i,alt,_ in result.images if page and re.search(r'第\s*'+str(page)+r'\s*页',alt)),0)
        result.issues.append(Issue(warning,line,page,'转换'))
    return result


def rewrite_images(markdown, rewrite):
    """Never interpret examples inside fenced code as actual image references."""
    index=document_index(markdown)
    lines=markdown.splitlines(keepends=True)
    for i in sorted(index.outside):
        lines[i]=IMAGE.sub(lambda m: rewrite(m) or m[0],lines[i])
    return ''.join(lines)


def change_language(markdown, block, language):
    if not re.fullmatch(r'[a-zA-Z0-9_+.#-]{1,40}',language):
        raise ValueError('语言标签只能包含字母、数字及 _+.#-')
    lines=markdown.splitlines(keepends=True)
    ending='\r\n' if lines[block.line].endswith('\r\n') else '\n'
    indent=re.match(r'^ *',lines[block.line])[0]
    lines[block.line]=indent+block.marker+language+ending
    return ''.join(lines)


def preview_sections(markdown, target=14000):
    """Bound layout work; repeat a large table's header on preview pages only."""
    lines=markdown.splitlines(keepends=True)
    index=document_index(markdown)
    # A spreadsheet can produce thousands of adjacent rows with no blank lines.
    # Qt otherwise lays out the entire table synchronously in one preview page.
    table_rows={}
    delimiter=re.compile(r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$')
    i=0
    while i+1<len(lines):
        if (i in index.outside and i+1 in index.outside and '|' in lines[i]
                and delimiter.fullmatch(lines[i+1].rstrip('\r\n'))):
            header=''.join(lines[i:i+2])
            j=i+2
            while j<len(lines) and j in index.outside and lines[j].strip() and '|' in lines[j]:
                table_rows[j]=header
                j+=1
            i=j
        else:
            i+=1
    sections=[]
    start=0
    size=0
    prefix=''
    for i,line in enumerate(lines):
        if (i>start and size>=target and i in index.outside
                and (line.startswith('#') or not line.strip() or i in table_rows)):
            sections.append((start,i,prefix+''.join(lines[start:i])))
            start=i
            prefix=table_rows.get(i,'')
            size=len(prefix)
        size+=len(line)
    if lines:
        sections.append((start,len(lines),prefix+''.join(lines[start:])))
    return sections


def export_chapters(markdown, destination):
    """Create an additional reading copy; leave the complete document intact."""
    from .core import atomic_write, safe_name
    destination=Path(destination)
    index=document_index(markdown)
    roots=[h for h in index.headings if h.level==1]
    if len(roots)<2 or len(markdown)<50000:
        return None
    folder=destination.with_suffix('.chapters')
    folder.mkdir(exist_ok=True)
    lines=markdown.splitlines(keepends=True)
    names=[f'{i+1:02d}-{safe_name(h.title)}.md' for i,h in enumerate(roots)]
    def relative(m):
        target=m[2].strip('<>')
        if urlsplit(target).scheme or target.startswith('#'):
            return m[0]
        return f'![{m[1]}](../{target})'
    for i,h in enumerate(roots):
        start=0 if i==0 else h.line
        end=roots[i+1].line if i+1<len(roots) else len(lines)
        body=rewrite_images(''.join(lines[start:end]),relative)
        nav=['[章节目录](目录.md)']
        if i:
            nav.append(f'[上一章]({quote(names[i-1])})')
        if i+1<len(names):
            nav.append(f'[下一章]({quote(names[i+1])})')
        atomic_write(folder/names[i],body.rstrip()+'\n\n---\n\n'+' · '.join(nav)+'\n')
    toc='# 分章阅读目录\n\n每次打开一章，减轻长文档排版与代码高亮负担。完整文档保存在上一级目录。\n\n'
    toc+='\n'.join(f'- [{h.title}]({quote(name)})' for h,name in zip(roots,names))+'\n'
    atomic_write(folder/'目录.md',toc)
    # Remove only files previously generated by this module if headings changed.
    manifest=folder/'.markflow-chapters.json'
    import json
    try:
        previous=json.loads(manifest.read_text(encoding='utf8'))
    except (OSError,ValueError):
        previous=[]
    for name in previous:
        if name not in names and Path(name).name==name and re.match(r'^\d{2,}-.+\.md$',name):
            (folder/name).unlink(missing_ok=True)
    atomic_write(manifest,json.dumps(names,ensure_ascii=False))
    return folder
