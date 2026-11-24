from __future__ import annotations

import argparse
import mimetypes
import zipfile
from pathlib import Path

from azure.storage.blob import ContentSettings

from app.config import load_settings
from app.document_reader import is_supported_document
from app.index_storage_to_search import run as run_indexer
from app.storage import build_blob_service_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrae PDFs e imagenes de un ZIP local y los sube al contenedor de documentos en Azure Blob Storage."
    )
    parser.add_argument("zip_path", help="Ruta local del archivo ZIP que quieres cargar.")
    parser.add_argument(
        "--prefix",
        default="",
        help="Prefijo opcional dentro del contenedor. Si no se indica, se usa el nombre del ZIP.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescribe blobs existentes con el mismo nombre.",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Lanza la indexacion a Azure Search cuando termine la subida.",
    )
    return parser.parse_args()


def run() -> None:
    args = parse_args()
    upload_zip_to_blob(args.zip_path, prefix=args.prefix, overwrite=args.overwrite, index=args.index)


def upload_zip_to_blob(zip_path: str, prefix: str = "", overwrite: bool = False, index: bool = False) -> tuple[int, int]:
    zip_path = Path(zip_path).expanduser().resolve()
    if not zip_path.exists():
        raise RuntimeError(f"No existe el archivo ZIP: {zip_path}")
    if zip_path.suffix.lower() != ".zip":
        raise RuntimeError(f"El archivo indicado no es un ZIP: {zip_path.name}")

    settings = load_settings()
    blob_service_client = build_blob_service_client(settings)
    container_client = blob_service_client.get_container_client(settings.azure_storage_documents_container)
    try:
        container_client.create_container()
    except Exception:
        pass

    prefix = normalize_prefix(prefix or zip_path.stem)
    uploaded = 0
    skipped = 0

    with zipfile.ZipFile(zip_path) as archive:
        members = archive.namelist()
        supported_members = [
            member_name
            for member_name in members
            if not member_name.endswith("/") and is_supported_document(member_name)
        ]
        skipped = len(members) - len(supported_members)
        total_supported = len(supported_members)
        print(f"Archivos detectados para subir: {total_supported}. Omitidos: {skipped}.")

        for index_position, member_name in enumerate(supported_members, start=1):
            if index_position == 1 or index_position % 100 == 0 or index_position == total_supported:
                print(f"Subiendo archivos a Azure Blob: {index_position}/{total_supported}")

            blob_name = build_blob_name(prefix, member_name)
            content_type = guess_content_type(member_name)
            with archive.open(member_name) as handle:
                blob_client = container_client.get_blob_client(blob_name)
                blob_client.upload_blob(
                    handle.read(),
                    overwrite=overwrite,
                    content_settings=ContentSettings(content_type=content_type),
                )
            uploaded += 1

    print(
        f"Carga completada. Archivos subidos: {uploaded}. "
        f"Archivos omitidos: {skipped}."
    )

    if index:
        print("Iniciando indexacion en Azure Search...")
        run_indexer()

    return uploaded, skipped


def normalize_prefix(value: str) -> str:
    cleaned = value.replace("\\", "/").strip().strip("/")
    return cleaned


def build_blob_name(prefix: str, member_name: str) -> str:
    normalized_member = member_name.replace("\\", "/").lstrip("/")
    if prefix:
        return f"{prefix}/{normalized_member}"
    return normalized_member


def guess_content_type(filename: str) -> str:
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


if __name__ == "__main__":
    run()
