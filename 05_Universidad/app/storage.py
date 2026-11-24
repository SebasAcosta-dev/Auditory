from __future__ import annotations

from pathlib import Path

from azure.storage.blob import BlobServiceClient, ContentSettings

from app.config import Settings


def build_blob_service_client(settings: Settings) -> BlobServiceClient:
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

    raise RuntimeError(
        "Falta configuracion de Azure Storage. Define AZURE_STORAGE_CONNECTION_STRING "
        "o AZURE_STORAGE_ACCOUNT_URL + AZURE_STORAGE_ACCOUNT_KEY."
    )


class AzureBlobReportStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.container_name = settings.azure_storage_reports_container
        self.client = build_blob_service_client(settings)

    def upload_report(self, file_path: Path) -> str:
        container_client = self.client.get_container_client(self.container_name)
        try:
            container_client.create_container()
        except Exception:
            pass

        blob_name = file_path.name
        blob_client = container_client.get_blob_client(blob_name)
        with file_path.open("rb") as handle:
            blob_client.upload_blob(
                handle,
                overwrite=True,
                content_settings=ContentSettings(content_type="text/markdown; charset=utf-8"),
            )
        return blob_client.url

def build_blob_report_store(settings: Settings) -> AzureBlobReportStore | None:
    if (
        settings.azure_storage_connection_string
        or (settings.azure_storage_account_url and settings.azure_storage_account_key)
        or (settings.azure_storage_account_name and settings.azure_storage_account_key)
    ):
        return AzureBlobReportStore(settings)
    return None
