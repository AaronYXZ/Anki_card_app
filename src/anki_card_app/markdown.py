from __future__ import annotations

from html import escape

from markdown_it import MarkdownIt
from markupsafe import Markup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound


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


def render_markdown(value: str | None) -> Markup:
    """Render trusted Markdown structure while escaping embedded HTML."""
    return Markup(_MARKDOWN.render(value or ""))
