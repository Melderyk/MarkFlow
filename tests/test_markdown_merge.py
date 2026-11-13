from pathlib import Path
import threading
from urllib.parse import unquote

import pytest

from markflow.core import Cancelled, ConversionError
from markflow.markdown_merge import merge_markdown_files, rebase_markdown_links


def document(folder, name, text, encoding='utf-8'):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding=encoding)
    return path


def test_direct_merge_keeps_order_sources_and_existing_output(tmp_path):
    first = document(tmp_path / 'a', '同名.md', '# 第一份\n\n正文 A\n')
    second = document(tmp_path / 'b', '同名.markdown', '# 第二份\n\n正文 B\n')
    originals = {path: path.read_bytes() for path in (first, second)}
    output = tmp_path / 'out'
    output.mkdir()
    existing = document(output, '合并文档.md', 'already here')
    destination = merge_markdown_files([second, first, second], output)
    text = destination.read_text(encoding='utf-8')
    assert destination.name == '合并文档_2.md'
    assert text.index('# 第二份') < text.index('# 第一份')
    assert text.count('# 第二份') == 1
    assert '## 目录' in text and '<a id="document-2"></a>' in text
    assert existing.read_text(encoding='utf-8') == 'already here'
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_local_links_titles_references_and_code_examples(tmp_path):
    source = tmp_path / '原始资料'
    output = tmp_path / '导出'
    (source / 'images').mkdir(parents=True)
    (source / 'images' / '图 (1).png').write_bytes(b'image')
    text = ('![图片](<images/图 (1).png> "原图")\n'
            '![另一张](images/picture(2).png "标题")\n'
            '[附件](notes.md#section)\n'
            '![引用][photo]\n[photo]: <images/图 (1).png> "参考"\n'
            '<img src="images/small.png" width="10">\n'
            '![远程](https://example.com/a.png)\n'
            '![协议相对](//example.com/a.png)\n'
            '![嵌入](data:image/png;base64,AAAA)\n'
            '[段落](#section)\n'
            '`![行内示例](images/sample.png)`\n'
            '```md\n![代码示例](images/sample.png)\n```\n')
    rebased = rebase_markdown_links(text, source, output)
    expected = '../%E5%8E%9F%E5%A7%8B%E8%B5%84%E6%96%99/images/'
    assert f'![图片](<{expected}%E5%9B%BE%20%281%29.png> "原图")' in rebased
    assert f'![另一张]({expected}picture%282%29.png "标题")' in rebased
    assert f'[photo]: <{expected}%E5%9B%BE%20%281%29.png> "参考"' in rebased
    assert '<img src="' + expected + 'small.png" width="10">' in rebased
    assert '[附件](../%E5%8E%9F%E5%A7%8B%E8%B5%84%E6%96%99/notes.md#section)' in rebased
    for original in text.splitlines()[7:]:
        assert original in rebased
    target = rebased.split('![图片](<', 1)[1].split('>', 1)[0]
    assert (output / unquote(target)).resolve() == source / 'images' / '图 (1).png'


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'gb18030'])
def test_direct_merge_reads_chinese_encodings(tmp_path, encoding):
    first = document(tmp_path, '中文.MD', '# 中文内容\n\n保留正文', encoding)
    second = document(tmp_path, 'other.markdown', '# Other')
    destination = merge_markdown_files([first, second], tmp_path / 'out', 'Typora')
    text = destination.read_text(encoding='utf-8')
    assert '中文内容' in text and '\ufeff' not in text and '[TOC]' in text


@pytest.mark.parametrize('invalid', ['duplicate', 'missing', 'suffix', 'fence', 'limit', 'cancelled'])
def test_invalid_merge_creates_no_partial_output(tmp_path, monkeypatch, invalid):
    from markflow import formats
    first = document(tmp_path, 'first.md', '# First\n')
    second = document(tmp_path, 'second.md', '# Second\n')
    paths = [first, second]
    cancel = threading.Event()
    if invalid == 'duplicate':
        paths = [first, first]
    elif invalid == 'missing':
        second.unlink()
    elif invalid == 'suffix':
        paths[1] = document(tmp_path, 'second.txt', 'not markdown')
    elif invalid == 'fence':
        second.write_text('```python\nprint(1)', encoding='utf-8')
    elif invalid == 'limit':
        monkeypatch.setattr(formats, 'MAX_INPUT_BYTES', 12)
    elif invalid == 'cancelled':
        cancel.set()
    output = tmp_path / 'out'
    with pytest.raises(Cancelled if invalid == 'cancelled' else ConversionError):
        merge_markdown_files(paths, output, cancel=cancel)
    assert not output.exists()


def test_merge_does_not_rewrite_absolute_or_remote_paths(tmp_path):
    text = f'![本地](<{tmp_path.as_posix()}/a.png>)\n[文件](file:///C:/a.pdf)\n'
    assert rebase_markdown_links(text, tmp_path, tmp_path / 'out') == text


def test_repeated_reference_ids_and_footnotes_remain_distinct(tmp_path):
    text = ('![图][image]\n[image][]\n[image]\n脚注[^1]\n'
            '[image]: images/a.png "标题"\n[^1]: 脚注文字\n'
            '`[image]`\n```md\n[image]: example.png\n```\n')
    first = document(tmp_path / 'a', 'first.md', text)
    second = document(tmp_path / 'b', 'second.md', text)
    destination = merge_markdown_files([first, second], tmp_path / 'out')
    merged = destination.read_text(encoding='utf-8')
    for index, folder in ((1, 'a'), (2, 'b')):
        assert f'![图][document-{index}-image]' in merged
        assert f'[image][document-{index}-image]' in merged
        assert f'[document-{index}-image]: ../{folder}/images/a.png "标题"' in merged
        assert f'脚注[^document-{index}-1]' in merged
        assert f'[^document-{index}-1]: 脚注文字' in merged
    assert merged.count('`[image]`') == 2
    assert merged.count('```md\n[image]: example.png\n```') == 2
