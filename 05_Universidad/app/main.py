from __future__ import annotations

import sys
import tkinter as tk
import unicodedata
import webbrowser
from dataclasses import dataclass
from html import escape
from pathlib import Path
from tkinter import filedialog

import requests

from app.analysis import (
    build_local_answer,
    classify_document_type,
    group_documents_by_account,
    validate_bundle,
)
from app.azure_search import AzureSearchClient, SearchDocument
from app.charts import (
    build_chart_artifacts,
    build_chart_metrics,
    build_dashboard_html_body,
    build_interactive_chart_artifacts,
    filter_documents_by_range,
)
from app.config import load_settings
from app.reporting import save_html_report, save_markdown_report
from app.storage import build_blob_report_store
from app.upload_zip_to_blob import upload_zip_to_blob


@dataclass(frozen=True)
class InventoryMetrics:
    total_documents: int
    total_accounts: int
    document_type_counts: dict[str, int]
    support_presence_counts: dict[str, int]
    bundle_status_counts: dict[str, int]
    bundle_risk_counts: dict[str, int]
    complete_accounts: int
    incomplete_accounts: int
    observed_accounts: int


def print_welcome() -> None:
    print("Asistente de facturacion medica")
    print("Comandos: pregunta <texto> | informe [tema] | recientes | graficas | graficas interactivas | duplicados | cargar [ruta_zip] | salir")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def format_recent_accounts(search_client: AzureSearchClient) -> str:
    docs = search_client.recent_accounts()
    if not docs:
        return "No se encontraron cuentas recientes en Azure Search."

    lines = ["Cuentas recientes encontradas en Azure Search:"]
    for doc in docs:
        lines.append(
            " | ".join(
                [
                    f"Cuenta: {doc.account_id or 'N/D'}",
                    f"Estado: {doc.status or 'N/D'}",
                    f"Fecha: {doc.updated_at or 'N/D'}",
                    f"Titulo: {doc.title}",
                ]
            )
        )
    return "\n".join(lines)


def collect_all_search_documents(search_client: AzureSearchClient, batch_size: int = 200) -> list[SearchDocument]:
    total = search_client.count_documents("*")
    documents: list[SearchDocument] = []
    for start in range(0, total, batch_size):
        documents.extend(search_client.search("*", top=batch_size, skip=start))
    return documents


def collect_search_validations(search_client: AzureSearchClient) -> list:
    docs = collect_all_search_documents(search_client)
    bundles = group_documents_by_account(docs)
    return [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]


def build_inventory_metrics(documents: list[SearchDocument]) -> InventoryMetrics:
    doc_type_counts = {
        "factura": 0,
        "autorizacion": 0,
        "historia_clinica": 0,
        "certificado": 0,
        "otro": 0,
    }
    for doc in documents:
        doc_type_counts[classify_document_type(doc.title)] = doc_type_counts.get(classify_document_type(doc.title), 0) + 1

    bundles = group_documents_by_account(documents)
    validations = [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]

    support_presence_counts = {
        "factura": 0,
        "autorizacion": 0,
        "historia_clinica": 0,
        "certificado": 0,
    }
    bundle_status_counts: dict[str, int] = {}
    bundle_risk_counts: dict[str, int] = {}

    complete_accounts = 0
    incomplete_accounts = 0
    observed_accounts = 0

    for validation in validations:
        for support_name in support_presence_counts:
            if support_name in validation.documents:
                support_presence_counts[support_name] += 1

        bundle_status_counts[validation.status] = bundle_status_counts.get(validation.status, 0) + 1
        bundle_risk_counts[validation.risk_level] = bundle_risk_counts.get(validation.risk_level, 0) + 1

        if validation.status == "completa":
            complete_accounts += 1
        elif validation.status == "incompleta":
            incomplete_accounts += 1
        else:
            observed_accounts += 1

    return InventoryMetrics(
        total_documents=len(documents),
        total_accounts=len(bundles),
        document_type_counts=doc_type_counts,
        support_presence_counts=support_presence_counts,
        bundle_status_counts=bundle_status_counts,
        bundle_risk_counts=bundle_risk_counts,
        complete_accounts=complete_accounts,
        incomplete_accounts=incomplete_accounts,
        observed_accounts=observed_accounts,
    )


def build_report(search_client: AzureSearchClient, title: str) -> str:
    docs = collect_all_search_documents(search_client)
    if not docs:
        return "\n".join(
            [
                f"Resumen: {title}",
                "",
                "No se encontraron documentos indexados en Azure Search.",
            ]
        )

    metrics = build_inventory_metrics(docs)
    bundles = group_documents_by_account(docs)
    validations = [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]
    validations.sort(key=lambda item: (item.latest_date, item.account_id), reverse=True)

    lines = [
        f"Resumen: {title}",
        "",
        "Origen del analisis:",
        "- Reporte generado desde Azure AI Search.",
        "",
        "Inventario general:",
        f"- Total de documentos indexados: {metrics.total_documents}",
        f"- Total de cuentas o facturas detectadas: {metrics.total_accounts}",
        f"- Facturas: {metrics.document_type_counts.get('factura', 0)}",
        f"- Autorizaciones u ordenes medicas: {metrics.document_type_counts.get('autorizacion', 0)}",
        f"- Historias clinicas: {metrics.document_type_counts.get('historia_clinica', 0)}",
        f"- Certificados: {metrics.document_type_counts.get('certificado', 0)}",
        "",
        "Cobertura por cuenta:",
        f"- Cuentas con factura: {metrics.support_presence_counts.get('factura', 0)}",
        f"- Cuentas con autorizacion: {metrics.support_presence_counts.get('autorizacion', 0)}",
        f"- Cuentas con historia clinica: {metrics.support_presence_counts.get('historia_clinica', 0)}",
        f"- Cuentas con certificado: {metrics.support_presence_counts.get('certificado', 0)}",
        f"- Cuentas completas: {metrics.complete_accounts}",
        f"- Cuentas incompletas: {metrics.incomplete_accounts}",
        f"- Cuentas con observaciones: {metrics.observed_accounts}",
        "",
        "Riesgo por cuenta:",
    ]

    for risk, count in sorted(metrics.bundle_risk_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {risk}: {count}")

    lines.extend(["", "Facturas priorizadas:"])
    for item in validations[:25]:
        lines.append(
            f"- Factura {item.account_id} | Estado {item.status} | Riesgo {item.risk_level} | Fecha {item.latest_date or 'N/D'}"
        )
        lines.append(f"  Hallazgo principal: {item.findings[0]}")

    return "\n".join(lines)


def answer_without_llm(search_client: AzureSearchClient, question: str) -> str:
    general_answer = answer_azure_general_question(search_client, question)
    if general_answer:
        return general_answer

    docs = search_client.search(question)
    if not docs:
        return "No encontre documentos relevantes para esa consulta en Azure Search."
    return build_local_answer(question, docs)


def answer_azure_general_question(search_client: AzureSearchClient, question: str) -> str | None:
    normalized = normalize_text(question)

    if any(term in normalized for term in ["cuantos documentos", "total de documentos"]):
        total = search_client.count_documents("*")
        return f"En Azure Search tienes {total} documento(s) indexado(s)."

    if any(term in normalized for term in ["cuantas facturas", "total de facturas", "cuantas cuentas", "cuantas cuentas tengo"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        accounts = sorted({doc.account_id for doc in docs if doc.account_id})
        suffix = ""
        if accounts:
            suffix = f" Algunas son: {', '.join(accounts[:10])}."
        return (
            f"En Azure Search tienes {metrics.total_accounts} cuenta(s) o factura(s) distinta(s) "
            f"y {metrics.document_type_counts.get('factura', 0)} documento(s) de factura.{suffix}"
        )

    if any(term in normalized for term in ["que documentos", "que tenemos", "que hay", "documentos cargados"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        return (
            "Inventario actual en Azure Search: "
            f"{metrics.total_documents} documentos, "
            f"{metrics.document_type_counts.get('factura', 0)} facturas, "
            f"{metrics.document_type_counts.get('autorizacion', 0)} autorizaciones, "
            f"{metrics.document_type_counts.get('historia_clinica', 0)} historias clinicas y "
            f"{metrics.document_type_counts.get('certificado', 0)} certificados."
        )

    if any(term in normalized for term in ["ordenes medicas", "orden medica", "autorizacion medica", "autorizaciones"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        return (
            "En Azure Search encontre "
            f"{metrics.document_type_counts.get('autorizacion', 0)} documento(s) de autorizacion u orden medica."
        )

    if any(term in normalized for term in ["historia clinica", "historias clinicas"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        return (
            "En Azure Search encontre "
            f"{metrics.document_type_counts.get('historia_clinica', 0)} documento(s) de historia clinica."
        )

    if any(term in normalized for term in ["certificados", "soportes crc", "crc"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        return f"En Azure Search encontre {metrics.document_type_counts.get('certificado', 0)} certificado(s)."

    if any(term in normalized for term in ["facturas cargadas", "documentos de factura"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        return f"En Azure Search encontre {metrics.document_type_counts.get('factura', 0)} documento(s) de factura."

    if any(term in normalized for term in ["cumplimiento", "soportes completos", "cuentas completas", "completas e incompletas"]):
        docs = collect_all_search_documents(search_client)
        metrics = build_inventory_metrics(docs)
        return (
            "Resumen de cumplimiento documental: "
            f"{metrics.complete_accounts} cuenta(s) completas, "
            f"{metrics.incomplete_accounts} incompletas y "
            f"{metrics.observed_accounts} con observaciones. "
            f"Con autorizacion: {metrics.support_presence_counts.get('autorizacion', 0)}, "
            f"con historia clinica: {metrics.support_presence_counts.get('historia_clinica', 0)}, "
            f"con certificado: {metrics.support_presence_counts.get('certificado', 0)}."
        )

    if any(term in normalized for term in ["problema principal", "hallazgo principal", "riesgo principal"]):
        validations = collect_search_validations(search_client)
        if not validations:
            return "No encontre documentos indexados en Azure Search."
        severity = {"alto": 2, "medio": 1, "bajo": 0}
        validations.sort(key=lambda item: (severity.get(item.risk_level, -1), item.latest_date), reverse=True)
        target = validations[0]
        return f"El hallazgo principal actual es la factura {target.account_id}: {target.findings[0]}"

    return None


def build_duplicate_candidates(search_client: AzureSearchClient) -> tuple[str, list[str]]:
    docs = collect_all_search_documents(search_client)
    return build_duplicate_candidates_from_documents(docs)


def build_duplicate_candidates_from_documents(docs: list[SearchDocument]) -> tuple[str, list[str]]:
    groups: dict[tuple[str, str, str], list[SearchDocument]] = {}
    for doc in docs:
        doc_type = classify_document_type(doc.title)
        cups_code = str(doc.raw.get("cups_code", "") or "").strip()
        group_key = (doc.account_id or "sin_cuenta", doc_type, cups_code or "sin_cups")
        groups.setdefault(group_key, []).append(doc)

    duplicate_ids: list[str] = []
    lines = ["Revision de duplicados candidatos:", ""]
    found = 0

    for (account_id, doc_type, cups_code), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        found += 1
        sorted_items = sorted(items, key=lambda doc: (doc.updated_at or "", doc.title), reverse=True)
        keep = sorted_items[0]
        remove = sorted_items[1:]
        duplicate_ids.extend(str(doc.raw.get("id", "")) for doc in remove if str(doc.raw.get("id", "")))
        lines.append(
            f"- Cuenta {account_id} | Tipo {doc_type} | CUPS {cups_code or 'N/D'} | "
            f"Conservar: {keep.title} ({keep.updated_at or 'N/D'})"
        )
        for doc in remove[:10]:
            lines.append(f"  Eliminar candidato: {doc.title} ({doc.updated_at or 'N/D'})")

    if found == 0:
        return "No se detectaron duplicados candidatos con las reglas actuales.", []

    lines.extend(
        [
            "",
            f"Total de documentos candidatos a eliminar del indice: {len(duplicate_ids)}",
            "Regla aplicada: misma cuenta, mismo tipo documental y mismo CUPS; se conserva el mas reciente.",
        ]
    )
    return "\n".join(lines), duplicate_ids


def persist_report(settings, title: str, body: str, blob_report_store) -> str:
    path = save_markdown_report(settings.report_output_dir, title, body)
    if blob_report_store is None:
        return f"Informe generado en: {path}"

    blob_url = blob_report_store.upload_report(path)
    return f"Informe generado en: {path}\nSubido a Azure Storage: {blob_url}"


def get_html_template_path() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent / "auditory.html"
    return candidate if candidate.exists() else None


def persist_html_report(settings, title: str, body_html: str, blob_report_store) -> str:
    path = save_html_report(settings.report_output_dir, title, body_html, get_html_template_path())
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:
        pass

    if blob_report_store is None:
        return f"Informe HTML generado en: {path}"

    blob_url = blob_report_store.upload_report(path)
    return f"Informe HTML generado en: {path}\nSubido a Azure Storage: {blob_url}"


def build_html_report(search_client: AzureSearchClient, title: str, range_label: str = "todo el periodo") -> str:
    docs = collect_all_search_documents(search_client)
    metrics = build_inventory_metrics(docs)
    chart_metrics = build_chart_metrics(docs)
    validations = chart_metrics.validations
    duplicate_report, duplicate_ids = build_duplicate_candidates_from_documents(docs)
    prioritized = sorted(
        validations,
        key=lambda item: ({"alto": 2, "medio": 1, "bajo": 0}.get(item.risk_level, -1), item.latest_date),
        reverse=True,
    )

    top_rows = []
    for item in prioritized[:15]:
        top_rows.append(
            f"""
            <tr>
              <td>{item.account_id or 'N/D'}</td>
              <td>{escape(item.status)}</td>
              <td>{escape(item.risk_level)}</td>
              <td>{escape(item.cups_code or 'N/D')}</td>
              <td>{escape(item.latest_date or 'N/D')}</td>
              <td>{escape(item.findings[0])}</td>
            </tr>
            """
        )

    critical_cards = []
    for item in prioritized[:6]:
        critical_cards.append(
            f"""
            <div class="service-card">
              <div class="service-num">{escape(item.risk_level.upper())}</div>
              <div class="service-icon">{'!!' if item.risk_level == 'alto' else '!' if item.risk_level == 'medio' else 'OK'}</div>
              <h3>Factura {escape(item.account_id or 'N/D')}</h3>
              <p><strong>Estado:</strong> {escape(item.status)}<br>
              <strong>CUPS:</strong> {escape(item.cups_code or 'N/D')}<br>
              <strong>Fecha:</strong> {escape(item.latest_date or 'N/D')}<br>
              <strong>Hallazgo:</strong> {escape(item.findings[0])}</p>
            </div>
            """
        )

    duplicate_lines = []
    for line in duplicate_report.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            duplicate_lines.append(f"<li>{escape(stripped[2:])}</li>")
        elif stripped.startswith("Eliminar candidato:"):
            duplicate_lines.append(f"<li>{escape(stripped)}</li>")

    dashboard_section = build_dashboard_html_body(chart_metrics, range_label, title)
    overview_section = f"""
    <section style="padding: 2rem 6%;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Resumen general</p>
        <h2 class="section-title">Estado de la auditoria</h2>
        <div class="benefits-grid">
          <div class="benefit-item"><span class="benefit-emoji">#</span><h4>Documentos</h4><p>{metrics.total_documents}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">#</span><h4>Cuentas</h4><p>{metrics.total_accounts}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">OK</span><h4>Completas</h4><p>{metrics.complete_accounts}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">!</span><h4>Observaciones</h4><p>{metrics.observed_accounts}</p></div>
        </div>
      </div>
    </section>
    <section id="cumplimiento" style="padding: 2rem 6%;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Cumplimiento</p>
        <h2 class="section-title">Cobertura documental por cuenta</h2>
        <div class="benefits-grid">
          <div class="benefit-item"><span class="benefit-emoji">FV</span><h4>Cuentas con factura</h4><p>{metrics.support_presence_counts.get('factura', 0)}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">OTR</span><h4>Con autorizacion</h4><p>{metrics.support_presence_counts.get('autorizacion', 0)}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">HC</span><h4>Con historia clinica</h4><p>{metrics.support_presence_counts.get('historia_clinica', 0)}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">CRC</span><h4>Con certificado</h4><p>{metrics.support_presence_counts.get('certificado', 0)}</p></div>
        </div>
      </div>
    </section>
    <section id="criticas" style="padding: 2rem 6%;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Facturas criticas</p>
        <h2 class="section-title">Casos a priorizar</h2>
        <div class="services-grid">
          {''.join(critical_cards) if critical_cards else '<p class="section-sub">No hay facturas criticas para mostrar.</p>'}
        </div>
      </div>
    </section>
    <section id="duplicados" style="padding: 2rem 6%;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Duplicados</p>
        <h2 class="section-title">Candidatos detectados</h2>
        <div class="about-visual">
          <p class="section-sub" style="max-width:none; margin-bottom:1rem;">
            Total de documentos candidatos a eliminar del indice: {len(duplicate_ids)}
          </p>
          <ul style="padding-left:1.2rem; color: var(--muted); line-height:1.8;">
            {''.join(duplicate_lines) if duplicate_lines else '<li>No se detectaron duplicados con las reglas actuales.</li>'}
          </ul>
        </div>
      </div>
    </section>
    <section style="padding: 2rem 6% 6rem;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Detalle</p>
        <h2 class="section-title">Facturas priorizadas</h2>
        <div class="about-visual" style="overflow:auto;">
          <table style="width:100%; border-collapse: collapse; font-size: 0.92rem;">
            <thead>
              <tr>
                <th style="text-align:left; padding:0.8rem; border-bottom:1px solid rgba(26,106,191,0.2);">Factura</th>
                <th style="text-align:left; padding:0.8rem; border-bottom:1px solid rgba(26,106,191,0.2);">Estado</th>
                <th style="text-align:left; padding:0.8rem; border-bottom:1px solid rgba(26,106,191,0.2);">Riesgo</th>
                <th style="text-align:left; padding:0.8rem; border-bottom:1px solid rgba(26,106,191,0.2);">CUPS</th>
                <th style="text-align:left; padding:0.8rem; border-bottom:1px solid rgba(26,106,191,0.2);">Fecha</th>
                <th style="text-align:left; padding:0.8rem; border-bottom:1px solid rgba(26,106,191,0.2);">Hallazgo principal</th>
              </tr>
            </thead>
            <tbody>
              {''.join(top_rows) if top_rows else '<tr><td colspan="6" style="padding:1rem;">No hay datos disponibles.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    </section>
    """
    return dashboard_section + overview_section


def select_zip_file() -> str:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(
            title="Selecciona un archivo ZIP",
            filetypes=[("Archivos ZIP", "*.zip"), ("Todos los archivos", "*.*")],
        )
    finally:
        root.destroy()
    return selected


def prompt_chart_range() -> tuple[str, int]:
    unit = input("Rango para graficas (dias/meses/anios/todo): ").strip().lower()
    if unit in {"", "todo"}:
        return "todo", 0
    if unit not in {"dias", "meses", "anios"}:
        print("Unidad no valida. Se usara todo el periodo.")
        return "todo", 0
    raw_amount = input(f"Cantidad de {unit}: ").strip()
    try:
        amount = max(1, int(raw_amount))
    except ValueError:
        print("Cantidad no valida. Se usara 1.")
        amount = 1
    return unit, amount


def prompt_html_export(message: str) -> bool:
    answer = input(f"{message} (s/n): ").strip().lower()
    return answer in {"s", "si", "y", "yes"}


def run() -> None:
    try:
        settings = load_settings()
    except RuntimeError as exc:
        print(f"Error de configuracion: {exc}")
        print("Crea un archivo .env y completa tus credenciales.")
        sys.exit(1)

    if settings.app_mode != "azure":
        print("Error de configuracion: esta version del asistente esta ajustada para APP_MODE=azure.")
        sys.exit(1)

    if not (settings.azure_search_endpoint and settings.azure_search_api_key and settings.azure_search_index):
        print("Error de configuracion: faltan credenciales de Azure AI Search para APP_MODE=azure.")
        sys.exit(1)

    search_client = AzureSearchClient(settings)
    blob_report_store = build_blob_report_store(settings)

    print_welcome()
    print(f"Modo activo: {settings.app_mode}")
    print("Proveedor de modelo: none")
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
            continue
        if raw.lower() == "graficas":
            try:
                unit, amount = prompt_chart_range()
                all_docs = collect_all_search_documents(search_client)
                filtered_docs, range_label = filter_documents_by_range(all_docs, unit, amount)
                artifacts = build_chart_artifacts(filtered_docs, range_label, open_window=True)
                print(artifacts.summary)
                if prompt_html_export("Quieres tambien estas graficas en HTML?"):
                    html_artifacts = build_chart_artifacts(
                        filtered_docs,
                        range_label,
                        generate_html=True,
                        output_dir=settings.report_output_dir,
                        template_path=get_html_template_path(),
                        title=f"Graficas de facturacion - {range_label}",
                    )
                    if html_artifacts.html_path:
                        try:
                            webbrowser.open(html_artifacts.html_path.resolve().as_uri())
                        except Exception:
                            pass
                        if blob_report_store is not None:
                            blob_url = blob_report_store.upload_report(html_artifacts.html_path)
                            print(f"Dashboard HTML: {html_artifacts.html_path}\nSubido a Azure Storage: {blob_url}")
                        else:
                            print(f"Dashboard HTML: {html_artifacts.html_path}")
            except requests.RequestException as exc:
                print(f"Error generando graficas: {exc}")
            continue
        if raw.lower() == "graficas interactivas":
            try:
                unit, amount = prompt_chart_range()
                all_docs = collect_all_search_documents(search_client)
                filtered_docs, range_label = filter_documents_by_range(all_docs, unit, amount)
                artifacts = build_interactive_chart_artifacts(
                    filtered_docs,
                    range_label,
                    output_dir=settings.report_output_dir,
                    template_path=get_html_template_path(),
                    title=f"Graficas interactivas - {range_label}",
                )
                if artifacts.html_path:
                    try:
                        webbrowser.open(artifacts.html_path.resolve().as_uri())
                    except Exception:
                        pass
                    if blob_report_store is not None:
                        blob_url = blob_report_store.upload_report(artifacts.html_path)
                        print(f"{artifacts.summary}\nDashboard interactivo: {artifacts.html_path}\nSubido a Azure Storage: {blob_url}")
                    else:
                        print(f"{artifacts.summary}\nDashboard interactivo: {artifacts.html_path}")
                else:
                    print(artifacts.summary)
            except requests.RequestException as exc:
                print(f"Error generando graficas interactivas: {exc}")
            continue
        if raw.lower() == "duplicados":
            try:
                report, duplicate_ids = build_duplicate_candidates(search_client)
                print(report)
                if not duplicate_ids:
                    continue
                confirm = input("\nQuieres eliminar estos duplicados del indice de Azure Search? (s/n): ").strip().lower()
                if confirm not in {"s", "si", "y", "yes"}:
                    print("No se eliminaron duplicados.")
                    continue
                deleted = search_client.delete_documents(duplicate_ids)
                print(f"Se eliminaron {deleted} documento(s) duplicado(s) del indice de Azure Search.")
            except requests.RequestException as exc:
                print(f"Error revisando duplicados: {exc}")
            continue
        if raw.lower() == "cargar":
            try:
                zip_path = select_zip_file()
                if not zip_path:
                    print("No se selecciono ningun archivo ZIP.")
                    continue
                uploaded, skipped = upload_zip_to_blob(zip_path, overwrite=True, index=True)
                print(
                    f"Carga finalizada. Subidos: {uploaded}. Omitidos: {skipped}. "
                    "Los documentos quedaron enviados a Azure Storage e indexados en Azure Search."
                )
            except Exception as exc:
                print(f"Error cargando archivo ZIP: {exc}")
            continue
        if raw.lower().startswith("cargar "):
            zip_path = raw[len("cargar ") :].strip().strip('"')
            try:
                uploaded, skipped = upload_zip_to_blob(zip_path, overwrite=True, index=True)
                print(
                    f"Carga finalizada. Subidos: {uploaded}. Omitidos: {skipped}. "
                    "Los documentos quedaron enviados a Azure Storage e indexados en Azure Search."
                )
            except Exception as exc:
                print(f"Error cargando archivo ZIP: {exc}")
            continue
        if raw.lower().startswith("pregunta "):
            question = raw[len("pregunta ") :].strip()
            try:
                print(answer_without_llm(search_client, question))
            except requests.RequestException as exc:
                print(f"Error procesando la consulta: {exc}")
            continue
        if raw.lower() == "informe" or raw.lower().startswith("informe "):
            topic = raw[len("informe ") :].strip() if raw.lower().startswith("informe ") else "Reporte general de facturas"
            try:
                body = build_report(search_client, topic)
                print(persist_report(settings, topic, body, blob_report_store))
                if prompt_html_export("Quieres tambien este informe en HTML?"):
                    body_html = build_html_report(search_client, topic)
                    print(persist_html_report(settings, topic, body_html, blob_report_store))
            except requests.RequestException as exc:
                print(f"Error generando el informe: {exc}")
            continue
        try:
            print(answer_without_llm(search_client, raw))
        except requests.RequestException as exc:
            print(f"Error procesando la consulta: {exc}")


if __name__ == "__main__":
    run()
