"""A small markdown reader for the explanations: enough for what they contain (paragraphs, headings, lists,
code, bold, italic, inline code and links), turned into blocks the PDF and Word writers both understand."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# one piece of inline text: its words and how they look
@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str | None = None


@dataclass
class Block:
    kind: str  # "p" | "h" | "item" | "code"
    runs: list[Run] = field(default_factory=list)
    level: int = 0  # heading level, or list nesting depth
    marker: str = ""  # "•" or "3." for list items
    text: str = ""  # code blocks


_INLINE = re.compile(
    r"(?P<code>`+)(?P<ctext>.+?)(?P=code)"
    r"|\*\*(?P<b1>.+?)\*\*"
    r"|(?<![\w])__(?P<b2>.+?)__(?![\w])"
    r"|(?<![\w*])\*(?![\s*])(?P<i1>.+?)(?<![\s*])\*(?![\w*])"
    r"|(?<![\w])_(?![\s_])(?P<i2>.+?)(?<![\s_])_(?![\w])"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<url>[^)\s]+)\)"
)


def inline(text: str, bold: bool = False, italic: bool = False, link: str | None = None) -> list[Run]:
    runs: list[Run] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append(Run(text[pos:m.start()], bold, italic, False, link))
        if m.group("code"):
            runs.append(Run(m.group("ctext").strip(), bold, italic, True, link))
        elif m.group("b1") or m.group("b2"):
            runs += inline(m.group("b1") or m.group("b2"), True, italic, link)
        elif m.group("i1") or m.group("i2"):
            runs += inline(m.group("i1") or m.group("i2"), bold, True, link)
        else:
            url = m.group("url")
            runs += inline(m.group("ltext"), bold, italic, url if re.match(r"https?://", url) else None)
        pos = m.end()
    if pos < len(text):
        runs.append(Run(text[pos:], bold, italic, False, link))
    return [r for r in runs if r.text]


_ITEM = re.compile(r"^(?P<indent>\s*)(?:(?P<bullet>[-*+])|(?P<num>\d{1,3})[.)])\s+(?P<text>.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def parse(md: str) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []
    lines = md.replace("\r\n", "\n").split("\n")

    def flush():
        if para:
            blocks.append(Block("p", inline(" ".join(s.strip() for s in para))))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            flush()
            fence = s[:3]
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            blocks.append(Block("code", text="\n".join(body).rstrip("\n")))
            i += 1
            continue
        if not s:
            flush()
            i += 1
            continue
        if h := _HEADING.match(s):
            flush()
            blocks.append(Block("h", inline(h.group(2)), level=len(h.group(1))))
            i += 1
            continue
        if s.startswith("|") and s.endswith("|"):  # a table: kept as aligned text
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            widths = [max(len(r[k]) if k < len(r) else 0 for r in rows) for k in range(max(map(len, rows)))]
            text = "\n".join("  ".join(c.ljust(widths[k]) for k, c in enumerate(r)).rstrip() for r in rows)
            blocks.append(Block("code", text=text))
            continue
        if m := _ITEM.match(line):
            flush()
            depth = min(len(m.group("indent").expandtabs(4)) // 2, 3)
            text = [m.group("text")]
            i += 1
            # lines that continue this item: indented, and not a new item, heading or blank line
            while i < len(lines) and lines[i].strip() and not _ITEM.match(lines[i]) \
                    and not _HEADING.match(lines[i].strip()) and (lines[i].startswith((" ", "\t")) or not para):
                if lines[i].strip().startswith(("```", "~~~")):
                    break
                text.append(lines[i].strip())
                i += 1
            marker = "•" if m.group("bullet") else f"{m.group('num')}."
            blocks.append(Block("item", inline(" ".join(text)), level=depth, marker=marker))
            continue
        para.append(line)
        i += 1
    flush()
    return blocks


def plain(md: str) -> str:
    """The text of some markdown, without its marks (for titles)."""
    return "".join(r.text for b in parse(md) for r in b.runs)
