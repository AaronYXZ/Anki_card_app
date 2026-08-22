from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape
from xml.etree import ElementTree

from latex2mathml.converter import convert as latex_to_mathml
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererProtocol
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

_BARE_MATH_PATTERN = re.compile(r"^[A-Za-z0-9\\{}_^+\-*/=().,|\s]+$")
_LATEX_COMMAND_PATTERN = re.compile(r"\\[A-Za-z]+")
_MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
_ALLOWED_MATHML_TAGS = frozenset(
    {
        "math",
        "menclose",
        "mfrac",
        "mi",
        "mn",
        "mo",
        "mover",
        "mpadded",
        "mphantom",
        "mroot",
        "mrow",
        "mspace",
        "msqrt",
        "mstyle",
        "msub",
        "msubsup",
        "msup",
        "mtable",
        "mtd",
        "mtext",
        "mtr",
        "munder",
        "munderover",
    }
)
_ALLOWED_MATHML_ATTRIBUTES = frozenset(
    {
        "accent",
        "columnalign",
        "columnlines",
        "columnspacing",
        "depth",
        "display",
        "displaystyle",
        "fence",
        "form",
        "height",
        "largeop",
        "linebreak",
        "linethickness",
        "lspace",
        "mathbackground",
        "mathsize",
        "mathvariant",
        "maxsize",
        "minsize",
        "movablelimits",
        "notation",
        "rowspacing",
        "rspace",
        "scriptlevel",
        "separator",
        "stretchy",
        "voffset",
        "width",
    }
)
ElementTree.register_namespace("", _MATHML_NAMESPACE)


def _highlight_code(code: str, language: str, attributes: str) -> str:
    del attributes
    if not language:
        return ""
    try:
        lexer = get_lexer_by_name(language)
    except ClassNotFound:
        return ""
    highlighted = highlight(code, lexer, HtmlFormatter(nowrap=True))
    language_class = escape(language, quote=True)
    return (
        f'<pre class="highlight"><code class="language-{language_class}">{highlighted}</code></pre>'
    )


def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _math_inline(state: StateInline, silent: bool) -> bool:
    start = state.pos
    if state.src.startswith(r"\(", start) and not _is_escaped(state.src, start):
        opening, closing = r"\(", r"\)"
    elif (
        state.src.startswith("$", start)
        and not state.src.startswith("$$", start)
        and not _is_escaped(state.src, start)
    ):
        opening = closing = "$"
    else:
        return False

    content_start = start + len(opening)
    search_position = content_start
    while True:
        end = state.src.find(closing, search_position)
        if end < 0:
            return False
        if not _is_escaped(state.src, end):
            break
        search_position = end + len(closing)

    content = state.src[content_start:end]
    if not content.strip():
        return False
    if not silent:
        token = state.push("math_inline", "math", 0)
        token.content = content
        token.markup = opening
    state.pos = end + len(closing)
    return True


def _looks_like_bare_math(value: str) -> bool:
    return bool(
        value
        and _BARE_MATH_PATTERN.fullmatch(value)
        and _LATEX_COMMAND_PATTERN.search(value)
        and any(operator in value for operator in ("=", "_", "^"))
    )


def _math_block(state: StateBlock, start_line: int, end_line: int, silent: bool) -> bool:
    if state.sCount[start_line] - state.blkIndent >= 4:
        return False
    start = state.bMarks[start_line] + state.tShift[start_line]
    line_end = state.eMarks[start_line]
    line = state.src[start:line_end].strip()

    if _looks_like_bare_math(line):
        if not silent:
            token = state.push("math_block", "math", 0)
            token.block = True
            token.content = line
            token.markup = "bare-latex"
            token.map = [start_line, start_line + 1]
        state.line = start_line + 1
        return True

    if line.startswith("$$"):
        opening, closing = "$$", "$$"
    elif line.startswith(r"\["):
        opening, closing = r"\[", r"\]"
    else:
        return False

    content_start = start + state.src[start:line_end].find(opening) + len(opening)
    close_position = state.src.find(closing, content_start, line_end)
    closing_line = start_line
    if close_position < 0:
        for next_line in range(start_line + 1, end_line):
            next_start = state.bMarks[next_line] + state.tShift[next_line]
            next_end = state.eMarks[next_line]
            close_position = state.src.find(closing, next_start, next_end)
            if close_position >= 0:
                closing_line = next_line
                break
    if close_position < 0:
        return False

    content = state.src[content_start:close_position].strip()
    if not content:
        return False
    if not silent:
        token = state.push("math_block", "math", 0)
        token.block = True
        token.content = content
        token.markup = opening
        token.map = [start_line, closing_line + 1]
    state.line = closing_line + 1
    return True


def _convert_math(content: str, *, display: str) -> str:
    try:
        converted = latex_to_mathml(content.strip(), display=display)
        root = ElementTree.fromstring(converted)
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag not in _ALLOWED_MATHML_TAGS:
                raise ValueError(f"Unsupported MathML element: {tag}")
            for attribute in element.attrib:
                name = attribute.rsplit("}", 1)[-1]
                if name not in _ALLOWED_MATHML_ATTRIBUTES:
                    raise ValueError(f"Unsupported MathML attribute: {name}")
        return ElementTree.tostring(root, encoding="unicode")
    except Exception:
        return f'<code class="math-error">{escape(content)}</code>'


def _render_math_inline(
    self: RendererProtocol,
    tokens: Sequence[Token],
    index: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    del self, options, env
    return (
        f'<span class="math inline">{_convert_math(tokens[index].content, display="inline")}</span>'
    )


def _render_math_block(
    self: RendererProtocol,
    tokens: Sequence[Token],
    index: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    del self, options, env
    return (
        f'<div class="math block">{_convert_math(tokens[index].content, display="block")}</div>\n'
    )


_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "breaks": True,
        "html": False,
        "highlight": _highlight_code,
        "linkify": False,
        "typographer": False,
    },
)
_MARKDOWN.inline.ruler.before("escape", "math_inline", _math_inline)
_MARKDOWN.block.ruler.before("fence", "math_block", _math_block)
_MARKDOWN.add_render_rule("math_inline", _render_math_inline)
_MARKDOWN.add_render_rule("math_block", _render_math_block)


def render_markdown(value: str | None) -> Markup:
    """Render Markdown and allowlisted MathML while escaping embedded HTML."""
    return Markup(_MARKDOWN.render(value or ""))
