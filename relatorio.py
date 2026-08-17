import os
import requests
import html
import re
from datetime import datetime, timedelta
from collections import Counter
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============================================================
# CONFIGURAÇÕES
# ============================================================
MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO", "").split(",")

# ============================================================
# BUSCAR TICKETS
# ============================================================
hoje = datetime.now()
sete_dias_atras = hoje - timedelta(days=7)
data_filtro = sete_dias_atras.strftime("%Y-%m-%dT00:00:00Z")

url_tickets = "https://api.movidesk.com/public/v1/tickets"
params = {
    "token": MOVIDESK_TOKEN,
    "$filter": f"createdDate ge {data_filtro}",
    # OBS: a API do Movidesk não tem uma propriedade "service" no Ticket.
    # O nome correto é "serviceFull" (array com a hierarquia do serviço,
    # ex: ["ETL", "Extractor development"]) ou serviceFirstLevel/
    # serviceSecondLevel/serviceThirdLevel.
    "$select": "subject,serviceFull,category",
    "$top": "1000" # Importante: limitar o top para não estourar
}

print(f"[DEBUG] Buscando tickets desde: {data_filtro}")
response = requests.get(url_tickets, params=params)

if not response.ok:
    print(f"[ERRO] Falha na API Movidesk: {response.status_code} - {response.text}")
    tickets = []
else:
    tickets = response.json()
    print(f"[DEBUG] Total de tickets encontrados: {len(tickets)}")

# ============================================================
# PROCESSAMENTO
# ============================================================
categorias = Counter()
servicos = Counter()
palavras = Counter()
STOPWORDS = {"de", "a", "o", "que", "e", "do", "da", "em", "um", "para", "é", "com", "não", "uma", "os", "no", "se", "na", "por", "mais", "as", "dos", "dos", "como", "mas", "foi", "ao", "ele", "das", "tem", "seu", "sua", "ou", "ser", "quando", "muito", "nos", "já", "está", "eu"}

def limpar_texto(texto):
    if not texto: return []
    texto = texto.lower()
    texto = re.sub(r'[^a-z0-9\s]', '', texto)
    return texto.split()

def nome_servico(service_full):
    # serviceFull é um array com a hierarquia, ex: ["ETL", "Extractor development"]
    # Usamos o último item, que é o serviço mais específico selecionado no ticket.
    if service_full and isinstance(service_full, list) and len(service_full) > 0:
        return service_full[-1]
    return "Sem Serviço"

for t in tickets:
    cat = t.get("category") or "Sem Categoria"
    svc = nome_servico(t.get("serviceFull"))
    sub = t.get("subject", "")

    categorias[cat] += 1
    servicos[svc] += 1
    
    for palavra in limpar_texto(sub):
        if palavra not in STOPWORDS and len(palavra) > 3:
            palavras[palavra] += 1

# ============================================================
# MONTAGEM DO HTML
# ============================================================
def criar_tabela(titulo, contador):
    if not contador: return f"<h3>{titulo}</h3><p>Nenhum dado encontrado.</p>"
    html_tab = f"<h3>{titulo}</h3><table style='width:100%; border-collapse:collapse; margin-bottom:20px;'>"
    html_tab += "<tr><th style='text-align:left; border-bottom:2px solid #ddd;'>Item</th><th style='text-align:right; border-bottom:2px solid #ddd;'>Qtd</th></tr>"
    for item, qtd in contador.most_common(10):
        html_tab += f"<tr><td style='padding:5px 0;'>{html.escape(str(item))}</td><td style='text-align:right;'>{qtd}</td></tr>"
    html_tab += "</table>"
    return html_tab

html_body = f"""
<h1>Relatório Semanal de Gargalos ({sete_dias_atras.strftime('%d/%m')} a {hoje.strftime('%d/%m')})</h1>
<p>Total de tickets processados: <strong>{len(tickets)}</strong></p>
{criar_tabela("Top Categorias", categorias)}
{criar_tabela("Top Serviços", servicos)}
{criar_tabela("Palavras-Chave Frequentes (Assuntos)", palavras)}
"""

# ============================================================
# ENVIO (Só envia se tiver ticket)
# ============================================================
if not tickets:
    print("[INFO] Nenhum ticket encontrado, abortando envio de e-mail para evitar spam.")
else:
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = ", ".join(EMAIL_TO)
    msg["Subject"] = f"📊 Relatório Semanal: Assuntos Frequentes ({hoje.strftime('%d/%m')})"
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
    print("[INFO] E-mail enviado com sucesso!")
