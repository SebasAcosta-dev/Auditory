from __future__ import annotations

import base64
import io
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests
from azure.storage.blob import BlobServiceClient

from app.config import Settings, load_settings
from app.document_reader import extract_text_from_bytes, is_supported_document


INDEX_BATCH_SIZE = 100


@dataclass(frozen=True)
class IndexedDocument:
    doc_id: str
    title: str
    content: str
    updated_at: str
    status: str
    patient_name: str
    account_id: str
    payer_id: str
    cups_code: str
    authorization_number: str


def run() -> None:
    settings = load_settings()
    documents = collect_documents_from_blob_storage(settings)
    if not documents:
        print("No se encontraron documentos compatibles para indexar.")
        return

    print(f"Total de documentos preparados para indexar: {len(documents)}")
    upload_documents_to_search(settings, documents)
    print(f"Documentos indexados correctamente: {len(documents)}")


def collect_documents_from_blob_storage(settings: Settings) -> list[IndexedDocument]:
    client = _build_blob_service_client(settings)
    container = client.get_container_client(settings.azure_storage_documents_container)
    documents: list[IndexedDocument] = []
    blobs = list(container.list_blobs())
    total_blobs = len(blobs)
    print(f"Blobs detectados en Azure Storage: {total_blobs}")

    for blob_index, blob in enumerate(blobs, start=1):
        blob_name = blob.name
        lower_name = blob_name.lower()
        blob_client = container.get_blob_client(blob_name)
        updated_at = blob.last_modified.strftime("%Y-%m-%d %H:%M:%S") if blob.last_modified else ""

        print(f"Procesando blob {blob_index}/{total_blobs}: {blob_name}")

        if is_supported_document(blob_name):
            payload = blob_client.download_blob().readall()
            documents.append(build_indexed_document(blob_name, Path(blob_name).name, payload, updated_at))
            continue

        if lower_name.endswith(".zip"):
            documents.extend(collect_documents_from_zip(blob_client, blob_name, updated_at, settings))

    return documents


def collect_documents_from_zip(blob_client, blob_name: str, updated_at: str, settings: Settings) -> list[IndexedDocument]:
    documents: list[IndexedDocument] = []
    print(f"Descargando ZIP desde Azure Blob: {blob_name}")
    with tempfile.NamedTemporaryFile(suffix=".zip") as temp_file:
        download_stream = blob_client.download_blob(max_concurrency=4)
        for chunk in download_stream.chunks():
            temp_file.write(chunk)
        temp_file.flush()

        with zipfile.ZipFile(temp_file.name) as archive:
            members = archive.namelist()
            supported_members = [
                member_name
                for member_name in members
                if not member_name.endswith("/") and is_supported_document(member_name)
            ]
            total_supported = len(supported_members)
            print(f"Archivos compatibles encontrados dentro del ZIP: {total_supported}")
            for member_index, member_name in enumerate(supported_members, start=1):
                if member_index == 1 or member_index % 100 == 0 or member_index == total_supported:
                    print(f"Leyendo ZIP {blob_name}: {member_index}/{total_supported}")
                with archive.open(member_name) as member:
                    payload = member.read()
                source_id = f"{blob_name}::{member_name}"
                title = Path(member_name).name
                documents.append(build_indexed_document(source_id, title, payload, updated_at, settings))

    return documents


def build_indexed_document(
    source_id: str,
    title: str,
    payload: bytes,
    updated_at: str,
    settings: Settings | None = None,
) -> IndexedDocument:
    active_settings = settings or load_settings()
    content = extract_text_from_bytes(title, payload, active_settings.local_pdf_text_limit, active_settings.pdf_content_char_limit)
    metadata = extract_metadata(title, content)
    return IndexedDocument(
        doc_id=build_safe_document_id(source_id),
        title=title,
        content=content,
        updated_at=updated_at,
        status=infer_status(title, content),
        patient_name=metadata["patient_name"],
        account_id=metadata["account_id"],
        payer_id=metadata["payer_id"],
        cups_code=metadata["cups_code"],
        authorization_number=metadata["authorization_number"],
    )


def upload_documents_to_search(settings: Settings, documents: list[IndexedDocument]) -> None:
    url = (
        f"{settings.azure_search_endpoint.rstrip('/')}"
        f"/indexes/{settings.azure_search_index}/docs/index"
        f"?api-version={settings.azure_search_api_version}"
    )

    total_documents = len(documents)
    total_batches = (total_documents + INDEX_BATCH_SIZE - 1) // INDEX_BATCH_SIZE

    for batch_index, start in enumerate(range(0, len(documents), INDEX_BATCH_SIZE), start=1):
        batch = documents[start : start + INDEX_BATCH_SIZE]
        end = start + len(batch)
        print(
            f"Enviando lote {batch_index}/{total_batches} a Azure Search "
            f"({start + 1}-{end} de {total_documents})"
        )
        payload = {
            "value": [
                {
                    "@search.action": "mergeOrUpload",
                    "id": document.doc_id,
                    "content": document.content,
                    "title": document.title,
                    "updated_at": document.updated_at,
                    "status": document.status,
                    "patient_name": document.patient_name,
                    "account_id": document.account_id,
                    "payer_id": document.payer_id,
                    "cups_code": document.cups_code,
                    "authorization_number": document.authorization_number,
                }
                for document in batch
            ]
        }
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "api-key": settings.azure_search_api_key,
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        print(f"Lote {batch_index}/{total_batches} indexado correctamente.")


def _build_blob_service_client(settings: Settings) -> BlobServiceClient:
    if settings.azure_storage_connection_string:
        return BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)

    if settings.azure_storage_account_url and settings.azure_storage_account_key:
        return BlobServiceClient(
            account_url=settings.azure_storage_account_url,
            credential=settings.azure_storage_account_key,
        )

    if settings.azure_storage_account_name and settings.azure_storage_account_key:
        account_url = f"https://{settings.azure_storage_account_name}.blob.core.windows.net"
        return BlobServiceClient(account_url=account_url, credential=settings.azure_storage_account_key)

    raise RuntimeError("Falta configuracion de Azure Storage para indexar documentos.")
def build_safe_document_id(value: str) -> str:
    encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=") or "document"


def infer_status(filename: str, content: str) -> str:
    haystack = f"{filename} {content}".lower()
    if "dev" in haystack or "devol" in haystack:
        return "devuelta"
    if "glosa" in haystack:
        return "glosada"
    if "rechazo" in haystack:
        return "rechazada"
    return "pendiente de revision"


def extract_metadata(filename: str, content: str) -> dict[str, str]:
    normalized = " ".join(content.split())
    return {
        "payer_id": extract_filename_segment(filename, 1),
        "account_id": Path(filename).stem.split("_")[-1],
        "cups_code": extract_cups_code(normalized),
        "authorization_number": extract_authorization_number(normalized),
        "patient_name": extract_patient_name(normalized),
    }


def extract_filename_segment(filename: str, index: int) -> str:
    parts = Path(filename).stem.split("_")
    return parts[index].strip() if len(parts) > index else ""


def extract_cups_code(content: str) -> str:
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


def extract_authorization_number(content: str) -> str:
    patterns = [
        r"autorizaci[oó]n[:\s]*(\d{6,12})",
        r"n[uú]mero de autorizaci[oó]n[:\s]*(\d{6,12})",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def extract_patient_name(content: str) -> str:
    patterns = [
        r"PACIENTE[:\s]*([A-ZÁÉÍÓÚÑ ]{6,}?)(?:\s+DOC[:\s]|\s+DESCUENTO[:\s]|\s+IVA[:\s]|$)",
        r"Yo,\s*([A-ZÁÉÍÓÚÑ ]{6,}),\s*mayor de edad",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split())
    return ""


if __name__ == "__main__":
    run()
