"""Incoming documents: convert PDF/Office files to Markdown the agent can read.

WeChat file messages land in the vault via media_in.save_file. For document
types a backend can't read natively — PDF on the API backend, and .docx/.xlsx/
.pptx on *both* — we extract a Markdown sibling with MarkItDown, so whichever
agent runs just reads plain text. PDF is included too so the API backend gets
its text; the Claude backend can still read the original PDF directly (bot.py
hands it both paths).

Best-effort by design: if the `docs` extra (MarkItDown) isn't installed, or a
file won't convert, extract_markdown() returns None and bot.py falls back to
handing the agent the original file with "read it if you can".
"""

from pathlib import Path

# Extensions we convert to Markdown at ingestion. Keep in sync with the README
# and the `docs` pyproject extra (markitdown[pdf,docx,pptx,xlsx]).
CONVERTIBLE = {".pdf", ".docx", ".xlsx", ".pptx"}


def is_convertible(path: Path) -> bool:
    return path.suffix.lower() in CONVERTIBLE


def extract_markdown(path: Path) -> Path | None:
    """Write `<name>.md` beside `path` holding its extracted text, and return
    that path — or None if the type isn't convertible, MarkItDown is missing, or
    extraction produced nothing."""
    if not is_convertible(path):
        return None
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None
    try:
        text = MarkItDown().convert(str(path)).text_content
    except Exception:  # malformed/encrypted/unsupported — fall back to the original
        return None
    if not text or not text.strip():
        return None
    out = path.with_name(path.name + ".md")  # keeps the original: sample.docx.md
    try:
        out.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return out
