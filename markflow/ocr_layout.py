"""Keep OCR geometry and use background/color only to support code detection."""
from dataclasses import dataclass
import re
import statistics

from .code_languages import detect_language
from .pdf_layout import fenced_code


@dataclass
class OCRRow:
    items: list

    @property
    def top(self):
        return min(i[1] for i in self.items)

    @property
    def bottom(self):
        return max(i[3] for i in self.items)

    @property
    def x0(self):
        return min(i[0] for i in self.items)

    @property
    def x1(self):
        return max(i[2] for i in self.items)

    @property
    def text(self):
        return ' '.join(i[4] for i in sorted(self.items))


def rows_from_ocr(result):
    groups=[]
    items=[]
    for box,text,confidence in result:
        if not text.strip():
            continue
        items.append((min(p[0] for p in box),min(p[1] for p in box),
                      max(p[0] for p in box),max(p[1] for p in box),text))
    for item in sorted(items,key=lambda i:((i[1]+i[3])/2,i[0])):
        center=(item[1]+item[3])/2
        if groups and abs(center-(groups[-1].top+groups[-1].bottom)/2)<min(item[3]-item[1],groups[-1].bottom-groups[-1].top)*.5:
            groups[-1].items.append(item)
        else:
            groups.append(OCRRow([item]))
    return groups


def colored_text(image,row):
    import numpy as np
    pixels=image[max(0,int(row.top)):int(row.bottom)+1,max(0,int(row.x0)):int(row.x1)+1]
    if pixels.size==0:
        return False
    high=pixels.max(axis=2).astype('int16')
    low=pixels.min(axis=2).astype('int16')
    return np.count_nonzero((high-low>45)&(low<210))>max(8,high.size*.006)


def shaded_panels(image):
    import cv2
    import numpy as np
    high=image.max(axis=2).astype('int16')
    low=image.min(axis=2).astype('int16')
    mask=((low>15)&(high<250)&(high-low<45)).astype('uint8')
    count,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    panels=[]
    height,width=image.shape[:2]
    for x,y,w,h,area in stats[1:]:
        if w>=100 and h>=20 and area>=600 and area/(w*h)>.6 and w*h<width*height*.95:
            panels.append((int(x),int(y),int(x+w),int(y+h)))
    return panels


def literal_rows(rows):
    # Width is inferred from OCR boxes. It is approximate: OCR can omit spaces
    # and confuse punctuation even when the visual layout is recovered.
    units=[]
    for row in rows:
        for x0,y0,x1,y1,text in row.items:
            cells=sum(2 if '\u3400'<=c<='\u9fff' else 1 for c in text)
            if cells>2:
                units.append((x1-x0)/cells)
    cell=statistics.median(units) if units else statistics.median(r.bottom-r.top for r in rows)*.55
    cell=max(1,cell)
    origin=min(r.x0 for r in rows)
    gaps=[b.top-a.top for a,b in zip(rows,rows[1:]) if b.top-a.top>1]
    leading=sorted(gaps)[len(gaps)//4] if gaps else cell*2
    output=[]
    for index,row in enumerate(rows):
        if index:
            output.extend(['']*min(6,max(0,round((row.top-rows[index-1].top)/leading)-1)))
        line=''
        right=origin
        for x0,y0,x1,y1,text in sorted(row.items):
            spaces=max(0,round((x0-right)/cell))
            line+=' '*spaces+text
            right=x1
        output.append(line.rstrip())
    return '\n'.join(output)


def ocr_markdown(pil,result,escape):
    import numpy as np
    image=np.asarray(pil.convert('RGB'))
    rows=rows_from_ocr(result)
    used=set()
    groups={}
    for box in sorted(shaded_panels(image),key=lambda b:(b[2]-b[0])*(b[3]-b[1])):
        indices=[i for i,r in enumerate(rows) if i not in used and box[0]<=r.x0+2 and r.x1<=box[2]+2
                 and box[1]<=(r.top+r.bottom)/2<=box[3]]
        if not indices:
            continue
        values=[rows[i] for i in indices]
        text=literal_rows(values)
        context=rows[indices[0]-1].text if indices[0] else ''
        color=any(colored_text(image,r) for r in values)
        language=detect_language(text,context,color)
        arrows=sum(bool(re.match(r'\s*(?:->|→)',r.text)) for r in values)
        if language!='text' or (len(values)>1 and arrows>=len(values)*.6):
            groups[indices[0]]=(indices,fenced_code(text,context,color))
            used.update(indices)
    parts=[]
    i=0
    while i<len(rows):
        if i in groups:
            indices,code=groups[i]
            parts.append(code)
            i+=1
            continue
        if i in used:
            i+=1
            continue
        context=rows[i-1].text if i else ''
        row=rows[i]
        language=detect_language(row.text,context,colored_text(image,row))
        if language!='text':
            block=[row]
            j=i+1
            while j<len(rows) and j not in used:
                nextrow=rows[j]
                close=nextrow.top-block[-1].bottom<max(8,(row.bottom-row.top)*1.5)
                weak=bool(re.match(r'^\s*(?:[{}]|else\b|//|/\*|\*/|\*\s)',nextrow.text))
                # Do not absorb plain Chinese explanatory paragraphs.
                nextlang=detect_language(nextrow.text,colored=colored_text(image,nextrow))
                if not close or (not weak and nextlang!=language):
                    break
                block.append(nextrow)
                j+=1
            parts.append(fenced_code(literal_rows(block),context,any(colored_text(image,r) for r in block)))
            i=j
        else:
            parts.append(escape(row.text))
            i+=1
    return '\n\n'.join(parts)
