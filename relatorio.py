import os
import html
import unicodedata
import requests
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

# ============================================================
# CONFIGURAÇÕES / SEGURANÇA
# ============================================================

MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
# Se DEBUG=1, imprime nos logs do Action os valores brutos de status/justification
DEBUG = os.environ.get("DEBUG", "0") == "1"

EMAIL_RECIPIENTS = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]

required_variables = {
    "MOVIDESK_TOKEN": MOVIDESK_TOKEN,
    "EMAIL_USER": EMAIL_USER,
    "EMAIL_PASSWORD": EMAIL_PASSWORD,
    "EMAIL_TO": EMAIL_TO,
}
missing_variables = [n for n, v in required_variables.items() if not v]
if missing_variables:
    raise RuntimeError("Variáveis de ambiente não configuradas: " + ", ".join(missing_variables))
if not EMAIL_RECIPIENTS:
    raise RuntimeError("EMAIL_TO não possui nenhum destinatário válido.")

# ============================================================
# NORMALIZAÇÃO DE TEXTO
# ============================================================

def normalize(text):
    if not text:
        return ""
    text = str(text).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text

# ============================================================
# BUSCAR TICKETS ABERTOS / EM ANDAMENTO (Paginação)
# ============================================================

url_tickets = "https://api.movidesk.com/public/v1/tickets"
tickets = []
skip = 0
top = 500

while True:
    params_tickets = {
        "token": MOVIDESK_TOKEN,
        "$select": "id,subject,status,justification,slaSolutionDate,owner,urgency,clients",
        "$expand": "owner,clients($expand=organization)",
        "$top": str(top),
        "$skip": str(skip),
        "$orderby": "id desc",
    }
    try:
        response_tickets = requests.get(url_tickets, params=params_tickets, timeout=30)
        if not response_tickets.ok:
            print(f"[ERRO] Status HTTP: {response_tickets.status_code}")
            print(f"[ERRO] Corpo da resposta da API: {response_tickets.text}")
            response_tickets.raise_for_status()
        batch = response_tickets.json()
        if not isinstance(batch, list) or not batch:
            break

        for t in batch:
            st_norm = normalize(t.get("status"))
            if not any(term in st_norm for term in ["resol", "fech", "cancel", "closed", "resolved", "cancelled"]):
                tickets.append(t)

        if len(batch) < top:
            break
        skip += top
    except Exception as e:
        print(f"Erro ao buscar tickets: {e}")
        break

print(f"[INFO] Total de tickets abertos/em andamento carregados: {len(tickets)}")

# ============================================================
# PROCESSAMENTO E FILTRAGEM DOS TICKETS
# ============================================================

hoje = datetime.now()
tickets_por_analista = defaultdict(list)


def parse_date(raw_date):
    if not raw_date:
        return None
    try:
        return datetime.fromisoformat(raw_date.replace("Z", "").split(".")[0])
    except Exception:
        return None


for ticket in tickets:
    status_norm = normalize(ticket.get("status"))
    justification_norm = normalize(ticket.get("justification"))
    due_dt = parse_date(ticket.get("slaSolutionDate"))

    match_sprint = "sprint" in status_norm or "sprint" in justification_norm

    is_aguardando = "aguardando" in status_norm or "aguardando" in justification_norm
    is_dev_just = "desenvolvimento" in justification_norm or "desenvolvimento" in status_norm

    is_due_condition_met = False
    if due_dt:
        diff = due_dt - hoje
        if diff.total_seconds() <= 0 or (0 <= diff.days <= 2):
            is_due_condition_met = True
    else:
        is_due_condition_met = True

    match_dev = is_aguardando and is_dev_just and is_due_condition_met

    if match_sprint or match_dev:
        owner = ticket.get("owner")
        analista_nome = "Não Atribuído"
        if isinstance(owner, dict):
            analista_nome = owner.get("businessName") or owner.get("name") or "Não Atribuído"

        ticket["_tipo_alerta"] = "Aguardando Sprint" if match_sprint else "Dev (Próximo/Vencido)"
        tickets_por_analista[analista_nome].append(ticket)

print(f"[INFO] Total de tickets que bateram no filtro Sprint/Dev: "
      f"{sum(len(v) for v in tickets_por_analista.values())}")

tickets_por_analista = dict(sorted(tickets_por_analista.items()))

# ============================================================
# FUNÇÕES AUXILIARES E ORDENAÇÃO POR URGÊNCIA
# ============================================================

def esc(value):
    return html.escape(str(value)) if value is not None else ""


def format_date(raw_date):
    if not raw_date:
        return "-"
    try:
        dt_obj = datetime.fromisoformat(raw_date.replace("Z", "").split(".")[0])
        return dt_obj.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return raw_date


def status_class(status):
    val = normalize(status)
    if "sprint" in val:
        return "status-warning"
    return "status-info"


def urgency_priority(urgency):
    val = normalize(urgency)
    if "urgent" in val:
        return 1
    elif "alt" in val:
        return 2
    elif "media" in val:
        return 3
    elif "baix" in val or "low" in val:
        return 4
    return 5


def urgency_badge(urgency):
    val = normalize(urgency)
    if "urgent" in val:
        return '<span class="urgency-urgent">URGENTE</span>'
    elif "alt" in val:
        return '<span class="urgency-high">ALTA</span>'
    elif "media" in val:
        return '<span class="urgency-medium">MÉDIA</span>'
    elif "baix" in val or "low" in val:
        return '<span class="urgency-low">BAIXA</span>'
    return f'<span class="urgency-normal">{esc(urgency or "Normal")}</span>'


# Ordena os tickets de cada analista por Urgência (1º) e data de SLA (2º)
for analista in tickets_por_analista:
    tickets_por_analista[analista].sort(key=lambda t: (
        urgency_priority(t.get("urgency")),
        parse_date(t.get("slaSolutionDate")) or datetime.max
    ))

# ============================================================
# CÁLCULO DE MÉTRICAS (Geral e por Analista)
# ============================================================

total_geral = 0
total_vencidos_geral = 0
total_hoje_geral = 0
total_alta_geral = 0
total_urgentes_geral = 0

analista_metrics = {}

for analista, t_list in tickets_por_analista.items():
    t_count = len(t_list)
    vencidos_count = 0
    vencem_hoje_count = 0
    alta_count = 0
    urgente_count = 0

    for t in t_list:
        total_geral += 1
        due_dt = parse_date(t.get("slaSolutionDate"))
        if due_dt:
            delta_days = (due_dt.date() - hoje.date()).days
            if delta_days < 0:
                vencidos_count += 1
                total_vencidos_geral += 1
            elif delta_days == 0:
                vencem_hoje_count += 1
                total_hoje_geral += 1

        urg_norm = normalize(t.get("urgency"))
        if "urgent" in urg_norm:
            urgente_count += 1
            total_urgentes_geral += 1
        elif "alt" in urg_norm:
            alta_count += 1
            total_alta_geral += 1

    analista_metrics[analista] = {
        "total": t_count,
        "vencidos": vencidos_count,
        "vencem_hoje": vencem_hoje_count,
        "alta": alta_count,
        "urgente": urgente_count
    }

# ============================================================
# MONTAGEM DO HTML E E-MAIL (Design Estilo Dashboard Moderno)
# ============================================================

data_atual_str = hoje.strftime("%d/%m/%Y")

html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Relatório de Tickets - Sprint & Desenvolvimento</title>
<style>
body {{
    margin: 0;
    padding: 0;
    background-color: #f0f3f8;
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    color: #1e293b;
}}
.wrapper {{
    width: 100%;
    padding: 40px 0;
}}
.container {{
    max-width: 1100px;
    margin: 0 auto;
    background: #f8fafc;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: 0 10px 30px rgba(0,0,0,0.06);
    padding: 30px;
}}
.header-card {{
    background: linear-gradient(135deg, #0ea5e9, #2563eb);
    color: #ffffff;
    border-radius: 16px;
    padding: 32px;
    margin-bottom: 24px;
    box-shadow: 0 4px 15px rgba(37, 99, 235, 0.2);
}}
.eyebrow {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    opacity: 0.85;
    margin-bottom: 8px;
}}
.title {{
    margin: 0;
    font-size: 28px;
    font-weight: 700;
}}
.subtitle {{
    margin: 8px 0 0;
    font-size: 14px;
    opacity: 0.9;
}}
.metrics-grid {{
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
    flex-wrap: wrap;
}}
.metric-card {{
    background: #ffffff;
    border-radius: 16px;
    padding: 20px;
    flex: 1;
    min-width: 180px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.03);
    border-top: 4px solid #cbd5e1;
}}
.metric-card.total {{ border-top-color: #3b82f6; }}
.metric-card.vencidos {{ border-top-color: #ef4444; }}
.metric-card.hoje {{ border-top-color: #f59e0b; }}
.metric-card.alta {{ border-top-color: #f97316; }}
.metric-card.urgente {{ border-top-color: #0f172a; }}

.metric-title {{
    font-size: 11px;
    text-transform: uppercase;
    color: #64748b;
    font-weight: 700;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
}}
.metric-value {{
    font-size: 26px;
    font-weight: 700;
    color: #0f172a;
}}
.analyst-section {{
    background: #ffffff;
    border-radius: 16px;
    margin-bottom: 24px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.03);
    overflow: hidden;
    border: 1px solid #e2e8f0;
}}
.analyst-header {{
    background: #f8fafc;
    padding: 20px 24px;
    border-bottom: 1px solid #e2e8f0;
    display: flex;
    flex-direction: column;
    gap: 12px;
}}
.analyst-title-name {{
    font-size: 18px;
    font-weight: 700;
    color: #0f172a;
}}
.analyst-badge-container {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}}
.analyst-badge {{
    padding: 6px 12px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 11px;
}}
.table-wrapper {{
    width: 100%;
    overflow-x: auto;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    background: #ffffff;
}}
th {{
    background: #f8fafc;
    color: #64748b;
    font-size: 11px;
    font-weight: 700;
    text-align: left;
    padding: 14px 12px;
    border-bottom: 1px solid #e2e8f0;
    white-space: nowrap;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
td {{
    padding: 14px 12px;
    border-bottom: 1px solid #f1f5f9;
    vertical-align: middle;
    color: #334155;
}}
.status {{
    display: inline-block;
    padding: 6px 10px;
    border-radius: 20px;
    font-size: 10px;
    font-weight: 700;
    white-space: nowrap;
}}
.status-warning {{ background: #fef3c7; color: #b45309; }}
.status-info {{ background: #eff6ff; color: #1d4ed8; }}

.urgency-urgent {{ background: #0f172a; color: #ffffff; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 10px; }}
.urgency-high {{ background: #fee2e2; color: #991b1b; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 10px; }}
.urgency-medium {{ background: #fef3c7; color: #b45309; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 10px; }}
.urgency-low {{ background: #ecfdf5; color: #047857; padding: 4px 8px; border-radius: 6px; font-weight: 700; font-size: 10px; }}
.urgency-normal {{ color: #475569; font-size: 11px; font-weight: 600; }}

.footer {{
    padding: 20px;
    text-align: center;
    font-size: 12px;
    color: #64748b;
    background: transparent;
    font-weight: 500;
}}
</style>
</head>
<body>
<div class="wrapper">
<div class="container">

<div class="header-card">
    <div class="eyebrow">VIDYA CODE • SUPORTE & ENGENHARIA</div>
    <h1 class="title">Relatório: Sprint & Desenvolvimento</h1>
    <p class="subtitle">Data de geração: <strong>{data_atual_str}</strong> | Organizado por Analista</p>
</div>

<div class="metrics-grid">
    <div class="metric-card total">
        <div class="metric-title">Total Geral</div>
        <div class="metric-value">{total_geral}</div>
    </div>
    <div class="metric-card vencidos">
        <div class="metric-title">Vencidos</div>
        <div class="metric-value" style="color: #ef4444;">{total_vencidos_geral}</div>
    </div>
    <div class="metric-card hoje">
        <div class="metric-title">Vencem Hoje</div>
        <div class="metric-value" style="color: #f59e0b;">{total_hoje_geral}</div>
    </div>
    <div class="metric-card alta">
        <div class="metric-title">Prioridade Alta</div>
        <div class="metric-value" style="color: #f97316;">{total_alta_geral}</div>
    </div>
    <div class="metric-card urgente">
        <div class="metric-title">Prioridade Urgente</div>
        <div class="metric-value" style="color: #0f172a;">{total_urgentes_geral}</div>
    </div>
</div>
"""

if not tickets_por_analista:
    html_content += """
    <div style="background: #ffffff; border-radius: 16px; text-align: center; padding: 50px; color: #64748b; font-size: 14px; box-shadow: 0 4px 15px rgba(0,0,0,0.03);">
        Nenhum ticket encontrado nos critérios de "Sprint" ou "Desenvolvimento (Próximo/Vencido)".
    </div>
    """
else:
    for analista, t_list in tickets_por_analista.items():
        m = analista_metrics[analista]
        html_content += f"""
        <div class="analyst-section">
            <div class="analyst-header">
                <div class="analyst-title-name">👤 {esc(analista)}</div>
                <div class="analyst-badge-container">
                    <span class="analyst-badge" style="background: #f1f5f9; color: #334155;">TOTAL: {m["total"]}</span>
                    <span class="analyst-badge" style="background: #fee2e2; color: #991b1b;">VENCIDOS: {m["vencidos"]}</span>
                    <span class="analyst-badge" style="background: #fef3c7; color: #b45309;">VENCEM HOJE: {m["vencem_hoje"]}</span>
                    <span class="analyst-badge" style="background: #ffedd5; color: #9a3412;">PRIORIDADE ALTA: {m["alta"]}</span>
                    <span class="analyst-badge" style="background: #0f172a; color: #ffffff;">PRIORIDADE URGENTE: {m["urgente"]}</span>
                </div>
            </div>
            <div class="table-wrapper">
            <table>
            <thead>
            <tr>
                <th>ID</th>
                <th>Urgência</th>
                <th>Organização / Solicitante</th>
                <th>Assunto</th>
                <th>Status</th>
                <th>Justificativa</th>
                <th>Vencimento (SLA)</th>
            </tr>
            </thead>
            <tbody>
        """
        for t in t_list:
            t_id = t.get("id")
            urgency_raw = t.get("urgency")
            status = t.get("status") or "-"
            justification = t.get("justification") or "-"
            due_formatted = format_date(t.get("slaSolutionDate"))
            
            row_style = ""
            due_dt = parse_date(t.get("slaSolutionDate"))
            if due_dt:
                due_date_only = due_dt.date()
                hoje_date_only = hoje.date()
                delta_days = (due_date_only - hoje_date_only).days
                
                if delta_days <= 0:
                    row_style = 'style="background-color: #fff1f2;"'
                elif delta_days in (1, 2):
                    row_style = 'style="background-color: #fefce8;"'

            clients = t.get("clients", [])
            organizacao = "-"
            solicitante = "-"
            if isinstance(clients, list) and clients:
                cliente = clients[0]
                if isinstance(cliente, dict):
                    solicitante = cliente.get("businessName") or "-"
                    org_obj = cliente.get("organization")
                    if isinstance(org_obj, dict):
                        organizacao = org_obj.get("businessName") or org_obj.get("name") or "-"

            urg_badge = urgency_badge(urgency_raw)
            status_css = status_class(status)

            just_norm = normalize(justification)
            if justification != "-":
                if "sprint" in just_norm:
                    just_badge = f'<span style="background: #e0f2fe; color: #0369a1; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block;">{esc(justification)}</span>'
                elif "desenvolvimento" in just_norm:
                    just_badge = f'<span style="background: #fee2e2; color: #991b1b; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block;">{esc(justification)}</span>'
                else:
                    just_badge = f'<span style="background: #f1f5f9; color: #334155; padding: 4px 8px; border-radius: 6px; font-size: 11px; display: inline-block;">{esc(justification)}</span>'
            else:
                just_badge = '<span style="color: #94a3b8;">-</span>'

            html_content += f"""
            <tr {row_style}>
                <td style="font-weight: 700; color: #475569;">{esc(t_id)}</td>
                <td>{urg_badge}</td>
                <td><strong style="color: #0f172a;">{esc(organizacao)}</strong><br><span style="font-size: 11px; color: #64748b;">{esc(solicitante)}</span></td>
                <td style="max-width: 240px; line-height: 1.4; color: #1e293b;">{esc(t.get("subject") or "-")}</td>
                <td><span class="status {status_css}">{esc(status)}</span></td>
                <td style="max-width: 200px; line-height: 1.4;">{just_badge}</td>
                <td style="white-space: nowrap; font-weight: 500;">{esc(due_formatted)}</td>
            </tr>
            """
        html_content += """
            </tbody>
            </table>
            </div>
        </div>
        """

html_content += """
<div class="footer">
    Relatório automático • Movidesk • Vidya Code
</div>
</div>
</div>
</body>
</html>
"""

# ============================================================
# ENVIO DO E-MAIL
# ============================================================

msg = MIMEMultipart()
msg["From"] = EMAIL_USER
msg["To"] = ", ".join(EMAIL_RECIPIENTS)
msg["Subject"] = f"Relatório - Tickets Sprint & Desenvolvimento ({data_atual_str})"

msg.attach(MIMEText(html_content, "html", "utf-8"))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_RECIPIENTS, msg.as_string())
    print("E-mail enviado com sucesso!")
except Exception as e:
    print(f"Erro ao enviar e-mail: {e}")
