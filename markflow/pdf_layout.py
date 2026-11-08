"""PDF text geometry, bookmark headings and literal code reconstruction.

Keep character coordinates until blocks are identified: word extraction alone
splits Chinese/Latin fonts with different ascenders on the same visual line.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import statistics
import unicodedata

from pypdf import PdfReader
from .code_languages import detect_language


RADICALS = str.maketrans("⻅⻆⻉⻋⻓⻔⻚⻛⻬", "见角贝车长门页风齐")


def normalize(text: str) -> str:
    # Repair PDF CJK compatibility mappings without changing code operators,
    # fullwidth string contents, or other characters with meaningful width.
    return "".join(unicodedata.normalize("NFKC", c)
                   if '\u2e80' <= c <= '\u2fff' or '\uf900' <= c <= '\ufaff'
                   or '\ufb00' <= c <= '\ufb06' else c for c in text).translate(RADICALS)


def comparison(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(RADICALS))


def monospace(char: dict) -> bool:
    return bool(re.search(r"courier|consolas|lucida.?console|menlo|monaco|mono|cascadia",
                          char.get("fontname", ""), re.I))


@dataclass
class Line:
    chars: list[dict]

    @property
    def top(self):
        return min(c["top"] for c in self.chars)

    @property
    def bottom(self):
        return max(c["bottom"] for c in self.chars)

    @property
    def x0(self):
        return min(c["x0"] for c in self.chars)

    @property
    def size(self):
        return max(c["size"] for c in self.chars)

    @property
    def text(self):
        return literal(self.chars).strip()


def group_lines(chars: list[dict]) -> list[Line]:
    rows: list[tuple[float, list[dict]]] = []
    for char in sorted((c for c in chars if c.get("text")),
                       key=lambda c: ((c["top"] + c["bottom"]) / 2, c["x0"])):
        center = (char["top"] + char["bottom"]) / 2
        # Compare centers with a font-relative tolerance, rather than rounded
        # tops. Adjacent 14pt code baselines remain separate; a 3.6pt CJK
        # ascender offset in a 12pt heading stays on one line.
        nearby = [(abs(center - y), i) for i, (y, row) in enumerate(rows[-4:], max(0, len(rows)-4))
                  if abs(center - y) <= min(char["size"], row[0]["size"]) * .52]
        if nearby:
            _, i = min(nearby)
            y, row = rows[i]
            rows[i] = ((y * len(row) + center) / (len(row) + 1), row + [char])
        else:
            rows.append((center, [char]))
    return [Line(sorted(row, key=lambda c: c["x0"])) for _, row in sorted(rows)]


def literal(chars: list[dict], origin=None, cell=None) -> str:
    chunks = []
    previous = None
    for char in sorted(chars, key=lambda c: c["x0"]):
        text = normalize(char["text"])
        if cell:
            text = text.replace('\u00a0',' ')
        if previous is None and origin is not None and cell:
            chunks.append(" " * max(0, round((char["x0"] - origin) / cell)))
        elif previous is not None:
            gap = char["x0"] - previous["x1"]
            unit = cell or max(1, min(char["size"], previous["size"]) * .3)
            if gap > unit * .6 and not chunks[-1].endswith((" ", "\t")) and not text.startswith(" "):
                chunks.append(" " * max(1, round(gap / unit)) if cell else " ")
        chunks.append(text)
        previous = char
    return "".join(chunks)


def contains(box, char):
    x = (char["x0"] + char["x1"]) / 2
    y = (char["top"] + char["bottom"]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def code_boxes(page) -> list[tuple]:
    candidates = {}
    for rect in page.rects:
        color = rect.get("non_stroking_color")
        if not rect.get("fill") or color is None:
            continue
        values = [color] if isinstance(color, (int, float)) else list(color)
        if not values or min(values) < .80 or max(values) > .995:
            continue
        box = (rect["x0"], rect["top"], rect["x1"], rect["bottom"])
        if box[2]-box[0] < 80 or box[3]-box[1] < 7 or (box[3]-box[1]) > page.height*.95:
            continue
        candidates[tuple(round(x, 1) for x in box)] = box
    accepted = []
    for box in sorted(candidates.values(), key=lambda b: (b[2]-b[0])*(b[3]-b[1])):
        inside = [c for c in page.chars if contains(box, c) and c["text"].strip()]
        if not inside or sum(monospace(c) for c in inside) < max(1, len(inside)*.06):
            continue
        # An outer shadow/border must not produce a duplicate code block.
        if any(box[0] <= b[0]+1 and box[1] <= b[1]+1 and box[2] >= b[2]-1 and box[3] >= b[3]-1
               for b in accepted):
            continue
        accepted.append(box)
    return accepted


def code_fence(lines: list[Line], origin=None, context="") -> str:
    chars = [c for line in lines for c in line.chars]
    widths = [c["x1"]-c["x0"] for c in chars if monospace(c) and c["text"].isascii()
              and len(c["text"]) == 1 and c["x1"] > c["x0"]]
    cell = statistics.median(widths) if widths else statistics.median(c["size"] for c in chars)*.6
    origin = min(line.x0 for line in lines) if origin is None else origin
    distances = [b.top-a.top for a,b in zip(lines,lines[1:]) if b.top-a.top > 1]
    # The lower quartile estimates normal leading even in short examples where
    # one of only two gaps is a deliberate blank line.
    leading = sorted(distances)[len(distances)//4] if distances else cell*2
    output = []
    for i, line in enumerate(lines):
        if i:
            output.extend([""] * min(8, max(0, round((line.top-lines[i-1].top)/leading)-1)))
        output.append(literal(line.chars, origin, cell).rstrip())
    text = "\n".join(output)
    colors = {str(c.get('non_stroking_color')) for c in chars if c['text'].strip()}
    return fenced_code(text,context=context,colored=len(colors)>1)


def fenced_code(text: str, context="", colored=False) -> str:
    language = detect_language(text,context,colored)
    # A fence longer than any literal backtick run also handles Markdown samples.
    fence = "`" * max(3, max((len(x) + 1 for x in re.findall(r"`+", text)), default=3))
    return f"{fence}{language}\n{text}\n{fence}"


def join_page_code(previous: str, current: str) -> tuple[str, str]:
    """Join only a panel continued through a physical PDF page boundary."""
    openings = list(re.finditer(r'(?m)^(`{3,})([a-zA-Z0-9_-]+)\n', previous))
    first = re.match(r'^(`{3,})([a-zA-Z0-9_-]+)\n',current)
    if not openings or not first:
        return previous,current
    last = openings[-1]
    previous_close = re.search(r'\n'+re.escape(last[1])+r'\s*$',previous)
    current_close = re.search(r'(?m)^'+re.escape(first[1])+r'\s*$',current[first.end():])
    if not previous_close or not current_close:
        return previous,current
    end = first.end()+current_close.start()
    continuation = current[first.end():end].rstrip('\n')
    body = previous[last.end():previous_close.start()]+'\n'+continuation
    # Re-detect language from the complete example and choose a safe fence.
    previous = previous[:last.start()]+fenced_code(body)
    current = current[first.end()+current_close.end():].lstrip('\n')
    return previous,current


@dataclass
class Heading:
    title: str
    level: int
    top: float | None
    matched: bool = False


class Layout:
    def __init__(self, source, pdf, warnings):
        self.headings = defaultdict(list)
        self.has_outline = False
        self.margin_text = set()
        self.base = 11
        self.heading_sizes = []
        try:
            reader = PdfReader(source)
            def walk(items, level=1):
                for item in items:
                    if isinstance(item, list):
                        walk(item, level+1)
                    else:
                        index = reader.get_destination_page_number(item)
                        if index is not None and item.title.strip():
                            dest = reader.pages[index]
                            # /Top is in unrotated PDF coordinates. For rotated
                            # pages use text matching, not an incorrect y hint.
                            top = (float(dest.mediabox.top) - float(item.top)) if item.top is not None and not dest.rotation else None
                            self.headings[index].append(Heading(normalize(item.title).strip(), min(6,level), top))
            walk(reader.outline)
            self.has_outline = bool(self.headings)
        except Exception:
            warnings.append("PDF 书签无法读取，已改用字体和版面判断标题。")
        fonts = Counter()
        margins = Counter()
        big = Counter()
        indices = sorted(set(round(i*(len(pdf.pages)-1)/max(1,min(11,len(pdf.pages)-1)))
                             for i in range(min(12,len(pdf.pages)))))
        for index in indices:
            page = pdf.pages[index]
            for c in page.chars:
                if c["text"].strip() and not monospace(c):
                    size = round(c["size"],1)
                    if not re.search("bold|black",c.get("fontname",""),re.I):
                        fonts[size] += 1
                    big[size] += 1
            seen = set()
            for line in group_lines(page.chars):
                if line.top < 32 or line.bottom > page.height-40:
                    if not any(monospace(c) for c in line.chars):
                        seen.add(comparison(line.text))
            margins.update(seen)
            page.close()
        self.margin_text = {t for t,n in margins.items() if n >= max(3,len(indices)//2) and t}
        if fonts:
            self.base = fonts.most_common(1)[0][0]
        self.heading_sizes = sorted((s for s,n in big.items() if s >= self.base*1.15 and n>=3), reverse=True)[:6]

    def match_headings(self, index, lines):
        matched = {}
        consumed = set()
        for heading in self.headings[index]:
            target = comparison(heading.title)
            candidates = []
            for i,line in enumerate(lines):
                if i in consumed or (heading.top is not None and abs(line.top-heading.top)>max(60,line.size*4)):
                    continue
                for length in range(1,4):
                    group = lines[i:i+length]
                    if len(group)!=length or any(j in consumed for j in range(i,i+length)):
                        break
                    if length>1 and group[-1].top-group[-2].top > line.size*2:
                        break
                    value = comparison("".join(l.text for l in group))
                    similarity = SequenceMatcher(None,target,value).ratio()
                    if target==value or (len(target)>12 and similarity>=.97):
                        distance = abs(line.top-heading.top) if heading.top is not None else i
                        candidates.append((similarity,-distance,i,length))
            if candidates:
                _,_,i,length = max(candidates)
                matched[i] = heading
                consumed.update(range(i,i+length))
                heading.matched = True
        return matched, consumed

    def inferred_level(self, line):
        if self.has_outline or line.size < self.base*1.15 or len(line.text)>160:
            return 0
        if line.size < self.base*1.3 and not any(re.search("bold|black",c.get("fontname",""),re.I) for c in line.chars):
            return 0
        return min(6, 1+min(range(len(self.heading_sizes)), key=lambda i:abs(self.heading_sizes[i]-line.size))) if self.heading_sizes else 1

    def report(self, warnings):
        missing = [h for group in self.headings.values() for h in group if not h.matched]
        if missing:
            names = "、".join(h.title for h in missing[:3])
            warnings.append(f"{len(missing)} 个 PDF 书签未能与正文标题匹配，请核对大纲（例如：{names}）。")


def is_scanned(page, raw, digital_context=False):
    if len(raw.strip()) >= 40 or not page.images:
        return False
    area = page.width*page.height
    largest = max((i["x1"]-i["x0"])*(i["bottom"]-i["top"]) for i in page.images)/area
    return largest >= .8 or (not digital_context and not raw.strip() and largest >= .35)
