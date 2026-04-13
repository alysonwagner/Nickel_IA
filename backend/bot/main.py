import os
import sys
import json
import requests
from datetime import datetime
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- MAPEAMENTO DE PASTAS ---
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from backend.core.config import settings
from backend.bot.processor import CerebroOperario
from backend.bot.oracle import gerar_insight_oraculo

# =====================================================================
# 1. INICIALIZAÇÃO DOS MOTORES E MEMÓRIA TEMPORÁRIA
# =====================================================================
bot = telebot.TeleBot(settings.TELEGRAM_TOKEN)
cerebro = CerebroOperario()

# Dicionário em RAM para guardar os lotes e investimentos aguardando clique no "Confirmar"
estado_usuarios = {} 

# =====================================================================
# 2. FUNÇÕES DE BANCO DE DADOS E LIMITES (REST API)
# =====================================================================
def get_headers():
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def obter_dados_usuario(telegram_id):
    """Busca se o cara é Premium ou Free e pega o Nome."""
    url = f"{settings.SUPABASE_URL}/rest/v1/clientes?select=plan,email,nome&id_telegram=eq.{telegram_id}"
    try:
        res = requests.get(url, headers=get_headers())
        if res.status_code == 200 and len(res.json()) > 0:
            return res.json()[0]
    except Exception as e:
        print("Erro Banco:", str(e).replace(settings.SUPABASE_KEY, "[CHAVE_OCULTA]"))
    return None

def registrar_metrica_uso(email, formato_msg, is_premium):
    """Alimenta o Dashboard de Métricas do Super Admin (Custo de API)."""
    url = f"{settings.SUPABASE_URL}/rest/v1/api_usage_logs"
    payload = {
        "user_email": email,
        "message_type": formato_msg, # 'AUDIO' ou 'TEXT'
        "is_premium": is_premium
    }
    try:
        requests.post(url, headers=get_headers(), json=payload)
    except Exception as e:
        print("Erro BI:", str(e).replace(settings.SUPABASE_KEY, "[CHAVE_OCULTA]"))

def puxar_textos_cms():
    """Baixa as frases personalizadas e os limites criados pelo dono no Super Admin."""
    chaves = "bot_free_start,bot_free_confirm,bot_free_upsell,bot_premium_start,bot_premium_confirm,limit_free_text,limit_free_audio,limit_premium_text,limit_premium_audio"
    url = f"{settings.SUPABASE_URL}/rest/v1/system_config?select=config_key,config_value&config_key=in.({chaves})"
    
    # Textos e Limites de emergência caso o Supabase caia ou ainda não tenha sido configurado
    config = {
        "bot_free_start": "Olá {nome}! Sua conta FREE está ativa.",
        "bot_free_confirm": "Lançamento confirmado, {nome}.",
        "bot_free_upsell": "Dica: Conheça o Oráculo Premium e acelere suas metas!",
        "bot_premium_start": "Bem-vindo {nome}, interface Premium ativa.",
        "bot_premium_confirm": "Operação registrada, {nome}.",
        "limit_free_text": "15",
        "limit_free_audio": "3",
        "limit_premium_text": "200",
        "limit_premium_audio": "50"
    }
    try:
        res = requests.get(url, headers=get_headers())
        if res.status_code == 200:
            for item in res.json():
                config[item['config_key']] = item['config_value']
    except Exception as e:
        print("Erro CMS/Limites:", str(e).replace(settings.SUPABASE_KEY, "[CHAVE_OCULTA]"))
    return config

def contar_lancamentos_dia(email):
    """Conta quantas mensagens de áudio e texto o usuário JÁ MANDOU HOJE para validar o limite."""
    data_hoje = datetime.now().strftime('%Y-%m-%d')
    # Nós usamos a tabela de logs de API (BI) para contar o gasto real, e não a tabela de transações
    url = f"{settings.SUPABASE_URL}/rest/v1/api_usage_logs?select=message_type&user_email=eq.{email}&created_at=gte.{data_hoje}T00:00:00Z"
    
    usos = {"TEXT": 0, "AUDIO": 0}
    try:
        res = requests.get(url, headers=get_headers())
        if res.status_code == 200:
            for log in res.json():
                tipo = log.get("message_type")
                if tipo in usos:
                    usos[tipo] += 1
    except Exception:
        pass
    return usos

def checar_limites_api(email, formato, is_premium, config):
    """Aplica o 'Freio de Mão'. Verifica se o usuário bateu no teto de envios."""
    usos_hoje = contar_lancamentos_dia(email)
    
    limite_txt = int(config['limit_premium_text']) if is_premium else int(config['limit_free_text'])
    limite_aud = int(config['limit_premium_audio']) if is_premium else int(config['limit_free_audio'])
    
    ja_usou = usos_hoje.get(formato, 0)
    limite = limite_txt if formato == 'TEXT' else limite_aud
    
    # Se o usuário for fazer mais uma chamada agora, ele passa do limite?
    if (ja_usou + 1) > limite:
        return False, ja_usou, limite
    
    return True, ja_usou, limite

# =====================================================================
# 3. INTERFACE DE COMUNICAÇÃO (TELEGRAM) E FREIOS DE MÃO
# =====================================================================
@bot.message_handler(commands=['start'])
def saudacao_inicial(message):
    usuario = obter_dados_usuario(message.chat.id)
    if not usuario:
        bot.reply_to(message, "⚠️ Não encontrei seu cadastro. Acesse o Painel Web e vincule seu número de Telegram.")
        return
    
    config = puxar_textos_cms()
    nome_curto = usuario.get('nome', 'Usuário').split()[0]
    
    if usuario.get('plan') == 'PREMIUM':
        resposta = config['bot_premium_start'].replace("{nome}", nome_curto)
    else:
        resposta = config['bot_free_start'].replace("{nome}", nome_curto)
        
    bot.reply_to(message, resposta)

def orquestrar_mensagem(message, conteudo, formato):
    """Função mestre: Lê, bate nos limites, converte e classifica (Gastos vs Investimentos)."""
    usuario = obter_dados_usuario(message.chat.id)
    if not usuario:
        bot.reply_to(message, "⚠️ Sua conta não está vinculada.")
        return

    email = usuario['email']
    is_premium = usuario.get('plan') == 'PREMIUM'
    nome_curto = usuario.get('nome', 'Usuário').split()[0]
    config = puxar_textos_cms()
    
    # --- FREIO DE MÃO: VALIDAÇÃO DOS LIMITES ---
    pode_usar, usos_feitos, limite_max = checar_limites_api(email, formato, is_premium, config)
    
    if not pode_usar:
        if is_premium:
            bot.reply_to(message, f"🛑 Você atingiu o teto diário de segurança da sua conta Premium ({limite_max}/{limite_max} {formato}). O limite será reiniciado à meia-noite.")
        else:
            bot.reply_to(message, f"🛑 Seu limite de {formato} diário para contas FREE esgotou ({limite_max}/{limite_max}).\n\n{config.get('bot_free_upsell', 'Assine o Premium para limites estendidos!')}")
        return
    # -------------------------------------------
    
    # 1. Alimenta as métricas e permite o fluxo
    registrar_metrica_uso(email, formato, is_premium)
    msg_aviso = bot.reply_to(message, "⏳ Processando...")
    
    # 2. Conversão de Áudio (se for o caso)
    texto_puro = conteudo
    if formato == 'AUDIO':
        texto_puro = cerebro.transcrever_audio(conteudo)
        if not texto_puro:
            bot.edit_message_text("❌ Falha na escuta do áudio.", message.chat.id, msg_aviso.message_id)
            return
            
    # 3. O Cérebro Operário divide a frase (Reconhecendo inclusive a "Data")
    analise = cerebro.processar_mensagem(texto_puro)
    if not analise:
        bot.edit_message_text("❌ Não consegui compreender a formatação financeira.", message.chat.id, msg_aviso.message_id)
        return
        
    intent = analise.get("intent")
    
    # --- ROTA A: CONVERSA FIADA ---
    if intent == "chat":
        if is_premium:
            bot.edit_message_text(f"👁️ {nome_curto}, o Oráculo aguarda suas dúvidas detalhadas no Painel Web. Por aqui, apenas envie seus gastos e investimentos.", message.chat.id, msg_aviso.message_id)
        else:
            # GATILHO DE VENDA PARA FREES EM CASO DE BATE-PAPO
            bot.edit_message_text(config['bot_free_upsell'], message.chat.id, msg_aviso.message_id)
        return
        
    # --- ROTA B: PROCESSAMENTO DE GASTOS EM LOTE ---
    if intent == "transaction":
        lote_transacoes = analise.get("transactions", [])
        if not lote_transacoes:
            bot.edit_message_text("❌ Nenhuma despesa ou receita válida encontrada.", message.chat.id, msg_aviso.message_id)
            return
            
        texto_recibo = "📋 **Lançamentos Identificados:**\n\n"
        soma_lote = 0.0
        
        for i, item in enumerate(lote_transacoes):
            icone = "🔴" if item.get('type') == 'EXPENSE' else "🟢"
            texto_recibo += f"{i+1}. {icone} {item.get('category')} - {item.get('description')} (R$ {item.get('amount'):.2f})\n"
            soma_lote += float(item.get('amount', 0))
            
        texto_recibo += f"\n**Total do Lote: R$ {soma_lote:.2f}**\nPosso lançar no painel?"
        
        estado_usuarios[message.chat.id] = {
            "tipo_fluxo": "transaction",
            "email": email,
            "is_premium": is_premium,
            "nome": nome_curto,
            "lote": lote_transacoes
        }
        
        botoes = InlineKeyboardMarkup()
        botoes.row(
            InlineKeyboardButton("✅ Confirmar Lote", callback_data="confirmar_lote"),
            InlineKeyboardButton("❌ Descartar", callback_data="cancelar_lote")
        )
        
        bot.edit_message_text(texto_recibo, message.chat.id, msg_aviso.message_id, reply_markup=botoes, parse_mode="Markdown")
        return

    # --- ROTA C: HOME BROKER (INVESTIMENTOS) ---
    if intent == "investment":
        # PAYWALL DE VOZ INTELIGENTE
        if not is_premium:
            bot.edit_message_text(f"🔒 {nome_curto}, vejo que você quer registrar um investimento na sua carteira.\n\nO Home Broker inteligente e o cálculo de Preço Médio a mercado são recursos exclusivos do Oráculo Premium.\n\n{config.get('bot_free_upsell', 'Faça o upgrade no painel web!')}", message.chat.id, msg_aviso.message_id)
            return
        
        investimentos = analise.get("investments", [])
        if not investimentos:
            bot.edit_message_text("❌ Não consegui identificar a moeda/fundo, o valor e a quantidade da operação.", message.chat.id, msg_aviso.message_id)
            return

        # Para simplificar a experiência via áudio, o Bot processa os investimentos 1 por 1
        inv = investimentos[0]
        ticker = inv.get("asset_code", "ATIVO").upper()
        preco = float(inv.get("unit_price", 0))
        qtd = float(inv.get("quantity", 0))
        total = preco * qtd
        data_operacao = inv.get("operation_date") # O cérebro pega a data falada ou o dia de hoje
        
        texto_inv = (
            f"📈 **Aporte Identificado:**\n\n"
            f"**Ativo:** {ticker}\n"
            f"**Data:** {data_hoje_formato(data_operacao)}\n"
            f"**Quantidade:** {qtd}\n"
            f"**Preço Pago (Un.):** R$ {preco:.2f}\n"
            f"**Total Descontado da Conta:** R$ {total:.2f}\n\n"
            f"O sistema realizará a *contabilidade dupla* (descontando o saldo e aumentando o patrimônio na carteira).\nConfirma a operação?"
        )

        estado_usuarios[message.chat.id] = {
            "tipo_fluxo": "investment",
            "email": email,
            "nome": nome_curto,
            "dados_investimento": inv
        }

        botoes = InlineKeyboardMarkup()
        botoes.row(
            InlineKeyboardButton("✅ Confirmar Aporte", callback_data="confirmar_investimento"),
            InlineKeyboardButton("❌ Descartar", callback_data="cancelar_lote")
        )

        bot.edit_message_text(texto_inv, message.chat.id, msg_aviso.message_id, reply_markup=botoes, parse_mode="Markdown")
        return

def data_hoje_formato(data_str=None):
    if not data_str:
        return datetime.now().strftime('%d/%m/%Y')
    try:
        dt = datetime.strptime(data_str, "%Y-%m-%d")
        return dt.strftime('%d/%m/%Y')
    except:
        return datetime.now().strftime('%d/%m/%Y')

@bot.message_handler(content_types=['text'])
def capturar_texto(message):
    orquestrar_mensagem(message, message.text, 'TEXT')

@bot.message_handler(content_types=['voice'])
def capturar_audio(message):
    try:
        arquivo_info = bot.get_file(message.voice.file_id)
        arquivo_baixado = bot.download_file(arquivo_info.file_path)
        orquestrar_mensagem(message, arquivo_baixado, 'AUDIO')
    except Exception:
        bot.reply_to(message, "❌ Falha ao processar arquivo de áudio.")

# =====================================================================
# 4. GESTÃO DOS BOTÕES (CONTABILIDADE DUPLA E CMS)
# =====================================================================
@bot.callback_query_handler(func=lambda call: True)
def processar_clique(call):
    chat_id = call.message.chat.id
    
    if call.data == "cancelar_lote":
        bot.edit_message_text("❌ Operação descartada.", chat_id, call.message.message_id)
        estado_usuarios.pop(chat_id, None)
        return
        
    estado = estado_usuarios.get(chat_id)
    if not estado:
        bot.edit_message_text("❌ Sessão expirada ou já confirmada.", chat_id, call.message.message_id)
        return
        
    email = estado['email']
    nome_curto = estado['nome']
    config = puxar_textos_cms()
    
    # -------------------------------------------------------------
    # SALVAMENTO DE GASTOS COMUNS (LOTE)
    # -------------------------------------------------------------
    if call.data == "confirmar_lote" and estado.get("tipo_fluxo") == "transaction":
        is_premium = estado['is_premium']
        lote = estado['lote']
        
        bot.edit_message_text("⏳ Salvando registros no cofre...", chat_id, call.message.message_id)
        
        url_trans = f"{settings.SUPABASE_URL}/rest/v1/transactions"
        data_hoje = datetime.now().strftime("%Y-%m-%d")
        
        pacote_banco = []
        soma_total = 0.0
        for item in lote:
            soma_total += float(item.get("amount", 0))
            pacote_banco.append({
                "user_email": email,
                "amount": item.get("amount"),
                "type": item.get("type"),
                "category": item.get("category"),
                "description": item.get("description"),
                "date": data_hoje,
                "source": "telegram"
            })
            
        try:
            res = requests.post(url_trans, headers=get_headers(), json=pacote_banco)
            if res.status_code in [200, 201]:
                
                categoria_exibicao = lote[0].get('category') if len(lote) == 1 else "Lote Múltiplo"
                tipo_exibicao = "Transação" if len(lote) == 1 else "Transações"
                
                msg_final = ""
                if is_premium:
                    msg_final = config['bot_premium_confirm']
                    msg_final = msg_final.replace("{nome}", nome_curto).replace("{valor}", f"{soma_total:.2f}").replace("{categoria}", categoria_exibicao).replace("{tipo}", tipo_exibicao)
                    
                    gasto_mais_caro = max(lote, key=lambda x: float(x.get('amount', 0)))
                    dados_oraculo = {
                        "user_email": email,
                        "amount": gasto_mais_caro.get('amount'),
                        "category": gasto_mais_caro.get('category'),
                        "type": gasto_mais_caro.get('type')
                    }
                    gerar_insight_oraculo(dados_oraculo)
                else:
                    msg_final = config['bot_free_confirm']
                    msg_final = msg_final.replace("{nome}", nome_curto).replace("{valor}", f"{soma_total:.2f}").replace("{categoria}", categoria_exibicao).replace("{tipo}", tipo_exibicao)
                    
                    # UPSELL INTELIGENTE (Se foi a primeira interação do dia ou o penúltimo áudio)
                    msg_final += "\n\n" + config.get('bot_free_upsell', '')
                        
                bot.edit_message_text(msg_final, chat_id, call.message.message_id)
                estado_usuarios.pop(chat_id, None) 
            else:
                bot.edit_message_text("❌ Falha de segurança ao gravar no banco.", chat_id, call.message.message_id)
        except Exception:
            bot.edit_message_text("❌ Erro grave de conexão com o banco.", chat_id, call.message.message_id)


    # -------------------------------------------------------------
    # SALVAMENTO DE INVESTIMENTO (CONTABILIDADE DUPLA)
    # -------------------------------------------------------------
    if call.data == "confirmar_investimento" and estado.get("tipo_fluxo") == "investment":
        bot.edit_message_text("⏳ Realizando Contabilidade Dupla na Carteira...", chat_id, call.message.message_id)
        
        inv = estado['dados_investimento']
        ticker = inv.get("asset_code", "ATIVO").upper()
        preco = float(inv.get("unit_price", 0))
        qtd = float(inv.get("quantity", 0))
        total_gasto = preco * qtd
        data_operacao = inv.get("operation_date") or datetime.now().strftime("%Y-%m-%d")
        
        # 1. Tira do Saldo (Conta Corrente)
        url_trans = f"{settings.SUPABASE_URL}/rest/v1/transactions"
        payload_trans = {
            "user_email": email,
            "amount": total_gasto,
            "type": "EXPENSE",
            "category": "INVESTIMENTO",
            "description": f"Aporte Carteira ({ticker})",
            "date": data_operacao,
            "source": "telegram"
        }
        
        # 2. Guarda na Gaveta de Ações (Livro Razão)
        url_inv = f"{settings.SUPABASE_URL}/rest/v1/investment_transactions"
        payload_inv = {
            "user_email": email,
            "asset_code": ticker,
            "asset_type": inv.get("asset_type", "ACAO"),
            "operation_type": "BUY",
            "quantity": qtd,
            "unit_price": preco,
            "total_amount": total_gasto,
            "operation_date": f"{data_operacao}T12:00:00Z"
        }
        
        try:
            # Roda as duas gravações juntas
            r1 = requests.post(url_trans, headers=get_headers(), json=payload_trans)
            r2 = requests.post(url_inv, headers=get_headers(), json=payload_inv)
            
            if r1.status_code in [200, 201] and r2.status_code in [200, 201]:
                msg_final = config.get('bot_premium_confirm', 'Aporte confirmado!')
                msg_final = msg_final.replace("{nome}", nome_curto).replace("{valor}", f"{total_gasto:.2f}").replace("{categoria}", f"Carteira {ticker}").replace("{tipo}", "Aporte")
                
                # Gera uma análise de carteira via Oráculo
                dados_oraculo = {
                    "user_email": email,
                    "amount": total_gasto,
                    "category": "INVESTIMENTO",
                    "type": "EXPENSE"
                }
                gerar_insight_oraculo(dados_oraculo)
                
                bot.edit_message_text(msg_final, chat_id, call.message.message_id)
                estado_usuarios.pop(chat_id, None)
            else:
                 bot.edit_message_text("❌ Falha de segurança na Contabilidade Dupla do banco.", chat_id, call.message.message_id)
        except Exception:
             bot.edit_message_text("❌ Erro de conexão ao registrar o Aporte.", chat_id, call.message.message_id)

if __name__ == "__main__":
    print("🚀 NÚCLEO NICKEL_IA ONLINE (Lotes, Freios de Mão e Home Broker Integrados)")
    bot.infinity_polling()