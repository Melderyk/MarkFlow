"""Conservative, offline language labels for document code fragments.

Colors are supporting evidence, never a language identifier by themselves.
Runtime output, filenames and instructions must remain literal text.
"""
import re


def detect_language(text: str, context: str = "", colored: bool = False) -> str:
    value = text.strip()
    if not value:
        return "text"
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if all(re.match(r"^(?:->|→|=>)", line) for line in lines):
        return "text"
    # Printed diagnostics can themselves quote valid C declarations.
    if re.search(r"(?im)^.*(?:\.[ch]:\d+(?::\d+)?:\s*(?:warning|error|note)|In file included from|段错误)", value):
        return "text"
    if re.search(r"(?:运行结果|运行输出|输出结果|运算结果|程序输出|控制台输出|编译结果|执行结果)\s*[:：]?\s*$", context):
        return "text"
    if re.search(r"(?im)^\s*(?:PS\s+[^>]+>|\$(?:env:|PROFILE\b|OutputEncoding\b)|\[console\]::|"
                 r"(?:New-Item|Set-ExecutionPolicy|Set-Location|Get-ChildItem|Write-Host|chcp)\b)", value):
        return "powershell"
    if re.search(r"(?m)^\s*(?:\$\s+|[\w.-]+@[\w.-]+:[^\n]*\$\s*|(?:gcc|g\+\+|clang(?:\+\+)?|make|cmake|gdb|objdump|ar)\s+)",value):
        return "bash"
    if re.match(r"^\s*\./[\w./-]+(?:\s|$)",value):
        return "bash"
    directives = re.findall(r"(?m)^\s*\.(?:file|text|globl|global|section|cfi_\w+|type|size|data|long|ident)\b",value)
    instructions = re.findall(r"(?im)^\s*(?:mov[qwlb]?|push[qwl]?|pop[qwl]?|lea[qwl]?|sub[qwl]?|add[qwl]?|call[q]?|ret[q]?|leave|ldr|str|bx)\b",value)
    if len(directives)>=2 or (len(instructions)>=2 and re.search(r"%\w+|\b[rwx]\d+\b|\.(?:text|globl)",value)):
        return "asm"
    if re.search(r"(?m)^\s*#\s*(?:include|define|undef|if|ifdef|ifndef|elif|else|endif|pragma|error|line)\b|^\s*#\s*\d+\s+\"",value):
        return "c"
    # Exclude comments/strings when testing syntax. Do not change the output.
    syntax = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ',value,flags=re.S)
    if re.search(r"\b(?:typedef|struct|union|enum)\s+[\w\u3400-\u9fff{]",syntax):
        return "c"
    types = r"(?:void|char|short|int|long|float|double|signed|unsigned|_Bool|bool|size_t|u?int(?:8|16|32|64)_t|FILE)"
    if re.search(r"\b"+types+r"\s+(?:(?:const|volatile|unsigned|signed|long|short|int)\s+)*(?:\*\s*)*[\w\u3400-\u9fff]+\s*(?:[,;=\[(]|[：:])",syntax):
        return "c"
    if re.search(r"\b"+types+r"\s*\(\s*\*\s*\w+",syntax):
        return "c"
    if re.search(r"\b(?:if|for|while|switch)\s*\([^\n]*\)|\b(?:case\s+[^:]+:|default\s*:)|\b(?:return|break|continue|goto)\b[^\n]*;",syntax):
        return "c"
    if re.search(r"(?m)^\s*(?:[\w.]+(?:\[[^\n]+\])?(?:->\w+)?\s*(?:[+*/%&|^-]?=|\+\+|--)\s*[^\n]*;|\w+\s*\([^\n;]*\)\s*;)",syntax):
        return "c"
    if re.search(r"(?m)^\s*(?:printf|scanf|puts|putchar|getchar|fopen|fclose|malloc|free|sizeof)\s*\(",syntax):
        # sizeof(x)=4 is printed output, rather than a C expression.
        if not re.search(r"\bsizeof\s*\([^\n]*\)\s*=\s*\d",value):
            return "c"
    keywords = re.findall(r"\b(?:auto|break|case|char|const|continue|default|do|double|else|enum|extern|float|for|goto|if|int|long|register|return|short|signed|sizeof|static|struct|switch|typedef|union|unsigned|void|volatile|while)\b",syntax)
    if len(set(keywords))>=10:
        return "c"
    # A colored keyword fragment can be a declaration without a semicolon.
    # Require both syntax punctuation and multiple C keywords, not mere color.
    if colored and len(set(keywords))>=2 and re.search(r"[{};]",syntax):
        return "c"
    return "text"
