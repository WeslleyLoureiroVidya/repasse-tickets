import os
import html
import requests
import sys
from datetime import datetime, timedelta
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

EMAIL_RECIPIENTS = [email.strip() for email in EMAIL_TO.split(",") if email.strip()]

# ============================================================
# BUSCA E PROCESSAMENTO DE TICKETS
# ============================================================

url_tickets = "https://api.movidesk.com/public/v1/tickets"
tickets_filtrados = []
skip = 0
top = 500

# Buscamos tickets abertos para filtrar no Python
while True:
    params_tickets = {
        "token": MOVIDESK_TOKEN,
        "$select": "id,subject,status,justification,dueDate,owner,urgency,clients",
        "$expand": "owner,clients($expand=organization)",
        "$top": str(top),
        "$skip": str(skip),
        "$orderby": "id desc"
    }
    try:
        response_tickets = requests.get(url_tickets, params=params_tickets, timeout=30)
        response_tickets.raise_for_status()
        batch = response_tickets.json()
        if not isinstance(batch, list) or not batch:
            break
        
        # Filtro: Pega apenas status que contêm "aguardando" (case insensitive)
        for t in batch:
            status = (t.get("status") or "").lower()
            if "aguardando" in status:
                tickets_filtrados.append(t)
                
        if len(batch) < top:
            break
        skip += top
    except Exception as e:
        print(f"Erro ao buscar tickets: {e}")
        break

# Agrupamento por analista
tickets_por_analista = defaultdict(list)
for t in tickets_filtrados:
    owner = t.get("owner")
    analista_nome = "Não Atribuído"
    if isinstance(owner, dict):
        analista_nome = owner.get("businessName") or owner.get("name") or "Não Atribuído"
    tickets_por_analista[analista_nome].append(t)

tickets_por_analista = dict(sorted(tickets_por_analista.items()))

# ============================================================
# MONTAGEM DO HTML E E-MAIL
# ============================================================

def esc(value): return html.escape(str(value)) if value is not None else ""
def format_date(raw_date):
    if not raw_date: return "-"
    try:
        dt_obj = datetime.fromisoformat(raw_date.replace("Z", "").split(".")[0])
        return dt_obj.strftime("%d/%m/%Y")
    except: return raw_date

html_content = f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333;">
<h2>Relatório de Tickets: Status "Aguardando"</h2>
<p>Data: {datetime.now().strftime("%d/%m/%Y")}</p>
"""

if not tickets_por_analista:
    html_content += "<p>Nenhum ticket com status 'Aguardando' encontrado.</p>"
else:
    for analista, t_list in tickets_por_analista.items():
        html_content += f"<h3 style='background:#f3f4f6; padding:10px;'>👤 {esc(analista)} ({len(t_list)} tickets)</h3>"
        html_content += "<table border='1' style='border-collapse:collapse; width:100%; font-size:12px;'>"
        html_content += "<tr style='background:#eee;'><th>ID</th><th>Status</th><th>Assunto</th><th>Vencimento</th></tr>"
        for t in t_list:
            html_content += f"""
            <tr>
                <td>{esc(t.get('id'))}</td>
                <td>{esc(t.get('status'))}</td>
                <td>{esc(t.get('subject'))}</td>
                <td>{format_date(t.get('dueDate'))}</td>
            </tr>
            """
        html_content += "</table><br>"

html_content += "</body></html>"

# ============================================================
# ENVIO
# ============================================================

msg = MIMEMultipart()
msg["From"] = EMAIL_USER
msg["To"] = ", ".join(EMAIL_RECIPIENTS)
msg["Subject"] = "Relatório de Tickets - Status Aguardando"
msg.attach(MIMEText(html_content, "html", "utf-8"))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_RECIPIENTS, msg.as_string())
    print("Sucesso!")
except Exception as e:
    print(f"Erro ao enviar: {e}")
