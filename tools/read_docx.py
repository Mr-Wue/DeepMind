"""
Thin .docx reader — extracts structured text only.
All entity extraction intelligence lives in SKILL.md.
"""

from pathlib import Path

from docx import Document
from langchain_core.tools import tool


def _resolve_path(file_path: str) -> Path:
    """解析文件路径，兼容 deepagents 虚拟路径（以 / 开头）和绝对/相对路径。"""
    from utils.paths import PROJECT_ROOT

    path = Path(file_path)
    if file_path.startswith("/") and not path.exists():
        resolved = (PROJECT_ROOT / file_path.lstrip("/")).resolve()
        if resolved.exists():
            return resolved
    return path


@tool
def read_docx(file_path: str) -> str:
    """Read a .docx file and return its content as structured markdown.

    Heading styles (Heading 1/2/3) are converted to markdown headings (#/##/###).
    Body paragraphs are preserved as-is.
    Use this before extracting entities from requirement documents.

    Args:
        file_path: Absolute or relative path to the .docx file.
    """
    path = _resolve_path(file_path)
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
