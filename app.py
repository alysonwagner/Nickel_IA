import os
import json
import requests
import threading
from datetime import datetime
from flask import Flask, request, jsonify

# ==========================================
# CONFIGURAÇÕES E CHAVES DE SEGURANÇA
# ==========================================
TELEGRAM_BOT_TOKEN = "8590071405:AAEBU105saCPFppYEWfnfiHCdEiPS5WqsSE"
SUPABASE_URL = "https://wghaaypsbhxxmneyccsw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndnaGFheXBzYmh4eG1uZXljY3N3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM1MDE4NTQsImV4cCI6MjA4OTA3Nzg1NH0.GjO23UOuliZjeNtKjvUJjgUru0_dVxeyGIRLH7dHkgI"
GEMINI_API_KEY = "AIzaSyCwKl2Gw8DmrbSQfwiM9c5VUiP9SXPRxlw"

app = Flask(__name__)

# ==========================================
# MEMÓRIA TEMPORÁRIA DO SERVIDOR
# ==========================================
transacoes_pendentes = {}

# ==========================================
# NOVAS FUNÇÕES: MÉTRICAS E LIMITES (SUPERADMIN)
# ==========================================

def registrar_log_uso(email, formato="TEXTO"):
    """Alimenta o painel Superadmin com as métricas de engajamento diário."""
    url = f"{SUPABASE_URL}/rest/v1/api_usage_logs"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    payload = {
        "user_email": email,
        "message_type": formato # 'TEXTO' ou 'AUDIO'
    }
    try:
        requests.post(url, headers=headers, json=payload, timeout=5)
        print(f"[RAIO-X] 📊 Log de engajamento ({formato}) registrado para o Superadmin.")
    except Exception as e:
        print("[RAIO-X ERRO] Falha ao registrar log de uso.")

def verificar_limite_diario(email, plano, formato, configs):
    """Bloqueia o usuário se ele ultrapassar o limite que você definiu no painel."""
    chave_limite = f"limit_{plano.lower()}_{'audio' if formato == 'AUDIO' else 'text'}"
    limite_diario = int(configs.get(chave_limite, 15 if plano == 'FREE' else 100))
    
    hoje = datetime.now().strftime('%Y-%m-%d')
    url = f"{SUPABASE_URL}/rest/v1/api_usage_logs?user_email=eq.{email}&message_type=eq.{formato}&created_at=gte.{hoje}T00:00:00"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        usos_hoje = len(res.json())
        print(f"[RAIO-X] 🚦 Uso de hoje ({plano}): {usos_hoje}/{limite_diario}")
        return usos_hoje < limite_diario
    except Exception as e:
        return True # Se o banco falhar, liberamos para não travar a UX do cliente

# ==========================================
# FUNÇÕES DE COMUNICAÇÃO E BANCO DE DADOS
# ==========================================

def enviar_mensagem_telegram(chat_id, texto, teclado_inline=None):
    print(f"[RAIO-X] 📡 Enviando para o Telegram (Chat ID: {chat_id})...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}
    if teclado_inline:
        payload["reply_markup"] = teclado_inline
        
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"[RAIO-X ERRO TELEGRAM] ❌ O Telegram recusou: {res.text}")
            return None
        else:
            print("[RAIO-X] ✅ Telegram entregou a mensagem com sucesso!")
            return res.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"[RAIO-X ERRO] Falha de conexão com Telegram: {str(e).replace(TELEGRAM_BOT_TOKEN, '[CHAVE_OCULTA]')}")
        return None

def editar_mensagem_telegram(chat_id, message_id, novo_texto, novo_teclado=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": novo_texto, "parse_mode": "HTML"}
    if novo_teclado:
        payload["reply_markup"] = novo_teclado
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"[RAIO-X ERRO TELEGRAM] ❌ Falha ao editar mensagem: {res.text}")
    except Exception as e:
        print(f"[RAIO-X ERRO] Falha ao editar Telegram: {str(e).replace(TELEGRAM_BOT_TOKEN, '[CHAVE_OCULTA]')}")

def buscar_configuracoes_sistema():
    url = f"{SUPABASE_URL}/rest/v1/system_config?select=config_key,config_value"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    configs = {}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            for item in res.json():
                configs[item['config_key']] = item['config_value']
    except Exception as e:
        print("[RAIO-X] Aviso: Falha ao carregar system_config. Usando fallbacks.")
    return configs

def buscar_cliente_supabase(chat_id):
    url = f"{SUPABASE_URL}/rest/v1/clientes?id_telegram=eq.{chat_id}&select=*"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        dados = res.json()
        if len(dados) > 0: return dados[0]
    except Exception as e:
        print(f"[RAIO-X ERRO] Supabase (Buscar Cliente): {str(e)[:50]}...")
    return None

def vincular_telegram_supabase(email_usuario, chat_id):
    url = f"{SUPABASE_URL}/rest/v1/clientes?email=eq.{email_usuario}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
    try:
        res = requests.patch(url, headers=headers, json={"id_telegram": str(chat_id)}, timeout=10)
        dados = res.json()
        if len(dados) > 0: return dados[0].get("nome", "Investidor(a)")
    except Exception as e:
        print(f"[RAIO-X ERRO] Supabase (Vincular): {str(e)[:50]}...")
    return None

def salvar_transacao_supabase(email, transacao):
    url = f"{SUPABASE_URL}/rest/v1/transactions"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    payload = {
        "user_email": email,
        "description": transacao.get("description"),
        "amount": float(transacao.get("amount")),
        "category": transacao.get("category", "OUTROS"),
        "type": transacao.get("type", "EXPENSE"),
        "date": datetime.now().strftime('%Y-%m-%d')
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 201 or res.status_code == 200:
            print(f"[RAIO-X] ✅ Transação salva com sucesso no BD principal!")
        else:
            print(f"[RAIO-X ERRO] O Supabase recusou a transação: {res.text}")
    except Exception as e:
        print(f"[RAIO-X ERRO] Falha ao salvar transação: {str(e)[:50]}...")

# ==========================================
# NOVOS MÓDULOS DE CARTÃO DE CRÉDITO E TETOS
# ==========================================

def buscar_cartoes_supabase(email):
    """Busca os cartões cadastrados do usuário para gerar os botões no Telegram."""
    url = f"{SUPABASE_URL}/rest/v1/credit_cards?user_email=eq.{email}&select=id,nome_cartao"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        return res.json() if res.status_code == 200 else []
    except Exception as e:
        print(f"[RAIO-X ERRO] Supabase (Buscar Cartões): {str(e)[:50]}")
        return []

def salvar_transacao_credito_supabase(email, card_id, transacao, parcelas):
    """Calcula a matemática das parcelas e salva a fatura no banco de dados."""
    url = f"{SUPABASE_URL}/rest/v1/credit_transactions"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    
    hoje = datetime.now()
    valor_total = float(transacao.get("amount"))
    num_parcelas = parcelas if parcelas > 0 else 1
    valor_parcela = valor_total / num_parcelas

    payloads = []
    for i in range(1, num_parcelas + 1):
        mes = hoje.month + i - 1
        ano = hoje.year + (mes - 1) // 12
        mes_real = (mes - 1) % 12 + 1
        mes_fatura = f"{ano}-{mes_real:02d}"

        payloads.append({
            "user_email": email,
            "card_id": card_id,
            "descricao": transacao.get("description"),
            "categoria": transacao.get("category", "OUTROS"),
            "valor_parcela": round(valor_parcela, 2),
            "parcela_atual": i,
            "total_parcelas": num_parcelas,
            "mes_fatura": mes_fatura
        })

    try:
        requests.post(url, headers=headers, json=payloads, timeout=10)
        print(f"[RAIO-X] ✅ {num_parcelas} Parcelas de crédito salvas com sucesso na fatura!")
    except Exception as e:
        print(f"[RAIO-X ERRO] Falha ao salvar crédito: {str(e)[:50]}...")

def buscar_categorias_teto_supabase(email):
    """Busca as categorias que o usuário configurou um teto de gastos para forçar a IA a usá-las."""
    url = f"{SUPABASE_URL}/rest/v1/spending_limits?user_email=eq.{email}&select=category"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            dados = res.json()
            return list(set([item['category'] for item in dados]))
        return []
    except Exception as e:
        print(f"[RAIO-X ERRO] Supabase (Buscar Tetos): {str(e)[:50]}")
        return []

# ==========================================
# CÉREBRO DA IA (GEMINI) COM MOTOR SEMÂNTICO
# ==========================================

def processar_texto_com_gemini(texto_usuario, nome_cliente, plano_cliente, configs, categorias_customizadas=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    if plano_cliente == "PREMIUM":
        persona_padrao = f"Você é o consultor financeiro executivo (CFO) da NICKEL_IA. Fale com {nome_cliente} com extrema polidez, foco em rentabilidade e análises profundas e estratégicas."
        chave_persona = "oracle_persona_premium"
    else:
        persona_padrao = f"Você é o assistente financeiro ágil da NICKEL_IA. Fale com {nome_cliente} de forma curta, animada, usando emojis e dando dicas rápidas de economia do dia a dia."
        chave_persona = "oracle_persona_free"
        
    persona = configs.get(chave_persona, persona_padrao)
    
    # MOTOR SEMÂNTICO: Adicionado PET na lista base
    categorias_base = "ALIMENTACAO, SAUDE, EDUCACAO, HABITACAO, COMUNICACAO, VEICULO, LAZER, OUTROS, REFEICAO, SUPERMERCADO, FARMACIA, PET"
    instrucao_categorias = f"Categorias válidas padrão: {categorias_base}."
    
    if categorias_customizadas:
        lista_custom = ", ".join(categorias_customizadas)
        instrucao_categorias += f"\n    ATENÇÃO MÁXIMA: O usuário possui tetos de gastos ativos nestas categorias: [{lista_custom}]. Você DEVE OBRIGATORIAMENTE priorizar o uso destas categorias exatas se o gasto descrito tiver qualquer relação semântica com elas."
    
    prompt_completo = f"""
    {persona}
    
    Sua função é classificar a mensagem do usuário.
    
    REGRAS DE CLASSIFICAÇÃO:
    1. Se for saudação, dúvida ou bate-papo: "intencao" = "CONVERSA". Preencha "resposta_bot" de forma amigável seguindo sua persona.
    2. Se for gasto, compra ou receita: "intencao" = "LANCAMENTO". Extraia os dados numéricos e deixe "resposta_bot" vazio. Identifique se o usuário mencionou parcelas (ex: em 3x = 3). Se não mencionou, parcelas = 1.
    
    SAÍDA OBRIGATÓRIA (Formato JSON Estrito):
    {{
      "intencao": "CONVERSA" ou "LANCAMENTO",
      "resposta_bot": "Sua resposta aqui...",
      "parcelas": 1,
      "transacoes": [
        {{"description": "nome curto", "amount": 15.50, "category": "OUTROS", "type": "EXPENSE"}}
      ]
    }}
    
    {instrucao_categorias}
    Tipos válidos: EXPENSE (saída), INCOME (entrada).
    
    MENSAGEM DO USUÁRIO: "{texto_usuario}"
    """

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_completo}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    print(f"[RAIO-X] 🧠 Chamando Gemini API (Persona: {plano_cliente})...")
    try:
        res = requests.post(url, json=payload, timeout=20)
        j = res.json()
        if 'error' in j:
            print(f"[RAIO-X ERRO] Erro API Google: {j['error']['message']}")
            return None
            
        texto = j['candidates'][0]['content']['parts'][0]['text']
        texto = texto.replace("```json\n", "").replace("```json", "").replace("\n```", "").replace("```", "").strip()
        print(f"[RAIO-X] ✅ Resposta da IA Extraída com a persona correta!")
        return json.loads(texto)
    except Exception as e:
        print(f"[RAIO-X ERRO] Falha na IA Gemini: {str(e)[:50]}...")
        return None

# ==========================================
# TRABALHADORES EM SEGUNDO PLANO (THREADS)
# ==========================================

def tratar_novo_cadastro(chat_id, texto, configs):
    partes = texto.split(" ")
    if len(partes) > 1:
        email_do_link = partes[1].strip()
        enviar_mensagem_telegram(chat_id, "⚙️ <i>Sincronizando...</i>")
        nome = vincular_telegram_supabase(email_do_link, chat_id)
        if nome:
            boas_vindas = configs.get("bot_free_start", "Sincronizado! Bem-vindo, {nome}.").replace("{nome}", nome)
            tec = {"inline_keyboard": [[{"text": "🖥️ Acessar Dashboard", "url": "https://google.com"}]]}
            enviar_mensagem_telegram(chat_id, boas_vindas, tec)
            registrar_log_uso(email_do_link, "TEXTO")
        else:
            enviar_mensagem_telegram(chat_id, "❌ E-mail não encontrado no sistema.")
    else:
        enviar_mensagem_telegram(chat_id, "🤖 Para sincronizar, acesse o painel Web da NICKEL_IA e clique no botão flutuante.")

def tratar_mensagem_texto(chat_id, texto, formato="TEXTO"):
    configs = buscar_configuracoes_sistema()

    if texto.startswith("/start"):
        tratar_novo_cadastro(chat_id, texto, configs)
        return

    cliente = buscar_cliente_supabase(chat_id)
    if not cliente:
        enviar_mensagem_telegram(chat_id, "🤖 Vi que seu Telegram não está conectado. Vá ao seu painel Web e clique no botão de Sincronizar.")
        return
        
    nome_cliente = cliente.get("nome", "Investidor(a)")
    plano_cliente = cliente.get("plan", "FREE")
    email_cliente = cliente.get("email")

    if not verificar_limite_diario(email_cliente, plano_cliente, formato, configs):
        gatilho_upsell = configs.get("bot_free_upsell", "Limite diário atingido! Faça o upgrade para o Premium para continuar usando a Inteligência Artificial.")
        enviar_mensagem_telegram(chat_id, gatilho_upsell)
        return

    registrar_log_uso(email_cliente, formato)

    msg_analisando_id = enviar_mensagem_telegram(chat_id, "⏳ <i>Analisando...</i>")

    # INJEÇÃO DE INTELIGÊNCIA: Busca os tetos do usuário antes de chamar a IA
    categorias_teto = buscar_categorias_teto_supabase(email_cliente)

    # Passa as categorias customizadas para o motor do Gemini
    ia = processar_texto_com_gemini(texto, nome_cliente, plano_cliente, configs, categorias_teto)
    
    if not ia:
        msg_erro = "❌ Meu cérebro deu um curto-circuito. Pode reformular?"
        if msg_analisando_id:
            editar_mensagem_telegram(chat_id, msg_analisando_id, msg_erro)
        else:
            enviar_mensagem_telegram(chat_id, msg_erro)
        return

    if ia.get("intencao") == "CONVERSA":
        teclado_painel = {"inline_keyboard": [[{"text": "🖥️ Acessar Meu Painel", "url": "https://google.com"}]]}
        if msg_analisando_id:
            editar_mensagem_telegram(chat_id, msg_analisando_id, f"🤖 {ia.get('resposta_bot')}", teclado_painel)
        else:
            enviar_mensagem_telegram(chat_id, f"🤖 {ia.get('resposta_bot')}", teclado_painel)
        return

    transacoes = ia.get("transacoes", [])
    
    if not transacoes:
        msg_erro = "❌ Entendi que falou de dinheiro, mas não achei valores exatos."
        if msg_analisando_id: editar_mensagem_telegram(chat_id, msg_analisando_id, msg_erro)
        return

    if len(transacoes) > 1 and plano_cliente == "FREE":
        upsell = configs.get("bot_free_upsell", "Limite atingido! Múltiplos lançamentos é apenas no Premium.")
        if msg_analisando_id: editar_mensagem_telegram(chat_id, msg_analisando_id, upsell)
        return

    parcelas_identificadas = ia.get("parcelas", 1)

    transacoes_pendentes[str(chat_id)] = {
        "email": email_cliente,
        "plano": plano_cliente,
        "nome": nome_cliente,
        "dados": transacoes,
        "parcelas": parcelas_identificadas
    }

    texto_confirmacao = "🧾 <b>Identifiquei o seguinte lançamento:</b>\n\n"
    for t in transacoes:
        tipo_str = "Saída" if t.get("type") == "EXPENSE" else "Entrada"
        texto_confirmacao += f"• <b>{t.get('description')}</b>\n"
        texto_confirmacao += f"💰 Valor Total: R$ {float(t.get('amount')):.2f}\n"
        if parcelas_identificadas > 1:
            texto_confirmacao += f"⏱ Parcelamento: {parcelas_identificadas}x\n"
        texto_confirmacao += f"🏷 Categoria: {t.get('category')}\n"
        texto_confirmacao += f"🔄 Tipo: {tipo_str}\n\n"
    
    texto_confirmacao += "💳 <b>Como este pagamento foi feito?</b>"

    cartoes = buscar_cartoes_supabase(email_cliente)
    teclado_botoes = []
    
    teclado_botoes.append([{"text": "💵 Débito / Pix (Saldo Atual)", "callback_data": "salvar_debito"}])
    
    for c in cartoes:
        teclado_botoes.append([{"text": f"💳 Cartão: {c['nome_cartao']}", "callback_data": f"salvar_credito_{c['id']}"}])

    teclado_botoes.append([{"text": "❌ Cancelar", "callback_data": "btn_cancelar"}])
    teclado_botoes.append([{"text": "🖥️ Ir para o Dashboard", "url": "https://google.com"}])

    teclado_confirmacao = {"inline_keyboard": teclado_botoes}

    if msg_analisando_id:
        editar_mensagem_telegram(chat_id, msg_analisando_id, texto_confirmacao, teclado_confirmacao)
    else:
        enviar_mensagem_telegram(chat_id, texto_confirmacao, teclado_confirmacao)


def tratar_clique_botao(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    dados_clique = callback_query["data"]
    chat_str = str(chat_id)
    
    pendente = transacoes_pendentes.get(chat_str)

    if dados_clique == "btn_cancelar":
        if chat_str in transacoes_pendentes: del transacoes_pendentes[chat_str]
        editar_mensagem_telegram(chat_id, message_id, "❌ Lançamento cancelado pelo usuário.")
        return

    if dados_clique.startswith("salvar_"):
        if not pendente:
            editar_mensagem_telegram(chat_id, message_id, "⚠️ Tempo expirado ou transação já processada.")
            return
            
        configs = buscar_configuracoes_sistema()
        email = pendente["email"]
        plano = pendente["plano"]
        nome = pendente["nome"]
        transacoes = pendente["dados"]
        parcelas = pendente.get("parcelas", 1)

        valor_total = sum(float(t['amount']) for t in transacoes)
        categorias = ", ".join(list(set(t['category'] for t in transacoes)))
        tipo_str = "Saída" if transacoes[0]['type'] == 'EXPENSE' else "Entrada"

        if dados_clique == "salvar_debito":
            for t in transacoes:
                salvar_transacao_supabase(email, t)
            forma_pagamento = "Débito/Pix"
            
        elif dados_clique.startswith("salvar_credito_"):
            card_id = dados_clique.replace("salvar_credito_", "")
            for t in transacoes:
                # 1. Salva a fatura real dividida em parcelas
                salvar_transacao_credito_supabase(email, card_id, t, parcelas)
                
                # 2. INJEÇÃO CORREÇÃO DE ERRO: Salva na tabela principal também.
                # Mantém o tipo "EXPENSE" para não dar erro no banco, mas injeta a tag [CRÉDITO] na descrição
                # Isso faz a compra aparecer no painel sem quebrar o sistema.
                t_copia = t.copy()
                t_copia['description'] = f"[CRÉDITO] {t['description']}"
                salvar_transacao_supabase(email, t_copia)

            forma_pagamento = f"Crédito ({parcelas}x)"

        chave_msg = "bot_premium_confirm" if plano == "PREMIUM" else "bot_free_confirm"
        msg_sucesso = configs.get(chave_msg, "✅ Lançamento salvo com sucesso, {nome}! Valor: R$ {valor} via {forma}.")
        
        msg_sucesso = msg_sucesso.replace("{nome}", nome)
        msg_sucesso = msg_sucesso.replace("{valor}", f"{valor_total:.2f}")
        msg_sucesso = msg_sucesso.replace("{categoria}", categorias)
        msg_sucesso = msg_sucesso.replace("{tipo}", tipo_str)
        msg_sucesso = msg_sucesso.replace("{forma}", forma_pagamento)
        
        del transacoes_pendentes[chat_str]

        teclado_dash = {"inline_keyboard": [[{"text": "🖥️ Acessar Dashboard", "url": "https://google.com"}]]}
        editar_mensagem_telegram(chat_id, message_id, msg_sucesso, teclado_dash)

# ==========================================
# ROTA PRINCIPAL DO WEBHOOK
# ==========================================
@app.route('/webhook', methods=['POST'])
def webhook():
    dados = request.json
    
    if "callback_query" in dados:
        print("[RAIO-X] 🖱️ Botão clicado!")
        threading.Thread(target=tratar_clique_botao, args=(dados["callback_query"],)).start()
        return jsonify({"status": "ok"}), 200

    if "message" in dados:
        chat_id = dados["message"]["chat"]["id"]
        
        if "text" in dados["message"]:
            texto = dados["message"]["text"]
            print(f"\n[RAIO-X] 📥 Nova mensagem: '{texto}'")
            threading.Thread(target=tratar_mensagem_texto, args=(chat_id, texto, "TEXTO")).start()
            
        elif "voice" in dados["message"] or "audio" in dados["message"]:
            print(f"\n[RAIO-X] 🎙️ Novo Áudio recebido.")
            enviar_mensagem_telegram(chat_id, "🎤 <i>O Módulo de transcrição de Áudio será ativado na próxima fase!</i>")
        
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    print("[RAIO-X] 🟢 Bot Online com Integração Total (Métricas & Limites)!")
    app.run(port=5000, debug=True)