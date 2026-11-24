from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import matplotlib.pyplot as plt
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.analysis import classify_document_type, group_documents_by_account, validate_bundle
from app.azure_search import SearchDocument
from app.reporting import save_html_report


@dataclass(frozen=True)
class ChartArtifacts:
    summary: str
    html_path: Path | None = None


@dataclass(frozen=True)
class ChartMetrics:
    total_documents: int
    total_accounts: int
    document_type_counts: dict[str, int]
    compliance_counts: dict[str, int]
    match_counts: dict[str, int]
    validations: list


def build_chart_artifacts(
    documents: list[SearchDocument],
    range_label: str,
    *,
    open_window: bool = False,
    generate_html: bool = False,
    output_dir: str | None = None,
    template_path: Path | None = None,
    title: str = "Graficas generales",
) -> ChartArtifacts:
    filtered_docs = sorted(documents, key=lambda doc: (doc.updated_at or "", doc.title), reverse=True)
    if not filtered_docs:
        return ChartArtifacts(summary="No hay documentos en el rango indicado para generar graficas.")

    metrics = build_chart_metrics(filtered_docs)
    summary = build_terminal_summary(metrics, range_label)

    if open_window:
        fig = create_dashboard_figure(metrics, range_label)
        try:
            fig.canvas.manager.set_window_title(title)
        except Exception:
            pass
        plt.show()
        plt.close(fig)

    html_path: Path | None = None
    if generate_html and output_dir:
        body_html = build_dashboard_html_body(metrics, range_label, title)
        html_path = save_html_report(output_dir, title, body_html, template_path)

    return ChartArtifacts(summary=summary, html_path=html_path)


def build_interactive_chart_artifacts(
    documents: list[SearchDocument],
    range_label: str,
    *,
    output_dir: str,
    template_path: Path | None = None,
    title: str = "Graficas interactivas",
) -> ChartArtifacts:
    filtered_docs = sorted(documents, key=lambda doc: (doc.updated_at or "", doc.title), reverse=True)
    if not filtered_docs:
        return ChartArtifacts(summary="No hay documentos en el rango indicado para generar graficas interactivas.")

    metrics = build_chart_metrics(filtered_docs)
    summary = "\n".join(
        [
            f"Graficas interactivas generadas para el rango: {range_label}",
            f"- Total de documentos analizados: {metrics.total_documents}",
            f"- Total de cuentas evaluadas: {metrics.total_accounts}",
        ]
    )
    body_html = build_interactive_dashboard_html_body(metrics, range_label, title)
    html_path = save_html_report(output_dir, title, body_html, template_path)
    return ChartArtifacts(summary=summary, html_path=html_path)


def build_chart_metrics(documents: list[SearchDocument]) -> ChartMetrics:
    counts = {"factura": 0, "autorizacion": 0, "historia_clinica": 0, "certificado": 0, "otro": 0}
    for doc in documents:
        doc_type = classify_document_type(doc.title)
        counts[doc_type] = counts.get(doc_type, 0) + 1

    bundles = group_documents_by_account(documents)
    validations = [validate_bundle(account_id, account_docs) for account_id, account_docs in bundles.items()]

    compliance = {"completa": 0, "incompleta": 0, "con observaciones": 0}
    match_counts = {
        "Factura vs Autorizacion": 0,
        "Factura vs Historia": 0,
        "Factura vs Certificado": 0,
    }

    for item in validations:
        compliance[item.status] = compliance.get(item.status, 0) + 1
        invoice = item.documents.get("factura")
        authorization = item.documents.get("autorizacion")
        clinical = item.documents.get("historia_clinica")
        certificate = item.documents.get("certificado")
        invoice_cups = _pick(invoice, "cups_code")
        if invoice_cups and authorization and _pick(authorization, "cups_code") == invoice_cups:
            match_counts["Factura vs Autorizacion"] += 1
        if invoice_cups and clinical and _pick(clinical, "cups_code") == invoice_cups:
            match_counts["Factura vs Historia"] += 1
        if invoice_cups and certificate and _pick(certificate, "cups_code") == invoice_cups:
            match_counts["Factura vs Certificado"] += 1

    return ChartMetrics(
        total_documents=len(documents),
        total_accounts=len(validations),
        document_type_counts=counts,
        compliance_counts=compliance,
        match_counts=match_counts,
        validations=validations,
    )


def build_terminal_summary(metrics: ChartMetrics, range_label: str) -> str:
    sections = [
        f"Graficas para el rango: {range_label}",
        f"- Total de documentos analizados: {metrics.total_documents}",
        f"- Total de cuentas evaluadas: {metrics.total_accounts}",
        "",
        _build_bar_section(
            "Documentos por tipo",
            [
                ("Factura", metrics.document_type_counts.get("factura", 0)),
                ("Autorizacion", metrics.document_type_counts.get("autorizacion", 0)),
                ("Historia Clinica", metrics.document_type_counts.get("historia_clinica", 0)),
                ("Certificado", metrics.document_type_counts.get("certificado", 0)),
                ("Otro", metrics.document_type_counts.get("otro", 0)),
            ],
        ),
        "",
        _build_bar_section(
            "Cumplimiento documental",
            [
                ("Completa", metrics.compliance_counts.get("completa", 0)),
                ("Incompleta", metrics.compliance_counts.get("incompleta", 0)),
                ("Con observaciones", metrics.compliance_counts.get("con observaciones", 0)),
            ],
        ),
        "",
        _build_bar_section("Coincidencias de CUPS contra factura", list(metrics.match_counts.items())),
    ]
    return "\n".join(sections)


def build_dashboard_html_body(metrics: ChartMetrics, range_label: str, title: str) -> str:
    image_base64 = build_dashboard_image_base64(metrics, range_label)
    top_findings = []
    prioritized = sorted(
        metrics.validations,
        key=lambda item: ({"alto": 2, "medio": 1, "bajo": 0}.get(item.risk_level, -1), item.latest_date),
        reverse=True,
    )
    for item in prioritized[:12]:
        top_findings.append(
            f"""
            <div class="benefit-item">
              <span class="benefit-emoji">{_emoji_for_risk(item.risk_level)}</span>
              <h4>Factura {escape(item.account_id or 'N/D')}</h4>
              <p><strong>Estado:</strong> {escape(item.status)}<br>
              <strong>Riesgo:</strong> {escape(item.risk_level)}<br>
              <strong>Hallazgo:</strong> {escape(item.findings[0])}</p>
            </div>
            """
        )

    return f"""
    <nav>
      <a href="#" class="logo"><div class="logo-icon">AI</div>AUDITORY</a>
      <ul class="nav-links">
        <li><a href="#resumen">Resumen</a></li>
        <li><a href="#graficas">Graficas</a></li>
        <li><a href="#cumplimiento">Cumplimiento</a></li>
        <li><a href="#criticas">Facturas criticas</a></li>
        <li><a href="#duplicados">Duplicados</a></li>
      </ul>
    </nav>

    <section class="hero" id="resumen">
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
      <div class="orb orb-3"></div>
      <div class="hero-inner">
        <div class="hero-badge"><span class="badge-dot"></span>Reporte operativo</div>
        <h1>{escape(title)}<br><span>{escape(range_label)}</span></h1>
        <p>Vista general generada desde Azure Search para auditoria documental de facturacion medica.</p>
      </div>
    </section>

    <div class="stats-strip">
      <div class="stat"><span class="stat-num">{metrics.total_documents}</span><span class="stat-label">Documentos</span></div>
      <div class="stat"><span class="stat-num">{metrics.total_accounts}</span><span class="stat-label">Cuentas</span></div>
      <div class="stat"><span class="stat-num">{metrics.document_type_counts.get('factura', 0)}</span><span class="stat-label">Facturas</span></div>
      <div class="stat"><span class="stat-num">{metrics.compliance_counts.get('completa', 0)}</span><span class="stat-label">Completas</span></div>
    </div>

    <section id="graficas">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Graficas</p>
        <h2 class="section-title">Visual general del cumplimiento</h2>
        <p class="section-sub">Panel consolidado de documentos, estados y coincidencias de CUPS.</p>
        <div class="about-visual" style="margin-top:2rem;">
          <img src="data:image/png;base64,{image_base64}" alt="Dashboard AUDITORY" style="width:100%; border-radius:12px; display:block;" />
        </div>
      </div>
    </section>

    <section id="hallazgos" style="padding: 6rem 6%;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Hallazgos</p>
        <h2 class="section-title">Facturas priorizadas</h2>
        <div class="benefits-grid">
          {''.join(top_findings) if top_findings else '<p class="section-sub">No hay hallazgos para mostrar.</p>'}
        </div>
      </div>
    </section>
    """


def build_interactive_dashboard_html_body(metrics: ChartMetrics, range_label: str, title: str) -> str:
    figure = create_interactive_dashboard_figure(metrics, range_label)
    plot_html = figure.to_html(full_html=False, include_plotlyjs="cdn", config={"displaylogo": False, "responsive": True})

    high_risk = sum(1 for item in metrics.validations if item.risk_level == "alto")
    medium_risk = sum(1 for item in metrics.validations if item.risk_level == "medio")
    low_risk = sum(1 for item in metrics.validations if item.risk_level == "bajo")

    return f"""
    <nav>
      <a href="#" class="logo"><div class="logo-icon">AI</div>AUDITORY</a>
      <ul class="nav-links">
        <li><a href="#resumen">Resumen</a></li>
        <li><a href="#dashboard">Dashboard</a></li>
        <li><a href="#riesgo">Riesgo</a></li>
      </ul>
    </nav>

    <section class="hero" id="resumen">
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
      <div class="orb orb-3"></div>
      <div class="hero-inner">
        <div class="hero-badge"><span class="badge-dot"></span>Dashboard interactivo</div>
        <h1>{escape(title)}<br><span>{escape(range_label)}</span></h1>
        <p>Explora la auditoria documental con linea de tiempo, hover, zoom y filtros visuales propios de Plotly.</p>
      </div>
    </section>

    <div class="stats-strip">
      <div class="stat"><span class="stat-num">{metrics.total_documents}</span><span class="stat-label">Documentos</span></div>
      <div class="stat"><span class="stat-num">{metrics.total_accounts}</span><span class="stat-label">Cuentas</span></div>
      <div class="stat"><span class="stat-num">{high_risk}</span><span class="stat-label">Riesgo alto</span></div>
      <div class="stat"><span class="stat-num">{metrics.compliance_counts.get('completa', 0)}</span><span class="stat-label">Completas</span></div>
    </div>

    <section id="dashboard">
      <div style="max-width:1200px; margin:0 auto;">
        <p class="section-label">Dashboard</p>
        <h2 class="section-title">Linea de tiempo y distribuciones</h2>
        <p class="section-sub">Puedes hacer zoom, mover la linea de tiempo y consultar valores exactos al pasar el cursor.</p>
        <div class="about-visual" style="margin-top:2rem; overflow:hidden;">
          {plot_html}
        </div>
      </div>
    </section>

    <section id="riesgo" style="padding: 2rem 6% 6rem;">
      <div style="max-width:1100px; margin:0 auto;">
        <p class="section-label">Riesgo</p>
        <h2 class="section-title">Lectura rapida del estado actual</h2>
        <div class="benefits-grid">
          <div class="benefit-item"><span class="benefit-emoji">!!</span><h4>Riesgo alto</h4><p>{high_risk}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">!</span><h4>Riesgo medio</h4><p>{medium_risk}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">OK</span><h4>Riesgo bajo</h4><p>{low_risk}</p></div>
          <div class="benefit-item"><span class="benefit-emoji">#</span><h4>Duplicados candidatos</h4><p>{_count_duplicate_candidates(metrics.validations)}</p></div>
        </div>
      </div>
    </section>
    """


def build_dashboard_image_base64(metrics: ChartMetrics, range_label: str) -> str:
    fig = create_dashboard_figure(metrics, range_label)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def create_dashboard_figure(metrics: ChartMetrics, range_label: str):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    fig.suptitle(f"Resumen de auditoria documental ({range_label})", fontsize=14, fontweight="bold")

    doc_labels = ["Factura", "Autorizacion", "Historia", "Certificado", "Otro"]
    doc_values = [
        metrics.document_type_counts.get("factura", 0),
        metrics.document_type_counts.get("autorizacion", 0),
        metrics.document_type_counts.get("historia_clinica", 0),
        metrics.document_type_counts.get("certificado", 0),
        metrics.document_type_counts.get("otro", 0),
    ]
    axes[0].bar(doc_labels, doc_values, color=["#1a6abf", "#1db87a", "#3a8fd4", "#ff9f43", "#8e9aad"])
    axes[0].set_title("Documentos por tipo")
    axes[0].tick_params(axis="x", rotation=18)
    axes[0].set_ylabel("Cantidad")

    comp_labels = [label.title() for label, value in metrics.compliance_counts.items() if value > 0]
    comp_values = [value for value in metrics.compliance_counts.values() if value > 0]
    if not comp_values:
        comp_labels = ["Sin datos"]
        comp_values = [1]
    axes[1].pie(comp_values, labels=comp_labels, autopct="%1.1f%%", startangle=90, colors=["#1db87a", "#ff9f43", "#d9534f"])
    axes[1].set_title("Cumplimiento documental")

    match_labels = list(metrics.match_counts.keys())
    match_values = list(metrics.match_counts.values())
    axes[2].bar(match_labels, match_values, color=["#1a6abf", "#3a8fd4", "#1db87a"])
    axes[2].set_title("Coincidencias de CUPS")
    axes[2].tick_params(axis="x", rotation=20)
    axes[2].set_ylabel("Coincidencias")

    fig.tight_layout()
    return fig


def create_interactive_dashboard_figure(metrics: ChartMetrics, range_label: str):
    timeline = _build_timeline_series(metrics.validations)
    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[[{"type": "scatter"}, {"type": "domain"}], [{"type": "bar"}, {"type": "bar"}]],
        subplot_titles=(
            f"Linea de tiempo ({range_label})",
            "Cumplimiento documental",
            "Documentos por tipo",
            "Coincidencias de CUPS",
        ),
    )

    fig.add_trace(
        go.Scatter(
            x=timeline["dates"],
            y=timeline["counts"],
            mode="lines+markers",
            name="Documentos",
            line=dict(color="#1a6abf", width=3),
        ),
        row=1,
        col=1,
    )

    comp_labels = [label.title() for label, value in metrics.compliance_counts.items() if value > 0]
    comp_values = [value for value in metrics.compliance_counts.values() if value > 0]
    if not comp_values:
        comp_labels = ["Sin datos"]
        comp_values = [1]
    fig.add_trace(
        go.Pie(labels=comp_labels, values=comp_values, hole=0.45, marker=dict(colors=["#1db87a", "#ff9f43", "#d9534f"])),
        row=1,
        col=2,
    )

    fig.add_trace(
        go.Bar(
            x=["Factura", "Autorizacion", "Historia", "Certificado", "Otro"],
            y=[
                metrics.document_type_counts.get("factura", 0),
                metrics.document_type_counts.get("autorizacion", 0),
                metrics.document_type_counts.get("historia_clinica", 0),
                metrics.document_type_counts.get("certificado", 0),
                metrics.document_type_counts.get("otro", 0),
            ],
            marker_color=["#1a6abf", "#1db87a", "#3a8fd4", "#ff9f43", "#8e9aad"],
            name="Tipos",
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=list(metrics.match_counts.keys()),
            y=list(metrics.match_counts.values()),
            marker_color=["#1a6abf", "#3a8fd4", "#1db87a"],
            name="Coincidencias",
        ),
        row=2,
        col=2,
    )

    fig.update_layout(
        height=820,
        title_text=f"Dashboard interactivo de auditoria ({range_label})",
        template="plotly_white",
        showlegend=False,
        margin=dict(l=40, r=40, t=80, b=40),
    )
    fig.update_xaxes(rangeslider_visible=True, row=1, col=1)
    return fig


def filter_documents_by_range(documents: list[SearchDocument], unit: str, amount: int) -> tuple[list[SearchDocument], str]:
    now = datetime.now()
    if unit == "dias":
        threshold = now - timedelta(days=amount)
        label = f"ultimos {amount} dias"
    elif unit == "meses":
        threshold = now - timedelta(days=30 * amount)
        label = f"ultimos {amount} meses"
    elif unit == "anios":
        threshold = now - timedelta(days=365 * amount)
        label = f"ultimos {amount} anios"
    else:
        return documents, "todo el periodo"

    filtered = []
    for doc in documents:
        doc_dt = parse_document_datetime(doc.updated_at)
        if doc_dt and doc_dt >= threshold:
            filtered.append(doc)
    return filtered, label


def parse_document_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %I:%M %p", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _build_bar_section(title: str, items: list[tuple[str, int]], width: int = 28) -> str:
    total = sum(value for _, value in items)
    max_value = max((value for _, value in items), default=0)
    lines = [f"{title}:"]
    for label, value in items:
        filled = round((value / max_value) * width) if max_value else 0
        bar = "#" * filled if filled else "-"
        percent = (value / total * 100) if total else 0.0
        lines.append(f"- {label:<22} [{bar:<{width}}] {value} ({percent:.1f}%)")
    return "\n".join(lines)


def _pick(document: SearchDocument | None, key: str) -> str:
    if not document:
        return ""
    value = document.raw.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _emoji_for_risk(risk_level: str) -> str:
    return {
        "alto": "!!",
        "medio": "!",
        "bajo": "OK",
    }.get(risk_level, "..")


def _build_timeline_series(validations: list) -> dict[str, list]:
    grouped: dict[str, int] = {}
    for item in validations:
        raw_date = (item.latest_date or "").split(" ", 1)[0]
        if not raw_date:
            raw_date = "Sin fecha"
        grouped[raw_date] = grouped.get(raw_date, 0) + 1

    dates = sorted(key for key in grouped.keys() if key != "Sin fecha")
    if "Sin fecha" in grouped:
        dates.append("Sin fecha")
    return {"dates": dates, "counts": [grouped[key] for key in dates]}


def _count_duplicate_candidates(validations: list) -> int:
    seen: dict[tuple[str, str, str], int] = {}
    count = 0
    for item in validations:
        for doc_type, document in item.documents.items():
            key = (item.account_id or "sin_cuenta", doc_type, _pick(document, "cups_code") or "sin_cups")
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                count += 1
    return count
