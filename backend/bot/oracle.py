import os
import sys
import json
import requests
from datetime import datetime

# --- MAPEAMENTO DE PASTAS ---
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from backend.core.config import settings

# =====================================================================
# 1. FUNÇÕES AUXILIARES DE BANCO (LEITURA/GRAVAÇÃO ISOLADA)
# =====================================================================
def get_headers():
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

def buscar_contexto_usuario(email, categoria_atual):
    """Busca o histórico e calcula os gastos específicos da categoria."""
    contexto = {
        "regras": [],
        "sonho": None,
        "gastos_mes": 0.0,
        "gastos_categoria_atual": 0.0
    }
    try:
        url_regras = f"{settings.SUPABASE_URL}/rest/v1/spending_limits?select=*&user_email=eq.{email.lower()}"
        res_regras = requests.get(url_regras, headers=get_headers())
        if res_regras.status_code == 200:
            contexto["regras"] = res_regras.json()

        url_sonho = f"{settings.SUPABASE_URL}/rest/v1/user_dreams?select=*&user_email=eq.{email.lower()}&limit=1"
        res_sonho = requests.get(url_sonho, headers=get_headers())
        if res_sonho.status_code == 200 and len(res_sonho.json()) > 0:
            contexto["sonho"] = res_sonho.json()[0]

        mes_atual = datetime.now().strftime('%Y-%m')
        url_trans = f"{settings.SUPABASE_URL}/rest/v1/transactions?select=amount,category&user_email=eq.{email.lower()}&type=eq.EXPENSE&date=like.{mes_atual}*"
        res_trans = requests.get(url_trans, headers=get_headers())
        
        if res_trans.status_code == 200:
            transacoes = res_trans.json()
            contexto["gastos_mes"] = sum(float(t['amount']) for t in transacoes)
            contexto["gastos_categoria_atual"] = sum(float(t['amount']) for t in transacoes if t.get('category', '').upper() == categoria_atual.upper())

    except Exception as e:
        erro_mascarado = str(e).replace(settings.SUPABASE_KEY, "[CHAVE_OCULTA]")
        print(f"Erro ao buscar contexto: {erro_mascarado}")
    
    return contexto

def buscar_memoria_ia():
    """Conecta no CMS do Super Admin e puxa as 3 caixas de instrução do Oráculo."""
    memoria = {
        "persona": "Você é o Oráculo, um assessor financeiro direto e analítico.",
        "rules": "Analise o impacto do gasto no orçamento do cliente.",
        "format": "Retorne um JSON com 'verdict_text' (análise curta) e 'dream_bussola' (impacto no sonho)."
    }
    try:
        url = f"{settings.SUPABASE_URL}/rest/v1/system_config?select=config_key,config_value&config_key=in.(oracle_persona,oracle_rules,oracle_format)"
        res = requests.get(url, headers=get_headers())
        if res.status_code == 200:
            for item in res.json():
                if item['config_key'] == 'oracle_persona': memoria['persona'] = item['config_value']
                elif item['config_key'] == 'oracle_rules': memoria['rules'] = item['config_value']
                elif item['config_key'] == 'oracle_format': memoria['format'] = item['config_value']
    except Exception as e:
        erro_mascarado = str(e).replace(settings.SUPABASE_KEY, "[CHAVE_OCULTA]")
        print(f"Erro ao buscar Memória Modular: {erro_mascarado}")
    
    return memoria

def salvar_insight_banco(email, veredito, bussola):
    try:
        url = f"{settings.SUPABASE_URL}/rest/v1/ai_insights"
        payload = {
            "user_email": email.lower(),
            "verdict_text": veredito,
            "dream_bussola": bussola
        }
        res = requests.post(url, headers=get_headers(), json=payload)
        return res.status_code in [200, 201]
    except Exception as e:
        erro_mascarado = str(e).replace(settings.SUPABASE_KEY, "[CHAVE_OCULTA]")
        print(f"Erro ao salvar insight: {erro_mascarado}")
        return False

# =====================================================================
# 2. MOTOR DO ASSISTENTE (COM MEMÓRIA MODULAR)
# =====================================================================
def gerar_insight_oraculo(dados_transacao, is_dream=False):
    print("👔 Cérebro Analítico puxando memórias do painel Super Admin...")
    
    user_email = dados_transacao.get('user_email')
    if not user_email:
        return "Erro: Identidade do usuário não encontrada."

    valor_transacao = float(dados_transacao.get('amount', 0))
    categoria = dados_transacao.get('category', 'GERAL')
    tipo = dados_transacao.get('type', 'EXPENSE')

    # 1. Puxa os dados financeiros
    contexto = buscar_contexto_usuario(user_email, categoria)
    
    # 2. Puxa as regras de personalidade do seu painel
    memoria_ia = buscar_memoria_ia()
    
    limite_categoria = None
    if contexto['regras']:
        for regra in contexto['regras']:
            if regra.get('category', '').upper() == categoria.upper():
                limite_categoria = float(regra.get('amount_limit', 0))
                break

    info_limite = f"A categoria '{categoria}' não tem teto de gastos definido."
    if limite_categoria and limite_categoria > 0:
        gasto_cat = contexto['gastos_categoria_atual']
        pct_gasto = (gasto_cat / limite_categoria) * 100
        info_limite = f"Teto para {categoria}: R$ {limite_categoria:.2f}. Já gastou R$ {gasto_cat:.2f} ({pct_gasto:.1f}% do limite)."
    
    info_sonho = "Nenhum sonho definido no cofre."
    if contexto['sonho']:
        s = contexto['sonho']
        info_sonho = f"Sonho: {s['dream_name']} (Faltam R$ {float(s['goal_amount']) - float(s['saved_amount']):.2f})"

    # --- O PROMPT MONTADO COM AS PEÇAS DO SEU PAINEL ---
    prompt = f"""
    [IDENTIDADE DO SISTEMA]
    {memoria_ia['persona']}

    DADOS DA OPERAÇÃO RECENTE:
    - Movimentação: {'Reserva no Cofre' if is_dream else ('Despesa' if tipo == 'EXPENSE' else 'Receita')} de R$ {valor_transacao:.2f} em {categoria}.
    - Status Atual da Categoria ({categoria}): {info_limite}
    - Objetivo de Vida (Cofre): {info_sonho}

    [REGRAS DE ANÁLISE E NEGÓCIO]
    {memoria_ia['rules']}

    [FORMATO DE SAÍDA EXIGIDO]
    {memoria_ia['format']}
    """

    url_gemini = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4, 
            "responseMimeType": "application/json"
        }
    }

    try:
        res = requests.post(url_gemini, headers=headers, json=payload)
        
        if res.status_code == 200:
            dados = res.json()
            texto_resposta = dados['candidates'][0]['content']['parts'][0]['text']
            analise = json.loads(texto_resposta)
            
            veredito = analise.get('verdict_text', 'Lançamento registrado com sucesso.')
            bussola = analise.get('dream_bussola', 'Acompanhando o ritmo rumo ao objetivo.')

            salvar_insight_banco(user_email, veredito, bussola)
            return veredito
        else:
            print("Erro na API Gemini: Status", res.status_code)
            return "Painel atualizado."

    except Exception as e:
        erro_mascarado = str(e).replace(settings.GEMINI_API_KEY, "[CHAVE_OCULTA]")
        print(f"Erro de processamento via REST: {erro_mascarado}")
        return "Painel atualizado."