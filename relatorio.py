import os
import html
import requests
import smtplib
import unicodedata

from datetime import datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ============================================================
# CONFIGURAÇÕES / SEGURANÇA
# ============================================================

MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

EMAIL_RECIPIENTS = [
    email.strip()
    for email in EMAIL_TO.split(",")
    if email.strip()
]


# ============================================================
# VALIDAÇÃO DAS CONFIGURAÇÕES
# ============================================================

required_variables = {
    "MOVIDESK_TOKEN": MOVIDESK_TOKEN,
    "EMAIL_USER": EMAIL_USER,
    "EMAIL_PASSWORD": EMAIL_PASSWORD,
    "EMAIL_TO": EMAIL_TO,
}

missing_variables = [
    name
    for name, value in required_variables.items()
    if not value
]

if missing_variables:
    raise RuntimeError(
        "Variáveis de ambiente não configuradas: "
        + ", ".join(missing_variables)
    )

if not EMAIL_RECIPIENTS:
    raise RuntimeError(
        "EMAIL_TO não possui nenhum destinatário válido."
    )


# ============================================================
# CONFIGURAÇÃO MOVIDESK
# ============================================================

URL_TICKETS = "https://api.movidesk.com/public/v1/tickets"

TOP = 500
SKIP = 0

# Status e justificativas que devem ser encontrados
STATUS_ALVO = "aguardando"

JUSTIFICATIVAS_ALVO = {
    "sprint",
    "equipe de desenvolvimento",
}


# ============================================================
# FUNÇÃO PARA NORMALIZAR TEXTOS
# ============================================================

def normalizar(texto):
    """
    Normaliza o texto para facilitar a comparação.

    Exemplos:
    'Aguardando' -> 'aguardando'
    ' AGUARDANDO ' -> 'aguardando'
    'Equipe de Desenvolvimento' -> 'equipe de desenvolvimento'
    """

    if texto is None:
        return ""

    texto = str(texto).strip().lower()

    texto = unicodedata.normalize(
        "NFKD",
        texto
    ).encode(
        "ascii",
        "ignore"
    ).decode(
        "ascii"
    )

    return " ".join(texto.split())


# ============================================================
# BUSCAR TODOS OS TICKETS
# ============================================================

tickets = []

print("============================================================")
print("INICIANDO CONSULTA AO MOVIDESK")
print("============================================================")

while True:

    params_tickets = {
        "token": MOVIDESK_TOKEN,

        "$select": (
            "id,"
            "subject,"
            "status,"
            "justification,"
            "dueDate,"
            "owner,"
            "urgency,"
            "clients"
        ),

        "$expand": (
            "owner,"
            "clients($expand=organization)"
        ),

        "$top": str(TOP),
        "$skip": str(SKIP),
        "$orderby": "id desc",
    }

    try:

        print(
            f"Consultando tickets: "
            f"skip={SKIP}, top={TOP}"
        )

        response = requests.get(
            URL_TICKETS,
            params=params_tickets,
            timeout=60
        )

        print(
            f"HTTP {response.status_code}"
        )

        response.raise_for_status()

        batch = response.json()

        if not isinstance(batch, list):
            raise RuntimeError(
                "A API do Movidesk não retornou uma lista de tickets."
            )

        if not batch:
            print(
                "Nenhum ticket adicional retornado."
            )
            break

        tickets.extend(batch)

        print(
            f"Tickets recebidos nesta página: {len(batch)}"
        )

        print(
            f"Total acumulado: {len(tickets)}"
        )

        # Se retornou menos que o limite, chegamos ao final
        if len(batch) < TOP:
            break

        SKIP += TOP

    except requests.exceptions.RequestException as e:

        print(
            "ERRO AO CONSULTAR O MOVIDESK:"
        )

        print(e)

        if hasattr(e, "response") and e.response is not None:

            print(
                "Resposta da API:"
            )

            print(
                e.response.text
            )

        raise


# ============================================================
# DIAGNÓSTICO DOS DADOS RETORNADOS
# ============================================================

print()
print("============================================================")
print("DIAGNÓSTICO DOS TICKETS")
print("============================================================")

print(
    f"Total de tickets recebidos da API: {len(tickets)}"
)


# Lista status encontrados
status_encontrados = sorted(
    {
        str(ticket.get("status") or "").strip()
        for ticket in tickets
        if ticket.get("status")
    }
)

print()
print("STATUS ENCONTRADOS:")

for status in status_encontrados:
    print(f" - {status}")


# Lista justificativas encontradas
justificativas_encontradas = sorted(
    {
        str(ticket.get("justification") or "").strip()
        for ticket in tickets
        if ticket.get("justification")
    }
)

print()
print("JUSTIFICATIVAS ENCONTRADAS:")

for justification in justificativas_encontradas:
    print(f" - {justification}")


# ============================================================
# FILTRAMENTO
# ============================================================

tickets_filtrados = []

tickets_por_analista = defaultdict(list)

print()
print("============================================================")
print("APLICANDO FILTRO")
print("============================================================")

for ticket in tickets:

    status_original = ticket.get("status") or ""
    justification_original = ticket.get("justification") or ""

    status = normalizar(status_original)
    justification = normalizar(justification_original)

    # --------------------------------------------------------
    # REGRA PRINCIPAL
    #
    # STATUS = AGUARDANDO
    #
    # E
    #
    # JUSTIFICATIVA =
    #   SPRINT
    #   OU
    #   EQUIPE DE DESENVOLVIMENTO
    # --------------------------------------------------------

    status_ok = status == STATUS_ALVO

    justification_ok = (
        justification in JUSTIFICATIVAS_ALVO
    )

    if not (status_ok and justification_ok):
        continue

    # Define o tipo do ticket
    if justification == "sprint":
        tipo_alerta = "Aguardando Sprint"

    elif justification == "equipe de desenvolvimento":
        tipo_alerta = "Aguardando Desenvolvimento"

    else:
        tipo_alerta = "Aguardando"

    ticket["_tipo_alerta"] = tipo_alerta

    tickets_filtrados.append(ticket)

    # --------------------------------------------------------
    # RESPONSÁVEL
    # --------------------------------------------------------

    owner = ticket.get("owner")

    analista_nome = "Não Atribuído"

    if isinstance(owner, dict):

        analista_nome = (
            owner.get("businessName")
            or owner.get("name")
            or "Não Atribuído"
        )

    tickets_por_analista[
        analista_nome
    ].append(ticket)


# Ordenar por nome do analista
tickets_por_analista = dict(
    sorted(tickets_por_analista.items())
)


# ============================================================
# RESULTADO DO FILTRO
# ============================================================

print()
print("============================================================")
print("RESULTADO")
print("============================================================")

print(
    f"Tickets encontrados com os critérios: "
    f"{len(tickets_filtrados)}"
)

print()

for ticket in tickets_filtrados:

    print(
        f"#{ticket.get('id')} | "
        f"Status: {ticket.get('status')} | "
        f"Justificativa: {ticket.get('justification')} | "
        f"Assunto: {ticket.get('subject')}"
    )


# ============================================================
# FUNÇÕES AUXILIARES DE HTML
# ============================================================

def esc(value):

    if value is None:
        return ""

    return html.escape(
        str(value)
    )


def format_date(raw_date):

    if not raw_date:
        return "-"

    try:

        dt_obj = datetime.fromisoformat(
            raw_date.replace("Z", "+00:00")
        )

        return dt_obj.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:

        return raw_date


def urgency_badge(urgency):

    val = normalizar(
        urgency
    )

    if "urgent" in val:

        return (
            '<span class="urgency-urgent">'
            'URGENTE'
            '</span>'
        )

    elif "alt" in val:

        return (
            '<span class="urgency-high">'
            'ALTA'
            '</span>'
        )

    elif "media" in val:

        return (
            '<span class="urgency-medium">'
            'MÉDIA'
            '</span>'
        )

    elif "baix" in val:

        return (
            '<span class="urgency-low">'
            'BAIXA'
            '</span>'
        )

    return (
        '<span class="urgency-normal">'
        f'{esc(urgency or "Normal")}'
        '</span>'
    )


# ============================================================
# HTML DO E-MAIL
# ============================================================

fuso_horario = ZoneInfo(
    "America/Recife"
)

hoje = datetime.now(
    fuso_horario
)

data_atual_str = hoje.strftime(
    "%d/%m/%Y"
)


html_content = f"""
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<title>
Relatório de Tickets - Sprint & Desenvolvimento
</title>

<style>

body {{
    margin: 0;
    padding: 0;
    background-color: #f4f6f8;
    font-family: Arial, sans-serif;
    color: #202124;
}}

.wrapper {{
    width: 100%;
    padding: 30px 0;
}}

.container {{
    max-width: 1200px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 3px 14px rgba(0,0,0,0.07);
}}

.header {{
    padding: 28px 32px;
    border-bottom: 1px solid #e8eaed;
    background: #ffffff;
}}

.eyebrow {{
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 1.2px;
    color: #6b7280;
    text-transform: uppercase;
    margin-bottom: 8px;
}}

.title {{
    margin: 0;
    font-size: 26px;
    color: #1f2937;
}}

.subtitle {{
    margin: 8px 0 0;
    font-size: 14px;
    color: #6b7280;
}}

.content {{
    padding: 26px 32px 32px;
}}

.summary {{
    display: flex;
    gap: 15px;
    margin-bottom: 25px;
}}

.summary-card {{
    flex: 1;
    padding: 18px;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    background: #fafafa;
}}

.summary-number {{
    font-size: 26px;
    font-weight: bold;
    color: #1f2937;
}}

.summary-label {{
    font-size: 12px;
    color: #6b7280;
    margin-top: 5px;
}}

.analyst-section {{
    margin-bottom: 30px;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    overflow: hidden;
    background: #fafafa;
}}

.analyst-header {{
    background: #f3f4f6;
    padding: 14px 20px;
    font-size: 16px;
    font-weight: bold;
    color: #1f2937;
    border-bottom: 1px solid #e5e7eb;
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
    color: #6b7280;
    font-size: 11px;
    font-weight: bold;
    text-align: left;
    padding: 12px 10px;
    border-bottom: 1px solid #e5e7eb;
    white-space: nowrap;
}}

td {{
    padding: 12px 10px;
    border-bottom: 1px solid #f0f1f3;
    vertical-align: middle;
    color: #374151;
}}

.badge-tag {{
    display: inline-block;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: bold;
}}

.badge-sprint {{
    background: #e0f2fe;
    color: #0369a1;
}}

.badge-dev {{
    background: #fee2e2;
    color: #991b1b;
}}

.status {{
    display: inline-block;
    padding: 5px 9px;
    border-radius: 20px;
    font-size: 10px;
    font-weight: bold;
    white-space: nowrap;
}}

.status-waiting {{
    background: #fff6df;
    color: #a15c00;
}}

.urgency-urgent {{
    background: #111827;
    color: #ffffff;
    padding: 4px 8px;
    border-radius: 4px;
    font-weight: bold;
    font-size: 10px;
}}

.urgency-high {{
    background: #fdecec;
    color: #b42318;
    padding: 4px 8px;
    border-radius: 4px;
    font-weight: bold;
    font-size: 10px;
}}

.urgency-medium {{
    background: #fff6df;
    color: #a15c00;
    padding: 4px 8px;
    border-radius: 4px;
    font-weight: bold;
    font-size: 10px;
}}

.urgency-low {{
    background: #e9f7ef;
    color: #18794e;
    padding: 4px 8px;
    border-radius: 4px;
    font-weight: bold;
    font-size: 10px;
}}

.urgency-normal {{
    color: #4b5563;
    font-size: 11px;
}}

.footer {{
    padding: 18px 32px;
    border-top: 1px solid #e5e7eb;
    background: #fafafa;
    font-size: 11px;
    color: #9ca3af;
    text-align: center;
}}

</style>

</head>

<body>

<div class="wrapper">

<div class="container">

<div class="header">

    <div class="eyebrow">
        VIDYA CODE • SUPORTE
    </div>

    <h1 class="title">
        Relatório: Sprint & Desenvolvimento
    </h1>

    <p class="subtitle">
        Data de geração:
        <strong>{data_atual_str}</strong>
        |
        Tickets em "Aguardando"
    </p>

</div>

<div class="content">

<div class="summary">

    <div class="summary-card">

        <div class="summary-number">
            {len(tickets_filtrados)}
        </div>

        <div class="summary-label">
            Total de tickets encontrados
        </div>

    </div>

</div>
"""


# ============================================================
# TICKETS NO HTML
# ============================================================

if not tickets_por_analista:

    html_content += """

    <div style="
        text-align: center;
        padding: 40px;
        color: #6b7280;
        font-size: 14px;
    ">

        Nenhum ticket encontrado com:

        <strong>
            Status = Aguardando
        </strong>

        e justificativa:

        <strong>
            Sprint
        </strong>

        ou

        <strong>
            Equipe de Desenvolvimento
        </strong>

    </div>

    """

else:

    for analista, t_list in tickets_por_analista.items():

        html_content += f"""

        <div class="analyst-section">

            <div class="analyst-header">

                👤 {esc(analista)}
                ({len(t_list)} ticket(s))

            </div>

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

        for ticket in t_list:

            t_id = ticket.get(
                "id"
            )

            urgency_raw = ticket.get(
                "urgency"
            )

            status = ticket.get(
                "status"
            ) or "-"

            justification = ticket.get(
                "justification"
            ) or "-"

            tipo_alerta = ticket.get(
                "_tipo_alerta",
                "-"
            )

            due_formatted = format_date(
                ticket.get("dueDate")
            )


            # ------------------------------------------------
            # CLIENTE / ORGANIZAÇÃO
            # ------------------------------------------------

            clients = ticket.get(
                "clients",
                []
            )

            organizacao = "-"
            solicitante = "-"

            if (
                isinstance(clients, list)
                and clients
            ):

                cliente = clients[0]

                if isinstance(cliente, dict):

                    solicitante = (
                        cliente.get(
                            "businessName"
                        )
                        or
                        cliente.get(
                            "name"
                        )
                        or "-"
                    )

                    org_obj = cliente.get(
                        "organization"
                    )

                    if isinstance(
                        org_obj,
                        dict
                    ):

                        organizacao = (
                            org_obj.get(
                                "businessName"
                            )
                            or
                            org_obj.get(
                                "name"
                            )
                            or "-"
                        )


            # ------------------------------------------------
            # BADGES
            # ------------------------------------------------

            badge_css = (
                "badge-sprint"
                if tipo_alerta == "Aguardando Sprint"
                else "badge-dev"
            )

            urg_badge = urgency_badge(
                urgency_raw
            )


            html_content += f"""

            <tr>

                <td style="
                    font-weight: bold;
                    color: #4b5563;
                ">
                    {esc(t_id)}
                </td>

                <td>

                    <span class="
                        badge-tag
                        {badge_css}
                    ">
                        {esc(tipo_alerta)}
                    </span>

                </td>

                <td>
                    {urg_badge}
                </td>

                <td>

                    <strong style="
                        color: #1f2937;
                    ">
                        {esc(organizacao)}
                    </strong>

                    <br>

                    <span style="
                        font-size: 11px;
                        color: #6b7280;
                    ">
                        {esc(solicitante)}
                    </span>

                </td>

                <td style="
                    max-width: 240px;
                    line-height: 1.4;
                ">
                    {esc(ticket.get("subject") or "-")}
                </td>

                <td>

                    <span class="
                        status
                        status-waiting
                    ">
                        {esc(status)}
                    </span>

                </td>

                <td style="
                    max-width: 200px;
                    color: #6b7280;
                    line-height: 1.4;
                ">
                    {esc(justification)}
                </td>

                <td style="
                    white-space: nowrap;
                ">
                    {esc(due_formatted)}
                </td>

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

msg["To"] = ", ".join(
    EMAIL_RECIPIENTS
)

msg["Subject"] = (
    f"Relatório - Tickets Aguardando "
    f"Sprint & Desenvolvimento "
    f"({data_atual_str})"
)

msg.attach(
    MIMEText(
        html_content,
        "html",
        "utf-8"
    )
)


# ============================================================
# ENVIO
# ============================================================

try:

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465
    ) as server:

        server.login(
            EMAIL_USER,
            EMAIL_PASSWORD
        )

        server.sendmail(
            EMAIL_USER,
            EMAIL_RECIPIENTS,
            msg.as_string()
        )

    print()
    print("============================================================")
    print("E-MAIL ENVIADO COM SUCESSO")
    print("============================================================")

    print(
        f"Destinatários: {', '.join(EMAIL_RECIPIENTS)}"
    )

    print(
        f"Tickets enviados: {len(tickets_filtrados)}"
    )

except Exception as e:

    print()
    print("============================================================")
    print("ERRO AO ENVIAR E-MAIL")
    print("============================================================")

    print(e)

    raise
