from __future__ import annotations

import io
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def is_supported_document(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix == ".pdf" or suffix in SUPPORTED_IMAGE_EXTENSIONS


def extract_text_from_bytes(filename: str, payload: bytes, page_limit: int, char_limit: int) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(payload, page_limit, char_limit)
    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return extract_image_text(payload, filename, char_limit)
    return f"Formato no soportado para extraccion de texto: {filename}"


def extract_text_from_path(file_path: Path, page_limit: int, char_limit: int) -> str:
    return extract_text_from_bytes(file_path.name, file_path.read_bytes(), page_limit, char_limit)


def extract_pdf_text(payload: bytes, page_limit: int, char_limit: int) -> str:
    if PdfReader is None:
        return "PDF sin extraccion de texto porque pypdf no esta instalado."

    try:
        reader = PdfReader(io.BytesIO(payload))
        page_text: list[str] = []
        pages = reader.pages if page_limit <= 0 else reader.pages[:page_limit]
        for page in pages:
            page_text.append((page.extract_text() or "").strip())
        merged = "\n".join(chunk for chunk in page_text if chunk)
        return merged[:char_limit] if merged else "PDF sin texto legible."
    except Exception:
        return "No se pudo extraer texto del PDF."


def extract_image_text(payload: bytes, filename: str, char_limit: int) -> str:
    if Image is None or pytesseract is None:
        return (
            f"Imagen sin OCR disponible: {filename}. "
            "Instala Pillow, pytesseract y el motor Tesseract OCR para extraer texto."
        )

    try:
        image = Image.open(io.BytesIO(payload))
        text = (pytesseract.image_to_string(image, lang="spa+eng") or "").strip()
        return text[:char_limit] if text else f"Imagen sin texto legible: {filename}"
    except Exception:
        return f"No se pudo extraer texto de la imagen: {filename}"
