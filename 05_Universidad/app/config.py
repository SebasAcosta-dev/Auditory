from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_mode: str
    local_docs_path: str
    local_pdf_text_limit: int
    pdf_content_char_limit: int
    model_provider: str
    gemini_api_key: str
    gemini_model: str
    gemini_base_url: str
    azure_search_endpoint: str
    azure_search_api_key: str
    azure_search_index: str
    azure_search_api_version: str
    azure_storage_connection_string: str
    azure_storage_account_url: str
    azure_storage_account_name: str
    azure_storage_account_key: str
    azure_storage_documents_container: str
    azure_storage_reports_container: str
    content_field: str
    title_field: str
    date_field: str
    status_field: str
    patient_field: str
    account_field: str
    search_top_k: int
    report_output_dir: str


def _require(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise RuntimeError(f"Falta la variable de entorno requerida: {name}")
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_settings() -> Settings:
    local_docs_path = _optional("LOCAL_DOCS_PATH", "")
    if local_docs_path:
        local_docs_path = str(Path(local_docs_path))

    return Settings(
        app_mode=_require("APP_MODE", "azure").lower(),
        local_docs_path=local_docs_path,
        local_pdf_text_limit=int(_require("LOCAL_PDF_TEXT_LIMIT", "3")),
        pdf_content_char_limit=int(_require("PDF_CONTENT_CHAR_LIMIT", "50000")),
        model_provider=_require("MODEL_PROVIDER", "none").lower(),
        gemini_api_key=_optional("GEMINI_API_KEY"),
        gemini_model=_optional("GEMINI_MODEL", "gemini-1.5-flash"),
        gemini_base_url=_optional("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
        azure_search_endpoint=_optional("AZURE_SEARCH_ENDPOINT"),
        azure_search_api_key=_optional("AZURE_SEARCH_API_KEY"),
        azure_search_index=_optional("AZURE_SEARCH_INDEX"),
        azure_search_api_version=_require("AZURE_SEARCH_API_VERSION", "2024-07-01"),
        azure_storage_connection_string=_optional("AZURE_STORAGE_CONNECTION_STRING"),
        azure_storage_account_url=_optional("AZURE_STORAGE_ACCOUNT_URL"),
        azure_storage_account_name=_optional("AZURE_STORAGE_ACCOUNT_NAME"),
        azure_storage_account_key=_optional("AZURE_STORAGE_ACCOUNT_KEY"),
        azure_storage_documents_container=_require("AZURE_STORAGE_DOCUMENTS_CONTAINER", "files"),
        azure_storage_reports_container=_require("AZURE_STORAGE_REPORTS_CONTAINER", "reports"),
        content_field=_require("AZURE_SEARCH_CONTENT_FIELD", "content"),
        title_field=_require("AZURE_SEARCH_TITLE_FIELD", "title"),
        date_field=_require("AZURE_SEARCH_DATE_FIELD", "updated_at"),
        status_field=_require("AZURE_SEARCH_STATUS_FIELD", "status"),
        patient_field=_require("AZURE_SEARCH_PATIENT_FIELD", "patient_name"),
        account_field=_require("AZURE_SEARCH_ACCOUNT_FIELD", "account_id"),
        search_top_k=int(_require("SEARCH_TOP_K", "5")),
        report_output_dir=_require("REPORT_OUTPUT_DIR", "reports"),
    )
