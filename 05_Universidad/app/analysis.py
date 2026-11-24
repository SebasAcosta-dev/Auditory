from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.azure_search import SearchDocument


DOC_TYPE_LABELS = {
    "FVS": "factura",
    "OTR": "autorizacion",
    "HEV": "historia_clinica",
    "CRC": "certificado",
}


@dataclass(frozen=True)
class BundleValidation:
    account_id: str
    payer_id: str
    latest_date: str
    cups_code: str
    patient_name: str
    authorization_number: str
    status: str
    risk_level: str
    findings: list[str]
    actions: list[str]
    documents: dict[str, SearchDocument]


def classify_document_type(title: str) -> str:
    prefix = title.split("_", 1)[0].upper()
    return DOC_TYPE_LABELS.get(prefix, "otro")


def validate_bundle(account_id: str, documents: Iterable[SearchDocument]) -> BundleValidation:
    docs = list(documents)
    by_type = {classify_document_type(doc.title): doc for doc in docs}
    findings: list[str] = []
    actions: list[str] = []

    missing = [label for label in ("factura", "autorizacion", "historia_clinica", "certificado") if label not in by_type]
    if missing:
        findings.append(f"Faltan soportes requeridos: {', '.join(missing)}.")
        actions.append("Completar los documentos faltantes antes de radicar o responder la devolucion.")

    invoice = by_type.get("factura")
    authorization = by_type.get("autorizacion")
    clinical = by_type.get("historia_clinica")
    certificate = by_type.get("certificado")

    invoice_cups = _pick_metadata(invoice, "cups_code")
    authorization_cups = _pick_metadata(authorization, "cups_code")
    clinical_cups = _pick_metadata(clinical, "cups_code")
    certificate_cups = _pick_metadata(certificate, "cups_code")

    cups_candidates = [
        invoice_cups,
        authorization_cups,
        clinical_cups,
        certificate_cups,
    ]
    cups_values = [value for value in cups_candidates if value]
    cups_code = cups_values[0] if cups_values else ""
    if len(set(cups_values)) > 1:
        findings.append(f"Inconsistencia en CUPS detectada: {', '.join(sorted(set(cups_values)))}.")
        actions.append("Revisar que el procedimiento facturado coincida con autorizacion e historia clinica.")
    findings.extend(
        _compare_field_against_invoice(
            "cups_code",
            "CUPS",
            invoice_cups,
            {
                "autorizacion": authorization_cups,
                "historia_clinica": clinical_cups,
                "certificado": certificate_cups,
            },
        )
    )

    patient_candidates = [
        _pick_metadata(invoice, "patient_name"),
        _pick_metadata(authorization, "patient_name"),
        _pick_metadata(clinical, "patient_name"),
        _pick_metadata(certificate, "patient_name"),
    ]
    patient_values = [value for value in patient_candidates if value]
    patient_name = patient_values[0] if patient_values else ""
    if len(set(patient_values)) > 1:
        findings.append("El nombre del paciente no coincide entre todos los soportes.")
        actions.append("Validar identificacion del paciente en factura, autorizacion y evolucion.")

    authorization_candidates = [
        _pick_metadata(authorization, "authorization_number"),
        _pick_metadata(certificate, "authorization_number"),
    ]
    auth_values = [value for value in authorization_candidates if value]
    authorization_number = auth_values[0] if auth_values else ""
    if certificate and authorization and len(set(auth_values)) > 1:
        findings.append("El numero de autorizacion no coincide entre autorizacion y certificado.")
        actions.append("Corregir el soporte administrativo y confirmar la autorizacion vigente.")
    findings.extend(
        _compare_field_against_invoice(
            "authorization_number",
            "numero de autorizacion",
            _pick_metadata(invoice, "authorization_number"),
            {
                "autorizacion": _pick_metadata(authorization, "authorization_number"),
                "historia_clinica": _pick_metadata(clinical, "authorization_number"),
                "certificado": _pick_metadata(certificate, "authorization_number"),
            },
        )
    )

    if invoice and not _pick_metadata(invoice, "invoice_number"):
        findings.append("La factura no expone numero identificable en el texto extraido.")
        actions.append("Verificar legibilidad del PDF de factura.")

    if not cups_code:
        findings.append("No fue posible identificar el codigo CUPS de forma automatica.")
        actions.append("Confirmar manualmente el CUPS o mejorar el patron de extraccion.")

    risk_level = "bajo"
    status = "completa"
    if missing or len(set(cups_values)) > 1 or len(set(patient_values)) > 1 or (certificate and authorization and len(set(auth_values)) > 1):
        risk_level = "alto"
        status = "con observaciones"
    elif not cups_code or not authorization_number:
        risk_level = "medio"
        status = "incompleta"

    if not findings:
        findings.append("Los soportes disponibles no muestran inconsistencias basicas.")
        actions.append("Mantener revision humana final antes de radicar.")

    latest_date = max((doc.updated_at for doc in docs), default="")
    payer_id = _extract_payer_id(docs)

    return BundleValidation(
        account_id=account_id,
        payer_id=payer_id,
        latest_date=latest_date,
        cups_code=cups_code,
        patient_name=patient_name,
        authorization_number=authorization_number,
        status=status,
        risk_level=risk_level,
        findings=findings,
        actions=actions,
        documents=by_type,
    )


def build_local_answer(question: str, documents: Iterable[SearchDocument]) -> str:
    docs = list(documents)
    if not docs:
        return "No encontre documentos relevantes para esa consulta en Azure Search."

    bundles = group_documents_by_account(docs)
    lines = [f"Consulta: {question}", ""]
    for account_id, account_docs in bundles.items():
        validation = validate_bundle(account_id, account_docs)
        lines.extend(_render_bundle(validation))
        lines.append("")
    return "\n".join(lines).strip()


def summarize_validations_for_llm(validations: Iterable[BundleValidation], max_items: int = 40) -> str:
    items = list(validations)
    high = [item for item in items if item.risk_level == "alto"]
    medium = [item for item in items if item.risk_level == "medio"]
    low = [item for item in items if item.risk_level == "bajo"]

    lines = [
        "Resumen estructurado de facturas:",
        f"- Total de facturas: {len(items)}",
        f"- Riesgo alto: {len(high)}",
        f"- Riesgo medio: {len(medium)}",
        f"- Riesgo bajo: {len(low)}",
        "",
        "Muestra de facturas para analisis:",
    ]

    prioritized = high + medium + low
    for item in prioritized[:max_items]:
        lines.append(
            f"- Factura {item.account_id} | CUPS {item.cups_code or 'N/D'} | Riesgo {item.risk_level} | Estado {item.status} | Problemas {'; '.join(item.findings)}"
        )

    return "\n".join(lines)


def build_privacy_context_for_question(question: str, validations: Iterable[BundleValidation], related_docs: Iterable[SearchDocument]) -> str:
    items = list(validations)
    related = list(related_docs)
    lines = [
        f"Pregunta del usuario: {question}",
        "",
        summarize_validations_for_llm(items, max_items=25),
    ]

    if related:
        lines.extend(["", "Documentos relacionados recuperados:"])
        for doc in related[:12]:
            lines.append(
                f"- Archivo {doc.title} | Factura {doc.account_id or 'N/D'} | Estado {doc.status or 'N/D'} | CUPS {doc.raw.get('cups_code', 'N/D') or 'N/D'}"
            )

    lines.extend(
        [
            "",
            "Regla de privacidad:",
            "- No expongas nombres de pacientes ni identificaciones.",
            "- Responde usando numero de factura, CUPS, riesgo y problema detectado.",
        ]
    )
    return "\n".join(lines)


def group_documents_by_account(documents: Iterable[SearchDocument]) -> dict[str, list[SearchDocument]]:
    bundles: dict[str, list[SearchDocument]] = {}
    for doc in documents:
        bundles.setdefault(doc.account_id or "sin_cuenta", []).append(doc)
    return bundles


def _render_bundle(validation: BundleValidation) -> list[str]:
    document_summary = ", ".join(
        f"{doc_type}={validation.documents[doc_type].title}"
        for doc_type in ("factura", "autorizacion", "historia_clinica", "certificado")
        if doc_type in validation.documents
    )
    return [
        f"Factura: {validation.account_id}",
        f"- Riesgo: {validation.risk_level}",
        f"- Estado: {validation.status}",
        f"- Paciente: {validation.patient_name or 'N/D'}",
        f"- CUPS: {validation.cups_code or 'N/D'}",
        f"- Autorizacion: {validation.authorization_number or 'N/D'}",
        f"- Fecha mas reciente: {validation.latest_date or 'N/D'}",
        f"- Documentos: {document_summary or 'Sin soportes identificados'}",
        f"- Hallazgos: {' | '.join(validation.findings)}",
        f"- Acciones: {' | '.join(validation.actions)}",
    ]


def _pick_metadata(document: SearchDocument | None, key: str) -> str:
    if not document:
        return ""
    value = document.raw.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _extract_payer_id(documents: Iterable[SearchDocument]) -> str:
    for doc in documents:
        payer_id = doc.raw.get("payer_id", "")
        if isinstance(payer_id, str) and payer_id.strip():
            return payer_id.strip()
    return ""


def _compare_field_against_invoice(
    field_name: str,
    field_label: str,
    invoice_value: str,
    other_documents: dict[str, str],
) -> list[str]:
    del field_name
    findings: list[str] = []
    if not invoice_value:
        return findings

    labels = {
        "autorizacion": "autorizacion medica",
        "historia_clinica": "historia clinica",
        "certificado": "certificado",
    }
    for doc_type, value in other_documents.items():
        if value and value != invoice_value:
            findings.append(
                f"El {field_label} de la factura no coincide con {labels.get(doc_type, doc_type)} "
                f"(factura: {invoice_value}; {labels.get(doc_type, doc_type)}: {value})."
            )
    return findings
