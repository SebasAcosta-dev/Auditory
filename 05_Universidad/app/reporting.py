from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
import re


def build_report_file_path(output_dir: str, title: str, extension: str) -> Path:
    report_dir = Path(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(char.lower() if char.isalnum() else "-" for char in title).strip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return report_dir / f"{timestamp}-{safe_title or 'reporte'}.{extension}"


def save_markdown_report(output_dir: str, title: str, body: str) -> Path:
    file_path = build_report_file_path(output_dir, title, "md")
    content = f"# {title}\n\n{body.strip()}\n"
    file_path.write_text(content, encoding="utf-8")
    return file_path


def save_html_report(output_dir: str, title: str, body_html: str, template_path: Path | None = None) -> Path:
    file_path = build_report_file_path(output_dir, title, "html")
    content = render_html_document(title, body_html, template_path)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def render_html_document(title: str, body_html: str, template_path: Path | None = None) -> str:
    if template_path and template_path.exists():
        template = template_path.read_text(encoding="utf-8", errors="ignore")
        template = re.sub(r"<title>.*?</title>", f"<title>{escape(title)}</title>", template, count=1, flags=re.IGNORECASE | re.DOTALL)
        body_match = re.search(r"<body[^>]*>", template, flags=re.IGNORECASE)
        end_match = re.search(r"</body>", template, flags=re.IGNORECASE)
        if body_match and end_match:
            start = body_match.end()
            end = end_match.start()
            return template[:start] + "\n" + body_html + "\n" + template[end:]

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; padding: 2rem; background: #f6f8fb; color: #1a2233; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
  </style>
</head>
<body>
  <div class="container">
    {body_html}
  </div>
</body>
</html>"""
