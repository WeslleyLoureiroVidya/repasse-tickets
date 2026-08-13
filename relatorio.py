import os
import requests

MOVIDESK_TOKEN = os.environ.get("MOVIDESK_TOKEN")

url_tickets = "https://api.movidesk.com/public/v1/tickets"
params = {
    "token": MOVIDESK_TOKEN,
    "$select": "id,status,justification",
    "$top": "50", # Pega os 50 mais recentes
    "$orderby": "id desc"
}

print("--- INICIANDO DIAGNÓSTICO ---")
try:
    response = requests.get(url_tickets, params=params, timeout=30)
    data = response.json()
    
    for t in data:
        # Imprime o ID e o Status exato que a API retorna
        print(f"DEBUG: ID {t.get('id')} | Status: '{t.get('status')}' | Justificativa: '{t.get('justification')}'")
        
except Exception as e:
    print(f"Erro na conexão: {e}")
