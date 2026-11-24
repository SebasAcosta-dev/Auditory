from __future__ import annotations

import os
from typing import Iterable

import requests

from app.azure_search import SearchDocument
from app.config import Settings


SYSTEM_PROMPT = """
Eres un asistente experto en facturacion medica y auditoria administrativa.
Tu funcion es ayudar a reducir rechazos y detectar riesgos en cuentas medicas.

Reglas:
- Responde en espanol claro y profesional.
- Prioriza causas de rechazo, inconsistencias y acciones preventivas.
- Si la evidencia documental no es suficiente, dilo explicitamente.
- No inventes datos clinicos ni administrativos.
- Cuando analices estados de cuentas recientes, separa:
  1. hallazgos,
  2. riesgos de rechazo,
  3. acciones recomendadas,
  4. resumen ejecutivo.
""".strip()


class BaseLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def answer_question(self, question: str, documents: Iterable[SearchDocument]) -> str:
        context = self._build_context(documents)
        return self._send_chat_request(question, context)

    def answer_from_context(self, question: str, context: str) -> str:
        return self._send_chat_request(question, context)

    def _build_context(self, documents: Iterable[SearchDocument]) -> str:
        chunks: list[str] = []
        for index, doc in enumerate(documents, start=1):
            chunks.append(
                "\n".join(
                    [
                        f"Documento {index}",
                        f"Titulo: {doc.title}",
                        f"Cuenta: {doc.account_id or 'N/D'}",
                        f"Paciente: {doc.patient_name or 'N/D'}",
                        f"Estado: {doc.status or 'N/D'}",
                        f"Fecha: {doc.updated_at or 'N/D'}",
                        f"Contenido: {doc.content[:4000]}",
                    ]
                )
            )
        return "\n\n".join(chunks) if chunks else "No se recuperaron documentos relevantes."

    def _send_chat_request(self, question: str, context: str) -> str:
        raise NotImplementedError


class GeminiClient(BaseLLMClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = os.getenv("GEMINI_MODEL", "").strip() or "gemini-1.5-flash"
        self.base_url = os.getenv("GEMINI_BASE_URL", "").strip() or "https://generativelanguage.googleapis.com/v1beta"

    def _send_chat_request(self, question: str, context: str) -> str:
        url = f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Usa el siguiente contexto documental para responder la pregunta.\n\n"
                                f"Pregunta:\n{question}\n\n"
                                f"Contexto:\n{context}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.2},
        }
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "Gemini no devolvio contenido."
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        return "\n".join(part for part in text_parts if part).strip() or "Gemini no devolvio texto util."


def build_llm_client(settings: Settings) -> BaseLLMClient | None:
    if settings.model_provider != "gemini":
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        return GeminiClient(settings)
    return None
