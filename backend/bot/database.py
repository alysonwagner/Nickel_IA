import requests
import sys
import os

# --- MAPEAMENTO DE PASTAS ---
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from backend.core.config import settings

class GerenciadorBanco:
    """
    Classe responsável pela ponte entre o Bot e o Supabase via API REST.
    Explicação: Utilizamos 'requests' para enviar dados via JSON (formato de dados leve)
    diretamente para a tabela 'transactions'.
    """

    def __init__(self):
        # O cabeçalho (headers) é o nosso 'cartão de acesso' para o Supabase
        self.url = f"{settings.SUPABASE_URL}/rest/v1/transactions"
        self.headers = {
            "apikey": settings.SUPABASE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

    def salvar_registro(self, dados_dict):
        """
        Pega o dicionário preparado pelo main.py e envia para a nuvem.
        """
        try:
            # Blindagem de segurança: Remove chaves sensíveis dos logs se houver erro
            log_dados = {k: (v if 'KEY' not in k else '[OCULTO]') for k, v in dados_dict.items()}
            
            # Realiza a requisição POST (envio de dados) para o Supabase
            response = requests.post(self.url, headers=self.headers, json=dados_dict)

            if response.status_code in [200, 201]:
                print(f"✅ [BANCO] Registro salvo com sucesso: {dados_dict.get('description')}")
                return True
            else:
                print(f"❌ [BANCO] Erro ao salvar: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            # Máscara de segurança para evitar vazamento de URL/Chaves em caso de erro crítico
            erro_limpo = str(e).replace(settings.SUPABASE_URL, "[URL_OCULTA]")
            print(f"⚠️ [BANCO] Erro de conexão: {erro_limpo}")
            return False