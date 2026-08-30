"""Document export: the markdown a model writes must survive into every format.

The rule these tests exist to protect is that an export never SILENTLY drops
content. A table that vanishes from the Word file the user sends to their
supervisor is worse than an export button that fails loudly.
"""

import pytest

from omnix.tools import docgen

RICH = """# Title

A **bold** word, *italic*, `inline()` and a [link](https://example.com).

## Section

| Country   | Share |
|-----------|-------|
| Chile     | 36%   |
| Australia | 24%   |

- first
- second

1. step one
2. step two

> A quoted line.

```python
def f():
    return 1
```

---

Trailing paragraph.
"""


def test_parses_every_block_kind():
    kinds = [b.kind for b in docgen.parse_blocks(RICH)]
    assert kinds == ["h1", "p", "h2", "table", "ul", "ol", "quote", "code",
                     "rule", "p"]


def test_table_keeps_its_cells():
    table = next(b for b in docgen.parse_blocks(RICH) if b.kind == "table")
    assert table.rows[0] == ["Country", "Share"]
    assert ["Chile", "36%"] in table.rows
    assert len(table.rows) == 3


def test_code_block_keeps_indentation():
    code = next(b for b in docgen.parse_blocks(RICH) if b.kind == "code")
    assert code.lang == "python"
    assert code.text == "def f():\n    return 1"


def test_list_continuation_joins_the_item_above():
    blocks = docgen.parse_blocks("- a bullet that\n  wraps over lines\n- second")
    ul = next(b for b in blocks if b.kind == "ul")
    assert ul.items == ["a bullet that wraps over lines", "second"]


@pytest.mark.parametrize("fmt", docgen.FORMATS)
def test_every_format_renders_non_empty_bytes(fmt):
    blob = docgen.render(RICH, fmt, "Test document", "subtitle")
    assert isinstance(blob, bytes)
    # Even the smallest format here (txt) carries the whole document.
    assert len(blob) > 200


def test_pdf_and_docx_have_the_right_magic_bytes():
    assert docgen.render(RICH, "pdf", "T").startswith(b"%PDF")
    # .docx is a zip container.
    assert docgen.render(RICH, "docx", "T").startswith(b"PK")


def test_plain_text_keeps_the_content_and_drops_the_syntax():
    txt = docgen.strip_markdown(RICH)
    for must in ("TITLE", "Chile", "36%", "first", "step one", "def f():"):
        assert must in txt, f"{must!r} was dropped"
    assert "**" not in txt
    assert "|" not in txt          # the table became tab-separated


def test_html_escapes_model_authored_markup():
    """A model can emit anything; the export must not become an injection."""
    html = docgen.render('He said <script>alert(1)</script> & "quoted".',
                         "html", "T").decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_pdf_survives_angle_brackets():
    """reportlab parses a mini-HTML dialect, so an unescaped '<' is a crash."""
    blob = docgen.render("Compare 5 < 10 & <b>tags</b> in text.", "pdf", "T")
    assert blob.startswith(b"%PDF")


def test_inline_runs_splits_styles():
    runs = docgen.inline_runs("plain **bold** `code` *it*")
    styles = [s for _, s in runs if s]
    assert "b" in styles and "code" in styles and "i" in styles


def test_link_keeps_its_url_in_print():
    # A printed page cannot be clicked, so a bare label loses the reference.
    runs = docgen.inline_runs("see [docs](https://x.com)")
    assert any("https://x.com" in t for t, _ in runs)


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        docgen.render("x", "exe", "T")


def test_filename_is_safe_and_bounded():
    assert docgen.filename('Report: "Q3" / final', "pdf") == "report-q3-final.pdf"
    assert docgen.filename("", "docx") == "omnix.docx"
    long = docgen.filename("word " * 50, "md")
    assert len(long) <= 64 and long.endswith(".md")


def test_empty_markdown_still_renders():
    """The endpoint rejects empty input, but the renderer must not explode if
    something upstream lets a whitespace-only answer through."""
    for fmt in docgen.FORMATS:
        assert docgen.render("   ", fmt, "Empty")
