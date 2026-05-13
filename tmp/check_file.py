from pathlib import Path
p = Path("/docs/req/输入1-用户需求.docx")
print(f"Exists: {p.exists()}")
print(f"Absolute: {p.absolute()}")
print(f"Size: {p.stat().st_size if p.exists() else 'N/A'}")
