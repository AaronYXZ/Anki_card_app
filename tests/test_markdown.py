from anki_card_app.markdown import render_markdown


def test_markdown_renderer_supports_structure_and_code() -> None:
    rendered = str(
        render_markdown(
            "## Recall\n\n**Important** and `inline`:\n\n"
            "- First\n- Second\n\n```python\nprint('safe')\n```"
        )
    )

    assert "<h2>Recall</h2>" in rendered
    assert "<strong>Important</strong>" in rendered
    assert "<code>inline</code>" in rendered
    assert "<ul>" in rendered
    assert '<code class="language-python">' in rendered


def test_markdown_renderer_escapes_html_and_rejects_unsafe_links() -> None:
    rendered = str(
        render_markdown(
            '<script>alert("x")</script>\n\n'
            '<img src=x onerror="alert(1)">\n\n'
            "[unsafe](javascript:alert(1))"
        )
    )

    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img src=x" in rendered
    assert "javascript:" in rendered
    assert "href=" not in rendered
