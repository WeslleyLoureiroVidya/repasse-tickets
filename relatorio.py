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
# Remove acentos, espaços extras e força minúsculo. Isso evita que
# diferenças de acentuação/maiúsculas façam o "in" falhar silenciosamente,
# que era a causa mais provável de nenhum ticket estar sendo encontrado.

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
            # Imprime o corpo da resposta: é aqui que a Movidesk explica
            # exatamente qual parâmetro do $select/$expand é inválido.
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

if DEBUG:
    print("[DEBUG] Amostra dos valores brutos de status/justification (até 20 tickets):")
    for t in tickets[:20]:
        print(
            f"  id={t.get('id')} | status={t.get('status')!r} | "
            f"justification={t.get('justification')!r} | slaSolutionDate={t.get('slaSolutionDate')!r}"
        )
    unique_status = sorted({t.get("status") for t in tickets if t.get("status")})
    unique_just = sorted({t.get("justification") for t in tickets if t.get("justification")})
    print(f"[DEBUG] Valores únicos de status encontrados: {unique_status}")
    print(f"[DEBUG] Valores únicos de justification encontrados: {unique_just}")

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
# FUNÇÕES AUXILIARES DE HTML
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

# ============================================================
# MONTAGEM DO HTML E E-MAIL
# ============================================================

data_atual_str = hoje.strftime("%d/%m/%Y")

html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Relatório de Tickets - Sprint & Desenvolvimento</title>
<style>
body {{ margin: 0; padding: 0; background-color: #f4f6f8; font-family: Arial, sans-serif; color: #202124; }}
.wrapper {{ width: 100%; padding: 30px 0; }}
.container {{ max-width: 1200px; margin: 0 auto; background: #ffffff; border-radius: 14px; overflow: hidden; box-shadow: 0 3px 14px rgba(0,0,0,0.07); }}
.header {{ padding: 28px 32px; border-bottom: 1px solid #e8eaed; background: #ffffff; }}
.eyebrow {{ font-size: 12px; font-weight: bold; letter-spacing: 1.2px; color: #6b7280; text-transform: uppercase; margin-bottom: 8px; }}
.title {{ margin: 0; font-size: 26px; color: #1f2937; }}
.subtitle {{ margin: 8px 0 0; font-size: 14px; color: #6b7280; }}
.content {{ padding: 26px 32px 32px; }}
.analyst-section {{ margin-bottom: 30px; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; background: #fafafa; }}
.analyst-header {{ background: #f3f4f6; padding: 14px 20px; font-size: 16px; font-weight: bold; color: #1f2937; border-bottom: 1px solid #e5e7eb; }}
.table-wrapper {{ width: 100%; overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; background: #ffffff; }}
th {{ background: #f8fafc; color: #6b7280; font-size: 11px; font-weight: bold; text-align: left; padding: 12px 10px; border-bottom: 1px solid #e5e7eb; white-space: nowrap; }}
td {{ padding: 12px 10px; border-bottom: 1px solid #f0f1f3; vertical-align: middle; color: #374151; }}
.status {{ display: inline-block; padding: 5px 9px; border-radius: 20px; font-size: 10px; font-weight: bold; white-space: nowrap; }}
.status-warning {{ background: #fff6df; color: #a15c00; }}
.status-info {{ background: #edf4ff; color: #2457a6; }}
.badge-tag {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; }}
.badge-sprint {{ background: #e0f2fe; color: #0369a1; }}
.badge-dev {{ background: #fee2e2; color: #991b1b; }}
.urgency-urgent {{ background: #111827; color: #ffffff; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
.urgency-high {{ background: #fdecec; color: #b42318; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
.urgency-medium {{ background: #fff6df; color: #a15c00; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
.urgency-low {{ background: #e9f7ef; color: #18794e; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
.urgency-normal {{ color: #4b5563; font-size: 11px; }}
.footer {{ padding: 18px 32px; border-top: 1px solid #e5e7eb; background: #fafafa; font-size: 11px; color: #9ca3af; text-align: center; }}
</style>
</head>
<body>
<div class="wrapper">
<div class="container">
<div class="header">
    <div class="eyebrow">VIDYA CODE • SUPORTE</div>
    <h1 class="title">Relatório: Sprint & Desenvolvimento</h1>
    <p class="subtitle">Data de geração: <strong>{data_atual_str}</strong> | Separado por analista</p>
</div>
<div class="content">
"""

if not tickets_por_analista:
    html_content += """
    <div style="text-align: center; padding: 40px; color: #6b7280; font-size: 14px;">
        Nenhum ticket encontrado nos critérios de "Sprint" ou "Desenvolvimento (Próximo/Vencido)".
    </div>
    """
else:
    for analista, t_list in tickets_por_analista.items():
        html_content += f"""
        <div class="analyst-section">
            <div class="analyst-header">👤 {esc(analista)} ({len(t_list)} ticket(s))</div>
            <div class="table-wrapper">
            <table>
            <thead>
            <tr>
                <th>ID</th>
                <th>TIPO</th>
                <th>URGÊNCIA</th>
                <th>ORGANIZAÇÃO / SOLICITANTE</th>
                <th>ASSUNTO</th>
                <th>STATUS</th>
                <th>JUSTIFICATIVA</th>
                <th>VENCIMENTO (SLA)</th>
            </tr>
            </thead>
            <tbody>
        """
        for t in t_list:
            t_id = t.get("id")
            urgency_raw = t.get("urgency")
            status = t.get("status") or "-"
            justification = t.get("justification") or "-"
            tipo_alerta = t.get("_tipo_alerta", "-")
            due_formatted = format_date(t.get("slaSolutionDate"))

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

            badge_css = "badge-sprint" if "Sprint" in tipo_alerta else "badge-dev"
            urg_badge = urgency_badge(urgency_raw)
            status_css = status_class(status)

            html_content += f"""
            <tr>
                <td style="font-weight: bold; color: #4b5563;">{esc(t_id)}</td>
                <td><span class="badge-tag {badge_css}">{esc(tipo_alerta)}</span></td>
                <td>{urg_badge}</td>
                <td><strong style="color: #1f2937;">{esc(organizacao)}</strong><br><span style="font-size: 11px; color: #6b7280;">{esc(solicitante)}</span></td>
                <td style="max-width: 240px; line-height: 1.4;">{esc(t.get("subject") or "-")}</td>
                <td><span class="status {status_css}">{esc(status)}</span></td>
                <td style="max-width: 200px; color: #6b7280; line-height: 1.4;">{esc(justification)}</td>
                <td style="white-space: nowrap;">{esc(due_formatted)}</td>
            </tr>
            """
        html_content += """
            </tbody>
            </table>
            </div>
        </div>
        """

html_content += """
</div>
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
