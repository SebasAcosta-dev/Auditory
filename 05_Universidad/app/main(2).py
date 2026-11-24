from __future__ import annotations

import sys

import requests

from app.azure_search import AzureSearchClient
from app.analysis import (
    answer_general_question,
    build_dashboard_report,
    build_local_answer,
    build_privacy_context_for_question,
    build_privacy_report,
    group_documents_by_account,
    summarize_validations_for_llm,
    validate_bundle,
)
from app.config import load_settings
from app.llm import BaseLLMClient, build_llm_client
from app.local_store import LocalDocumentStore
from app.reporting import save_markdown_report
from app.storage import build_blob_report_store


def print_welcome() -> None:
    print("Asistente de facturacion medica")
    print("Puedes escribir preguntas libres o usar: recientes | dashboard | informe [tema] | salir")


def format_recent_accounts(search_client: AzureSearchClient) -> str:
    docs = search_client.recent_accounts()
    if not docs:
        return "No se encontraron cuentas recientes."

    lines = ["Cuentas recientes encontradas:"]
    for doc in docs:
        lines.append(
            " | ".join(
                [
                    f"Cuenta: {doc.account_id or 'N/D'}",
                    f"Paciente: {doc.patient_name or 'N/D'}",
                    f"Estado: {doc.status or 'N/D'}",
                    f"Fecha: {doc.updated_at or 'N/D'}",
                    f"Titulo: {doc.title}",
                ]
            )
        )
    return "\n".join(lines)


def build_dashboard(search_client) -> str:
    if not hasattr(search_client, "all_documents"):
        return "El dashboard local solo esta disponible en APP_MODE=local."

    docs = search_client.all_documents()
    bundles = group_documents_by_account(docs)
    validations = [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]
    validations.sort(key=lambda item: (item.risk_level, item.latest_date), reverse=True)
    return build_dashboard_report(validations, "Dashboard de auditoria de facturas")


def build_report(search_client, title: str) -> str:
    if hasattr(search_client, "all_documents"):
        docs = search_client.all_documents()
        bundles = group_documents_by_account(docs)
        validations = [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]
        validations.sort(key=lambda item: (item.risk_level, item.latest_date), reverse=True)
        return build_privacy_report(validations, title)

    return "El reporte local con minimizacion de datos solo esta disponible en APP_MODE=local."


def answer_local_free_text(search_client, question: str) -> str:
    if hasattr(search_client, "all_documents"):
        docs = search_client.all_documents()
        bundles = group_documents_by_account(docs)
        validations = [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]
        general_answer = answer_general_question(question, validations)
        if general_answer:
            return general_answer

    docs = search_client.search(question)
    return build_local_answer(question, docs)


def collect_local_validations(search_client) -> list:
    docs = search_client.all_documents()
    bundles = group_documents_by_account(docs)
    return [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]


def answer_with_llm(search_client, llm_client: BaseLLMClient, question: str) -> str:
    if hasattr(search_client, "all_documents"):
        validations = collect_local_validations(search_client)
        related_docs = search_client.search(question)
        context = build_privacy_context_for_question(question, validations, related_docs)
        prompt = (
            "Responde la pregunta del usuario como auditor de facturacion medica. "
            "Si la pregunta es general, usa el resumen estructurado. "
            "Si la pregunta es sobre una factura o soporte concreto, apóyate en los documentos relacionados. "
            "Cumple la regla de privacidad y no muestres datos personales."
        )
        return llm_client.answer_from_context(prompt, context)

    docs = search_client.search(question)
    return llm_client.answer_question(question, docs)


def build_report_with_llm(search_client, llm_client: BaseLLMClient, title: str) -> str:
    if hasattr(search_client, "all_documents"):
        validations = collect_local_validations(search_client)
        context = "\n\n".join(
            [
                summarize_validations_for_llm(validations, max_items=80),
                "Regla de privacidad: no incluyas nombres, documentos de identidad ni datos sensibles del paciente.",
                "El reporte debe mostrar solo factura, CUPS, nivel de riesgo y problema detectado.",
            ]
        )
        prompt = (
            f"Genera un reporte ejecutivo titulado '{title}' sobre las facturas analizadas. "
            "Estructuralo con: resumen general, metricas, facturas con riesgo alto, facturas con riesgo medio, "
            "patrones frecuentes de error y acciones recomendadas. Cumple la regla de privacidad."
        )
        return llm_client.answer_from_context(prompt, context)

    docs = search_client.search(title)
    return llm_client.answer_question(title, docs)


def persist_report(settings, title: str, body: str, blob_report_store) -> str:
    path = save_markdown_report(settings.report_output_dir, title, body)
    if blob_report_store is None:
        return f"Informe generado en: {path}"

    blob_url = blob_report_store.upload_report(path)
    return f"Informe generado en: {path}\nSubido a Azure Storage: {blob_url}"


def handle_question(search_client: AzureSearchClient, llm_client: BaseLLMClient, question: str) -> str:
    docs = search_client.search(question)
    return llm_client.answer_question(question, docs)


def handle_report(search_client: AzureSearchClient, llm_client: BaseLLMClient, topic: str, output_dir: str) -> str:
    report_prompt = (
        f"Genera un informe ejecutivo sobre: {topic}. "
        "Incluye panorama actual, hallazgos, riesgos de rechazo, acciones inmediatas y prioridades."
    )
    body = handle_question(search_client, llm_client, report_prompt)
    path = save_markdown_report(output_dir, topic, body)
    return f"Informe generado en: {path}"


def run() -> None:
    try:
        settings = load_settings()
    except RuntimeError as exc:
        print(f"Error de configuracion: {exc}")
        print("Crea un archivo .env a partir de .env.example y completa tus credenciales.")
        sys.exit(1)

    if settings.app_mode == "azure":
        if not (
            settings.azure_search_endpoint
            and settings.azure_search_api_key
            and settings.azure_search_index
        ):
            print("Error de configuracion: faltan credenciales de Azure AI Search para APP_MODE=azure.")
            sys.exit(1)
        search_client = AzureSearchClient(settings)
    else:
        search_client = LocalDocumentStore(settings)

    llm_client = build_llm_client(settings)
    blob_report_store = build_blob_report_store(settings)

    print_welcome()
    print(f"Modo activo: {settings.app_mode}")
    print(f"Proveedor de modelo: {settings.model_provider}")
    while True:
        raw = input("\n> ").strip()
        if not raw:
            continue
        if raw.lower() in {"salir", "exit", "quit"}:
            print("Sesion finalizada.")
            break
        if raw.lower() == "recientes":
            try:
                print(format_recent_accounts(search_client))
            except requests.RequestException as exc:
                print(f"Error consultando Azure AI Search: {exc}")
            except RuntimeError as exc:
                print(f"Error local: {exc}")
            continue
        if raw.lower() == "dashboard":
            try:
                print(build_dashboard(search_client))
            except requests.RequestException as exc:
                print(f"Error construyendo dashboard: {exc}")
            except RuntimeError as exc:
                print(f"Error local: {exc}")
            continue
        if raw.lower().startswith("pregunta "):
            question = raw[len("pregunta ") :].strip()
            try:
                if llm_client is None:
                    print(answer_local_free_text(search_client, question))
                else:
                    print(answer_with_llm(search_client, llm_client, question))
            except requests.RequestException as exc:
                print(f"Error procesando la consulta: {exc}")
            except RuntimeError as exc:
                print(f"Error local: {exc}")
            continue
        if raw.lower() == "informe" or raw.lower().startswith("informe "):
            topic = raw[len("informe ") :].strip() if raw.lower().startswith("informe ") else "Reporte general de facturas"
            try:
                if llm_client is None:
                    body = build_report(search_client, topic)
                else:
                    body = build_report_with_llm(search_client, llm_client, topic)
                print(persist_report(settings, topic, body, blob_report_store))
            except requests.RequestException as exc:
                print(f"Error generando el informe: {exc}")
            except RuntimeError as exc:
                print(f"Error local: {exc}")
            continue
        try:
            if llm_client is None:
                print(answer_local_free_text(search_client, raw))
            else:
                print(answer_with_llm(search_client, llm_client, raw))
        except requests.RequestException as exc:
            print(f"Error procesando la consulta: {exc}")
        except RuntimeError as exc:
            print(f"Error local: {exc}")


if __name__ == "__main__":
    run()
