from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config import Settings


@dataclass(frozen=True)
class SearchDocument:
    title: str
    content: str
    updated_at: str
    status: str
    patient_name: str
    account_id: str
    score: float | None
    raw: dict[str, Any]


class AzureSearchClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (
            f"{settings.azure_search_endpoint.rstrip('/')}"
            f"/indexes/{settings.azure_search_index}/docs/search"
            f"?api-version={settings.azure_search_api_version}"
        )

    def search(
        self,
        query: str,
        top: int | None = None,
        filter_expression: str | None = None,
        order_by: str | None = None,
        skip: int | None = None,
    ) -> list[SearchDocument]:
        payload: dict[str, Any] = {
            "search": query,
            "top": top or self.settings.search_top_k,
            "queryType": "simple",
        }
        if filter_expression:
            payload["filter"] = filter_expression
        if order_by:
            payload["orderby"] = order_by
        if skip:
            payload["skip"] = skip

        response = requests.post(
            self.base_url,
            headers={
                "Content-Type": "application/json",
                "api-key": self.settings.azure_search_api_key,
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        results = response.json().get("value", [])
        return [self._to_document(item) for item in results]

    def count_documents(self, query: str = "*", filter_expression: str | None = None) -> int:
        payload: dict[str, Any] = {
            "search": query,
            "top": 0,
            "count": True,
            "queryType": "simple",
        }
        if filter_expression:
            payload["filter"] = filter_expression

        response = requests.post(
            self.base_url,
            headers={
                "Content-Type": "application/json",
                "api-key": self.settings.azure_search_api_key,
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return int(response.json().get("@odata.count", 0))

    def delete_documents(self, document_ids: list[str]) -> int:
        if not document_ids:
            return 0

        index_url = (
            f"{self.settings.azure_search_endpoint.rstrip('/')}"
            f"/indexes/{self.settings.azure_search_index}/docs/index"
            f"?api-version={self.settings.azure_search_api_version}"
        )
        payload = {
            "value": [
                {
                    "@search.action": "delete",
                    "id": document_id,
                }
                for document_id in document_ids
            ]
        }
        response = requests.post(
            index_url,
            headers={
                "Content-Type": "application/json",
                "api-key": self.settings.azure_search_api_key,
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        return len(document_ids)

    def recent_accounts(self, days: int = 7, top: int | None = None) -> list[SearchDocument]:
        del days
        return self.search("*", top=top or self.settings.search_top_k, order_by=f"{self.settings.date_field} desc")

    def _to_document(self, item: dict[str, Any]) -> SearchDocument:
        return SearchDocument(
            title=str(item.get(self.settings.title_field, "Sin titulo")),
            content=str(item.get(self.settings.content_field, "")),
            updated_at=str(item.get(self.settings.date_field, "")),
            status=str(item.get(self.settings.status_field, "")),
            patient_name=str(item.get(self.settings.patient_field, "")),
            account_id=str(item.get(self.settings.account_field, "")),
            score=item.get("@search.score"),
            raw=item,
        )
