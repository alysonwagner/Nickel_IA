import os
import json
import requests
from datetime import datetime, timedelta

# --- MAPEAMENTO DE PASTAS ---
# Presumindo que o processor.py está dentro de backend/bot/
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from backend.core.config import settings

class CerebroOperario:
    def __init__(self):
        # ARQUITETURA BLINDADA: Usando REST API em vez de SDK para evitar conflitos de dependência
        self.gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro-latest:generateContent?key={settings.GEMINI_API_KEY}"
        self.whisper_url = "https://api.openai.com/v1/audio/transcriptions"

    def transcrever_audio(self, audio_bytes):
        """Usa a API REST do Whisper da OpenAI para transcrever o áudio com precisão."""
        try:
            headers = {
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}"
            }
            files = {
                "file": ("audio.ogg", audio_bytes, "audio/ogg")
            }
            data = {
                "model": "whisper-1", 
                "language": "pt"
            }
            
            response = requests.post(self.whisper_url, headers=headers, files=files, data=data)
            
            if response.status_code == 200:
                return response.json().get("text", "")
            else:
                # Segurança: Máscara na chave caso dê erro no log
                print("Erro na API Whisper:", response.text.replace(settings.OPENAI_API_KEY, "[CHAVE_OCULTA]"))
                return None
        except Exception as e:
            print("Erro de Conexão (Áudio):", str(e).replace(settings.OPENAI_API_KEY, "[CHAVE_OCULTA]"))
            return None

    def processar_mensagem(self, texto_usuario):
        """Passa o texto (ou áudio transcrito) pelo Gemini para extrair a intenção e os dados (Gastos ou Investimentos)."""
        data_hoje = datetime.now().strftime('%Y-%m-%d')
        
        prompt_sistema = f"""
        Você é o Cérebro Operário da NICKEL_IA, um motor de extração de dados financeiros de elite.
        Data atual do sistema: {data_hoje}. Use isso como referência matemática se o usuário disser "hoje", "ontem", "anteontem", etc.

        Sua ÚNICA função é ler o texto do usuário e retornar um JSON puro.
        NUNCA retorne texto fora do JSON. NUNCA use blocos de código markdown (como ```json). APENAS o objeto JSON limpo.

        Existem 3 intenções (intents) possíveis:
        1. "chat": Para conversa fiada, dúvidas, perguntas genéricas ou assuntos não relacionados a lançamentos financeiros.
        2. "transaction": Para receitas e despesas normais do dia a dia (mercado, salário, padaria, etc).
        3. "investment": PARA ATIVOS DE RENDA VARIÁVEL E FIXA (Comprar/Vender Ações, Fundos Imobiliários, Tesouro Direto, Criptomoedas).

        --- ESTRUTURAS ESPERADAS (Siga rigorosamente) ---

        SE FOR CHAT:
        {{
            "intent": "chat"
        }}

        SE FOR TRANSACTION:
        {{
            "intent": "transaction",
            "transactions": [
                {{
                    "description": "Nome curto e direto do gasto",
                    "amount": 150.50,
                    "type": "EXPENSE", // ou "INCOME"
                    "category": "MERCADO" // Use categorias óbvias: ALIMENTACAO, VEICULO, SAUDE, EDUCACAO, HABITACAO...
                }}
            ]
        }}

        SE FOR INVESTMENT (Atenção redobrada à precisão matemática):
        {{
            "intent": "investment",
            "investments": [
                {{
                    "asset_code": "TICKER (Ex: BTC, MXRF11, PETR4, ETH)",
                    "asset_type": "CRIPTO", // Ou "FII", "ACAO", "RENDA_FIXA"
                    "operation_type": "BUY", // Ou "SELL"
                    "quantity": 0.05,
                    "unit_price": 100000.00,
                    "total_amount": 5000.00,
                    "operation_date": "YYYY-MM-DD" 
                }}
            ]
        }}

        --- REGRAS DE OURO PARA INVESTMENT ---
        - REGRA DE CÁLCULO 1: Se o usuário disser "Comprei 500 reais de BTC a 300 mil reais a unidade":
          total_amount = 500.00
          unit_price = 300000.00
          quantity = 500 / 300000 = 0.001666 (calcule com precisão de até 6 casas decimais).
        - REGRA DE CÁLCULO 2: Se o usuário disser "Comprei 10 cotas de MXRF11 a 10 reais":
          quantity = 10.0
          unit_price = 10.00
          total_amount = 100.00 (quantity * unit_price).
        - DATA ("operation_date"): Se o usuário não falar a data, use o dia de hoje ({data_hoje}). Se disser "ontem", deduza o dia corretamente no formato YYYY-MM-DD.
        
        Texto do usuário a ser processado: "{texto_usuario}"
        """

        payload = {
            "contents": [{"parts": [{"text": prompt_sistema}]}],
            "generationConfig": {"temperature": 0.0} # Temperatura ZERO para extração matemática fria e precisa
        }

        try:
            response = requests.post(self.gemini_url, json=payload)
            if response.status_code == 200:
                resposta_texto = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                # Limpeza forçada caso a IA ainda insista no markdown
                resposta_limpa = resposta_texto.replace("```json", "").replace("```", "").strip()
                return json.loads(resposta_limpa)
            else:
                # Segurança: Máscara na chave caso dê erro no log
                print("Erro na API Gemini:", response.text.replace(settings.GEMINI_API_KEY, "[CHAVE_OCULTA]"))
                return None
        except Exception as e:
            print("Erro de Conexão (Gemini):", str(e).replace(settings.GEMINI_API_KEY, "[CHAVE_OCULTA]"))
            return None