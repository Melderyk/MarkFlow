"""Merge existing Markdown files without converting their contents."""
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

from .markdown_tools import document_index


_TARGET = r'<[^>\n]*>|(?:\\.|[^\\\s()]|\((?:\\.|[^\\()\n])*\))+'
_INLINE_LINK = re.compile(
    r'!?\[(?:\\.|[^\]\\])*\]\([ \t]*(?P<target>' + _TARGET + r')'
    r'(?:[ \t]+(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\([^\n)]*\)))?[ \t]*\)')
_REFERENCE = re.compile(r'^ {0,3}\[(?!\^)(?:\\.|[^\]\\])+\]:[ \t]*(?P<target>' + _TARGET + r')')
_HTML_LINK = re.compile(r'<(?:img|a|source)\b[^>]*?\b(?:src|href)\s*=\s*["\'](?P<target>[^"\']*)["\']', re.I)
_INLINE_CODE = re.compile(r'(`+).*?(?<!`)\1(?!`)')
_DEFINITION = re.compile(r'^ {0,3}\[(?P<label>(?:\\.|[^\]\\])+)\]:')
_REFERENCE_USE = re.compile(r'(?P<image>!?)\[(?P<text>(?:\\.|[^\]\\])+)\](?:\[(?P<label>(?:\\.|[^\]\\])*)\])?(?![(:])')


def namespace_references(markdown, document_number):
    """Keep identically named references and footnotes distinct across files."""
    lines = markdown.splitlines(keepends=True)
    outside = sorted(document_index(markdown).outside)
    normalize = lambda value: ' '.join(value.split()).casefold()
    labels = {}
    for index in outside:
        match = _DEFINITION.match(lines[index])
        if match:
            label = match['label']
            replacement = (f'^document-{document_number}-' + label[1:] if label.startswith('^')
                           else f'document-{document_number}-' + label)
            labels[normalize(label)] = replacement
    for index in outside:
        line = lines[index]
        definition = _DEFINITION.match(line)
        code = [(m.start(), m.end()) for m in _INLINE_CODE.finditer(line)]
        def rewrite(match):
            if (definition and match.start() <= definition.end()) or any(start <= match.start() < end for start, end in code):
                return match[0]
            label = match['label'] or match['text']
            replacement = labels.get(normalize(label))
            if replacement is None:
                return match[0]
            if match['text'].startswith('^'):
                return '[' + replacement + ']'
            return match['image'] + '[' + match['text'] + '][' + replacement + ']'
        line = _REFERENCE_USE.sub(rewrite, line)
        if definition:
            start, end = definition.span('label')
            line = line[:start] + labels[normalize(definition['label'])] + line[end:]
        lines[index] = line
    return ''.join(lines)


def rebase_markdown_links(markdown, source_parent, output_dir):
    """Keep relative images and file links valid; leave fenced examples intact."""
    def target(value):
        angled = value.startswith('<') and value.endswith('>')
        raw = value[1:-1] if angled else value
        if not raw or raw.startswith(('#', '//')) or Path(raw).is_absolute():
            return value
        parsed = urlsplit(raw.replace('\\', '/'))
        if parsed.scheme:
            return value
        absolute = (source_parent / unquote(parsed.path)).resolve()
        try:
            replacement = quote(os.path.relpath(absolute, output_dir).replace('\\', '/'), safe='/.:_-~')
        except ValueError:  # Different Windows drives cannot use relative paths.
            replacement = absolute.as_uri()
        if parsed.query:
            replacement += '?' + parsed.query
        if parsed.fragment:
            replacement += '#' + parsed.fragment
        return '<' + replacement + '>' if angled else replacement

    lines = markdown.splitlines(keepends=True)
    for index in sorted(document_index(markdown).outside):
        line = lines[index]
        code = [(m.start(), m.end()) for m in _INLINE_CODE.finditer(line)]
        replacements = []
        for pattern in (_INLINE_LINK, _REFERENCE, _HTML_LINK):
            for match in pattern.finditer(line):
                if any(start <= match.start() < end for start, end in code):
                    continue
                start, end = match.span('target')
                if any(start < right and end > left for left, right, _ in replacements):
                    continue
                replacements.append((start, end, target(match['target'])))
        for start, end, value in sorted(replacements, reverse=True):
            line = line[:start] + value + line[end:]
        lines[index] = line
    return ''.join(lines)


def merge_markdown_files(paths, output_dir, profile='GitHub / 通用', *, cancel=None, progress=lambda _: None):
    from .core import ConversionError, Converter, Result, merge_results
    from .formats import MAX_INPUT_BYTES, read_text

    sources = list(dict.fromkeys(Path(path).resolve() for path in paths))
    if len(sources) < 2:
        raise ConversionError('请至少选择两个不同的 MD 文档。')
    results = []
    total = 0
    for index, source in enumerate(sources, 1):
        Converter.check(cancel)
        if source.suffix.lower() not in {'.md', '.markdown'} or not source.is_file():
            raise ConversionError(f'不是可读取的 MD 文档：{source.name}')
        total += source.stat().st_size
        if total > MAX_INPUT_BYTES:
            raise ConversionError('所选 MD 文档合计超过 64 MB，请分批合并。')
        progress(f'正在读取 {index} / {len(sources)}：{source.name}')
        markdown = read_text(source)
        if any(issue.message == '代码围栏未闭合' for issue in document_index(markdown).issues):
            raise ConversionError(f'{source.name} 的代码围栏未闭合，请修正后再合并。')
        results.append(Result(source, source, namespace_references(markdown, index)))
    Converter.check(cancel)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress('正在生成合并文档')
    return merge_results(results, output_dir, profile, cancel=cancel)
