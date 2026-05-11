"""
Thin .docx reader — extracts structured text only.
All entity extraction intelligence lives in SKILL.md.
"""

from pathlib import Path

from docx import Document
from langchain_core.tools import tool


@tool
def read_docx(file_path: str) -> str:
    """Read a .docx file and return its content as structured markdown.

    Heading styles (Heading 1/2/3) are converted to markdown headings (#/##/###).
    Body paragraphs are preserved as-is.
    Use this before extracting entities from requirement documents.

    Args:
        file_path: Absolute or relative path to the .docx file.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"

    doc = Document(str(path))
    lines: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else ""

        if "Heading 1" in style:
            lines.append(f"\n# {text}\n")
        elif "Heading 2" in style:
            lines.append(f"\n## {text}\n")
        elif "Heading 3" in style:
            lines.append(f"\n### {text}\n")
        elif "Heading" in style:
            lines.append(f"\n### {text}\n")
        else:
            lines.append(text)

    result = "\n".join(lines)
    if not result.strip():
        return f"Warning: no text extracted from {file_path}. File may be empty or use non-standard styles."

    return result
