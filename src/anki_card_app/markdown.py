from __future__ import annotations

from markdown_it import MarkdownIt
from markupsafe import Markup

_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "breaks": True,
        "html": False,
        "linkify": False,
        "typographer": False,
    },
)


def render_markdown(value: str | None) -> Markup:
    """Render trusted Markdown structure while escaping embedded HTML."""
    return Markup(_MARKDOWN.render(value or ""))
