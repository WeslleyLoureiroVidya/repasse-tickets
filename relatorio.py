import os
import requests
from collections import defaultdict

MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")
url_tickets = "https://api.movidesk.com/public/v1/tickets"

params_tickets = {
    "token": MOVIDESK_TOKEN,
    "$select": "id,status,justification",
    "$top": "100",
    "$orderby": "id desc"
}

print("--- INICIANDO BUSCA DETALHADA ---")
try:
    response = requests.get(url_tickets, params=params_tickets, timeout=30)
    batch = response.json()
    
    for t in batch:
        st = (t.get("status") or "").lower()
        just = (t.get("justification") or "").lower()
        
        # Lógica de decisão
        match_sprint = "sprint" in just or "sprint" in st
        match_dev = "aguardando" in st and ("equipe de desenvolvimento" in just or "desenvolvimento" in just)
        
        decisao = "INCLUIR" if (match_sprint or match_dev) else "DESCARTAR"
        
        print(f"DEBUG: ID {t.get('id')} | Decisão: {decisao} | Status: '{st}' | Just: '{just}'")

except Exception as e:
    print(f"Erro na conexão: {e}")
