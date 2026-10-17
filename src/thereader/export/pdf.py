"""Write a Document as a PDF: title page, contents with page numbers, bookmarks, and every section."""

from __future__ import annotations

import importlib.util
import os
import re
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from .build import Document, Section
from .md import Block, Run

INK = HexColor("#1c2a3a")
INK2 = HexColor("#44546a")
MUTED = HexColor("#758394")
RULE = HexColor("#d3dce4")
HL = HexColor("#f4d35e")
TEAL = HexColor("#1f7a74")
CODE_BG = HexColor("#f2f5f8")
MAX_TOC_FILES = 400  # list files in the contents only when there aren't too many


# ---------------------------------------------------------------- fonts
_FACES = {
    "sans": ["DejaVuSans.ttf"],
    "sans-bold": ["DejaVuSans-Bold.ttf"],
    "sans-italic": ["DejaVuSans-Oblique.ttf"],
    "sans-bolditalic": ["DejaVuSans-BoldOblique.ttf"],
    "mono": ["DejaVuSansMono.ttf"],
    "mono-bold": ["DejaVuSansMono-Bold.ttf"],
}
# for scripts DejaVu doesn't cover (Chinese, Japanese, Korean, Indian scripts, ...): TrueType fonts only,
# since reportlab can't draw the PostScript-outline kind most Noto CJK files use
_FALLBACKS = ["DroidSansFallbackFull.ttf", "DroidSansFallback.ttf", "wqy-microhei.ttc", "wqy-zenhei.ttc",
              "ipag.ttf", "ipagp.ttf", "NanumGothic.ttf", "NotoSansDevanagari-Regular.ttf", "Lohit-Devanagari.ttf",
              "NotoSansTamil-Regular.ttf", "NotoSansBengali-Regular.ttf", "FreeSans.ttf", "unifont.ttf"]
_VERA = {"sans": "Vera.ttf", "sans-bold": "VeraBd.ttf", "sans-italic": "VeraIt.ttf", "sans-bolditalic": "VeraBI.ttf"}


def _font_dirs() -> list[Path]:
    home = Path.home()
    dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local/share/fonts", home / ".fonts",
            Path("/Library/Fonts"), home / "Library/Fonts", Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    try:  # matplotlib ships DejaVu
        spec = importlib.util.find_spec("matplotlib")
        if spec and spec.origin:
            dirs.append(Path(spec.origin).parent / "mpl-data/fonts/ttf")
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=1)
def fonts() -> dict[str, str]:
    """Register the fonts for the document; returns face -> registered name. DejaVu covers most scripts;
    without it, the Vera fonts that come with reportlab and Courier are used."""
    wanted = {n for names in _FACES.values() for n in names} | set(_FALLBACKS)
    found: dict[str, Path] = {}
    for d in _font_dirs():
        for root, _, files in os.walk(d):
            for f in files:
                if f in wanted and f not in found:
                    found[f] = Path(root) / f
            if len(found) == len(wanted):
                break
    import reportlab
    vera_dir = Path(reportlab.__file__).parent / "fonts"
    names: dict[str, str] = {}
    for face, files in _FACES.items():
        path = next((found[f] for f in files if f in found), None)
        if path is None and face in _VERA and (vera_dir / _VERA[face]).exists():
            path = vera_dir / _VERA[face]
        if path is None:
            names[face] = {"mono": "Courier", "mono-bold": "Courier-Bold"}.get(face, "Helvetica")
            continue
        reg = f"TheReader-{face}"
        try:
            pdfmetrics.registerFont(TTFont(reg, str(path)))
            names[face] = reg
        except Exception:
            names[face] = {"mono": "Courier", "mono-bold": "Courier-Bold"}.get(face, "Helvetica")
    pdfmetrics.registerFontFamily("TheReaderSans", normal=names["sans"], bold=names["sans-bold"],
                                  italic=names["sans-italic"], boldItalic=names["sans-bolditalic"])
    pdfmetrics.registerFontFamily("TheReaderMono", normal=names["mono"], bold=names["mono-bold"],
                                  italic=names["mono"], boldItalic=names["mono-bold"])
    names["unicode"] = "1" if names["mono"].startswith("TheReader") else ""
    for k, f in enumerate(f for f in _FALLBACKS if f in found):
        reg = f"TheReader-fallback{k}"
        try:
            font = TTFont(reg, str(found[f]), **({"subfontIndex": 0} if f.endswith(".ttc") else {}))
            pdfmetrics.registerFont(font)
            _fallbacks.append((reg, font.face.charToGlyph))
        except Exception:
            continue
    for face in ("sans", "mono"):
        try:
            _covered[face] = pdfmetrics.getFont(names[face]).face.charToGlyph
        except AttributeError:  # a built-in font: Latin-1
            _covered[face] = {c: 1 for c in range(256)}
    return names


_fallbacks: list[tuple[str, dict]] = []
_covered: dict[str, dict] = {}


def _mixed(text: str, mono: bool = False) -> str:
    """Escaped text, with characters the main font lacks set in a fallback font that has them."""
    text = _clean(text, mono)
    fonts()
    have = _covered["mono" if mono else "sans"]
    if all(ord(ch) in have or ch in "\n\t" for ch in text):
        return escape(text)
    out, run, run_font = [], [], None

    def flush():
        if run:
            t = escape("".join(run))
            out.append(f'<font name="{run_font}">{t}</font>' if run_font else t)
            run.clear()

    for ch in text:
        font = None
        if ord(ch) not in have and ch not in "\n\t":
            font = next((name for name, cmap in _fallbacks if ord(ch) in cmap), None)
        if font != run_font:
            flush()
            run_font = font
        run.append(ch)
    flush()
    return "".join(out)


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text: str, mono: bool = False) -> str:
    text = _CONTROL.sub("", text)
    if mono and not fonts()["unicode"]:  # Courier only knows Latin-1
        text = text.encode("latin-1", "replace").decode("latin-1")
    return text


# ---------------------------------------------------------------- styles
def _styles() -> dict[str, ParagraphStyle]:
    f = fonts()
    base = ParagraphStyle("body", fontName=f["sans"], fontSize=10, leading=14.5, textColor=INK, alignment=TA_LEFT,
                          spaceAfter=6, splitLongWords=True)
    return {
        "body": base,
        "item": ParagraphStyle("item", parent=base, spaceAfter=3),
        "code": ParagraphStyle("code", parent=base, fontName=f["mono"], fontSize=8.3, leading=11.2, textColor=INK2,
                               backColor=CODE_BG, borderPadding=(5, 6, 5, 6), spaceBefore=4, spaceAfter=10,
                               leftIndent=6, rightIndent=6),
        "h-inner": ParagraphStyle("h-inner", parent=base, fontName=f["sans-bold"], fontSize=10.5, spaceBefore=4,
                                  spaceAfter=4, keepWithNext=1),
        "h1": ParagraphStyle("h1", parent=base, fontName=f["sans-bold"], fontSize=17, leading=22, spaceBefore=4,
                             spaceAfter=2, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=base, fontName=f["sans-bold"], fontSize=12.5, leading=17, spaceBefore=14,
                             spaceAfter=1, keepWithNext=1),
        "h3": ParagraphStyle("h3", parent=base, fontName=f["sans-bold"], fontSize=10.5, leading=14, spaceBefore=10,
                             spaceAfter=1, keepWithNext=1),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=7,
                               keepWithNext=1),
        "title": ParagraphStyle("title", parent=base, fontName=f["sans-bold"], fontSize=30, leading=36, spaceAfter=8),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=13, leading=18, textColor=INK2, spaceAfter=28),
        "fact-k": ParagraphStyle("fact-k", parent=base, fontSize=9, textColor=MUTED, spaceAfter=0),
        "fact-v": ParagraphStyle("fact-v", parent=base, fontSize=9.5, spaceAfter=0),
        "note": ParagraphStyle("note", parent=base, fontName=f["sans-italic"], fontSize=9, textColor=INK2),
        "toc-title": ParagraphStyle("toc-title", parent=base, fontName=f["sans-bold"], fontSize=17, leading=22,
                                    spaceAfter=14),
        "toc0": ParagraphStyle("toc0", parent=base, fontName=f["sans-bold"], fontSize=10, leading=14, spaceBefore=5,
                               spaceAfter=1),
        "toc1": ParagraphStyle("toc1", parent=base, fontName=f["mono"], fontSize=8.5, leading=12, leftIndent=14,
                               spaceAfter=0, textColor=INK2),
    }


def _runs(runs: list[Run], size: float = 10) -> str:
    f = fonts()
    out = []
    for r in runs:
        t = _mixed(r.text, r.code)
        if r.code:
            t = f'<font name="{f["mono"]}" size="{size * 0.9:.1f}" color="#2a3a4c">{t}</font>'
        if r.bold:
            t = f"<b>{t}</b>"
        if r.italic:
            t = f"<i>{t}</i>"
        if r.link:
            t = f'<a href="{escape(r.link, {chr(34): "&quot;"})}" color="#1f7a74">{t}</a>'
        out.append(t)
    return "".join(out)


def _code_markup(text: str) -> str:
    lines = []
    for line in text.expandtabs(4).split("\n"):
        stripped = line.lstrip(" ")
        body = re.sub(r" {2,}", lambda m: " " + "&nbsp;" * (len(m.group()) - 1), _mixed(stripped, True))
        lines.append("&nbsp;" * (len(line) - len(stripped)) + body)
    return "<br/>".join(lines) or "&nbsp;"


def _blocks(blocks: list[Block], st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    for b in blocks:
        if b.kind == "p":
            out.append(Paragraph(_runs(b.runs, st["body"].fontSize), st["body"]))
        elif b.kind == "h":
            out.append(Paragraph(_runs(b.runs, st["h-inner"].fontSize), st["h-inner"]))
        elif b.kind == "item":
            indent = 14 + b.level * 14
            style = ParagraphStyle(f"item{b.level}", parent=st["item"], leftIndent=indent, bulletIndent=indent - 11,
                                   bulletFontName=fonts()["sans"])
            marker = escape(b.marker) if b.marker != "•" else ("•" if b.level == 0 else "◦")
            out.append(Paragraph(_runs(b.runs, style.fontSize), style, bulletText=marker))
        elif b.kind == "code":
            out.append(Paragraph(_code_markup(b.text), st["code"]))
    return out


# ---------------------------------------------------------------- page furniture
class _Heading(Paragraph):
    """A section title that also becomes a bookmark and a contents entry."""

    def __init__(self, text: str, style: ParagraphStyle, outline: str, level: int, key: str, toc: bool):
        super().__init__(text, style)
        self.outline, self.level, self.key, self.toc = outline, level, key, toc


class _Mark(Flowable):
    """The three-bar mark of The Reader."""

    def wrap(self, *_):
        return 34, 30

    def draw(self):
        c = self.canv
        for y, w, color in ((24, 34, HL), (13, 22, RULE), (2, 29, RULE)):
            c.setFillColor(color)
            c.roundRect(0, y, w, 6, 3, stroke=0, fill=1)


class _Rule(Flowable):
    def wrap(self, aw, ah):
        self.width = aw
        return aw, 8

    def draw(self):
        self.canv.setStrokeColor(RULE)
        self.canv.setLineWidth(0.6)
        self.canv.line(0, 4, self.width, 4)


class _Doc(BaseDocTemplate):
    def __init__(self, path: str, doc: Document, **kw):
        super().__init__(path, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm,
                         bottomMargin=20 * mm, title=f"{doc.title}: the code, explained", author="The Reader",
                         subject="A layered explanation of a codebase", creator="The Reader", **kw)
        self.source = doc
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate("cover", [frame], onPage=lambda c, d: None),
                               PageTemplate("page", [frame], onPage=self._furniture)])

    def _furniture(self, canv, _doc):
        f = fonts()
        canv.saveState()
        canv.setFont(f["sans"], 8)
        canv.setFillColor(MUTED)
        canv.drawString(self.leftMargin, 11 * mm, _clean(self.source.title))
        canv.drawRightString(self.leftMargin + self.width, 11 * mm, str(canv.getPageNumber()))
        canv.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, _Heading):
            self.canv.bookmarkPage(flowable.key)
            self.canv.addOutlineEntry(flowable.outline, flowable.key, level=flowable.level, closed=flowable.level > 0)
            if flowable.toc:
                self.notify("TOCEntry", (flowable.level, _mixed(flowable.outline), self.page, flowable.key))


# ---------------------------------------------------------------- the document
def _title_page(doc: Document, st: dict) -> list[Flowable]:
    out: list[Flowable] = [Spacer(1, 40 * mm), _Mark(), Spacer(1, 14),
                           Paragraph(_mixed(doc.title), st["title"]),
                           Paragraph("The code, explained layer by layer", st["subtitle"])]
    rows = [[Paragraph(escape(k), st["fact-k"]), Paragraph(_mixed(v), st["fact-v"])] for k, v in doc.facts]
    if rows:
        t = Table(rows, colWidths=[30 * mm, None], hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
        out.append(t)
    for n in doc.notes:
        out += [Spacer(1, 8), Paragraph(_mixed(n), st["note"])]
    out += [Spacer(1, 30), Paragraph("Made with The Reader", st["meta"])]
    return out


def _section(s: Section, st: dict, toc_files: bool) -> list[Flowable]:
    out: list[Flowable] = []
    title = _mixed(s.title, s.mono)
    if s.mono:
        title = f'<font name="{fonts()["mono-bold"]}">{title}</font>'
    if s.level == 1:
        out += [CondPageBreak(70 * mm), Spacer(1, 6), _Rule()]
    style = st[f"h{s.level}"]
    out.append(_Heading(title, style, _clean(s.title), s.level - 1, s.anchor,
                        toc=s.level == 1 or (s.level == 2 and toc_files)))
    meta = _mixed(s.meta) + (' · <font color="#1f7a74">written by AI</font>' if s.ai else "")
    if meta:
        out.append(Paragraph(meta, st["meta"]))
    out += _blocks(s.blocks, st)
    return out


def write_pdf(doc: Document, path: str | Path, progress=None) -> None:
    st = _styles()
    story: list[Flowable] = _title_page(doc, st)
    toc = TableOfContents(levelStyles=[st["toc0"], st["toc1"]], dotsMinLevel=0)
    toc_files = sum(1 for s in doc.sections if s.level == 2) <= MAX_TOC_FILES
    story += [NextPageTemplate("page"), PageBreak(), Paragraph("Contents", st["toc-title"]), toc, PageBreak()]
    for s in doc.sections:
        story += _section(s, st, toc_files)
    pdf = _Doc(str(path), doc)
    pdf.multiBuild(story, maxPasses=4)
