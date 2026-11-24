from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re

from app.azure_search import SearchDocument
from app.config import Settings
from app.document_reader import SUPPORTED_IMAGE_EXTENSIONS, extract_text_from_path


@dataclass
class LocalDocumentStore:
    settings: Settings

    def __post_init__(self) -> None:
        self.base_path = Path(self.settings.local_docs_path)
        if not self.base_path.exists():
            raise RuntimeError(
                f"La carpeta local no existe: {self.base_path}. "
                "Actualiza LOCAL_DOCS_PATH en tu archivo .env."
            )
        self._documents = self._scan_documents()
        self._content_cache: dict[str, str] = {}

    def search(self, query: str, top: int | None = None, filter_expression: str | None = None) -> list[SearchDocument]:
        del filter_expression
        terms = [term.lower() for term in query.split() if term.strip()]
        scored: list[tuple[int, SearchDocument]] = []
        for doc in self._documents:
            haystack = " ".join([doc.title, doc.account_id, doc.status]).lower()
            score = sum(1 for term in terms if term in haystack)
            if query == "*" or score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        limit = top or self.settings.search_top_k
        return [self._enrich_document(doc) for _, doc in scored[:limit]]

    def recent_accounts(self, days: int = 30, top: int | None = None) -> list[SearchDocument]:
        threshold = datetime.now() - timedelta(days=days)
        docs = [doc for doc in self._documents if self._parse_date(doc.updated_at) >= threshold]
        docs.sort(key=lambda doc: doc.updated_at, reverse=True)
        limit = top or self.settings.search_top_k
        return docs[:limit]

    def all_documents(self) -> list[SearchDocument]:
        return [self._enrich_document(doc) for doc in self._documents]

    def _scan_documents(self) -> list[SearchDocument]:
        documents: list[SearchDocument] = []
        patterns = ["*.pdf", *[f"*{ext}" for ext in sorted(SUPPORTED_IMAGE_EXTENSIONS)]]
        candidate_paths: list[Path] = []
        for pattern in patterns:
            candidate_paths.extend(self.base_path.rglob(pattern))

        for document_path in sorted({path for path in candidate_paths}):
            stat = document_path.stat()
            account_id = document_path.stem.split("_")[-1]
            updated_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            status = self._infer_status(document_path.name, "")
            documents.append(
                SearchDocument(
                    title=document_path.name,
                    content="",
                    updated_at=updated_at,
                    status=status,
                    patient_name="",
                    account_id=account_id,
                    score=None,
                    raw={"path": str(document_path), "source": "local"},
                )
            )
        return documents

    def _enrich_document(self, doc: SearchDocument) -> SearchDocument:
        path = doc.raw.get("path", "")
        if not path:
            return doc

        if path not in self._content_cache:
            self._content_cache[path] = self._extract_text(Path(path))

        content = self._content_cache[path]
        metadata = self._extract_metadata(doc.title, content)
        return SearchDocument(
            title=doc.title,
            content=content,
            updated_at=doc.updated_at,
            status=self._infer_status(doc.title, content),
            patient_name=doc.patient_name,
            account_id=doc.account_id,
            score=doc.score,
            raw={**doc.raw, **metadata},
        )

    def _extract_text(self, document_path: Path) -> str:
        return extract_text_from_path(
            document_path,
            self.settings.local_pdf_text_limit,
            self.settings.pdf_content_char_limit,
        )

    def _infer_status(self, filename: str, content: str) -> str:
        haystack = f"{filename} {content}".lower()
        if "dev" in haystack or "devol" in haystack:
            return "devuelta"
        if "glosa" in haystack:
            return "glosada"
        if "rechazo" in haystack:
            return "rechazada"
        return "pendiente de revision"

    def _parse_date(self, value: str) -> datetime:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min

    def _extract_metadata(self, filename: str, content: str) -> dict[str, str]:
        normalized = " ".join(content.split())
        return {
            "doc_type": filename.split("_", 1)[0].upper(),
            "payer_id": self._extract_filename_segment(filename, 1),
            "invoice_number": self._extract_invoice_number(filename, normalized),
            "cups_code": self._extract_cups_code(normalized),
            "authorization_number": self._extract_authorization_number(normalized),
            "patient_name": self._extract_patient_name(normalized),
        }

    def _extract_filename_segment(self, filename: str, index: int) -> str:
        parts = Path(filename).stem.split("_")
        return parts[index].strip() if len(parts) > index else ""

    def _extract_invoice_number(self, filename: str, content: str) -> str:
        from_filename = self._extract_filename_segment(filename, 2)
        if from_filename:
            return from_filename
        match = re.search(r"\bFE\s*-?\s*(\d{4,})\b", content, flags=re.IGNORECASE)
        return f"FE{match.group(1)}" if match else ""

    def _extract_cups_code(self, content: str) -> str:
        patterns = [
            r"\bCUPS[:\s-]*(\d{5,8})\b",
            r"\bCODIGO\s+CUPS[:\s-]*(\d{5,8})\b",
            r"\bCUPS\s+No\.?[:\s-]*(\d{5,8})\b",
            r"\bCODIGO(?:\s+SERVICIO)?[:\s-]*(\d{5,8})\b",
            r"\bPROC(?:EDIMIENTO)?[:\s-]*(\d{5,8})\b",
            r"\bSERVICIO[:\s-]*(\d{5,8})\b",
            r"\b(\d{5,8})\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s()/-]{8,}\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def _extract_authorization_number(self, content: str) -> str:
        patterns = [
            r"autorizaci[oó]n[:\s]*(\d{6,12})",
            r"n[uú]mero de autorizaci[oó]n[:\s]*(\d{6,12})",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def _extract_patient_name(self, content: str) -> str:
        patterns = [
            r"PACIENTE[:\s]*([A-ZÁÉÍÓÚÑ ]{6,}?)(?:\s+DOC[:\s]|\s+DESCUENTO[:\s]|\s+IVA[:\s]|$)",
            r"Yo,\s*([A-ZÁÉÍÓÚÑ ]{6,}),\s*mayor de edad",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                return " ".join(match.group(1).split())
        return ""
