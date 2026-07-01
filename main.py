import os
import json
import time
import random
import hashlib
import html as html_mod
import logging
import threading
import datetime
import schedule
import feedparser
import requests
import telebot
import google.generativeai as genai
from flask import Flask

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Variáveis de ambiente
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
ALI_APP_KEY      = os.getenv("ALI_APP_KEY", "")
ALI_APP_SECRET   = os.getenv("ALI_APP_SECRET", "")
ALI_TRACKING_ID  = os.getenv("ALI_TRACKING_ID", "")

# ---------------------------------------------------------------------------
# Canais de destino e constantes de admin
# ---------------------------------------------------------------------------
CANAIS_DESTINO    = ["@gruposecretodomago", "@AchadosSemImposto"]
ADMIN_ID          = 1166455103          # ID do dono do bot (MagoAventureiro)
CANAL_VERIFICACAO = "@gruposecretodomago"    # Canal usado para verificar membros no sorteio
GRUPO_META_ID     = "@gruposecretodomago"    # Grupo cujos membros contam para a META_SORTEIO

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE       = os.path.join(BASE_DIR, "config.json")
PARTICIPANTS_FILE = os.path.join(BASE_DIR, "participants.json")
AUDIT_FILE        = os.path.join(BASE_DIR, "audit.log")

# ---------------------------------------------------------------------------
# Gestão de configuração e participantes (persistência em JSON)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "META_SORTEIO":       1000,
    "PREMIO_ATUAL":       "Sem prêmio definido",
    "VENCEDOR_PENDENTE":  None,   # {"user_id", "nome", "deadline_iso"}
    "ALERTA_90_ENVIADO":  False,  # Evita repetir o aviso de 90% da meta
}

def _get_membros_grupo() -> int:
    """Retorna a contagem real de membros do GRUPO_META_ID. Retorna -1 em caso de erro."""
    try:
        return bot.get_chat_member_count(GRUPO_META_ID)
    except telebot.apihelper.ApiTelegramException as exc:
        codigo = exc.error_code
        descricao = exc.description
        if "Forbidden" in descricao:
            logger.error(
                "[MEMBROS] Acesso negado ao grupo %s (bot não é membro/admin?). "
                "Código: %s | Descrição: %s",
                GRUPO_META_ID, codigo, descricao, exc_info=True,
            )
        elif "chat not found" in descricao.lower():
            logger.error(
                "[MEMBROS] Grupo %s não encontrado (ID/username errado?). "
                "Código: %s | Descrição: %s",
                GRUPO_META_ID, codigo, descricao, exc_info=True,
            )
        else:
            logger.error(
                "[MEMBROS] Erro inesperado ao consultar membros do grupo %s. "
                "Código: %s | Descrição: %s",
                GRUPO_META_ID, codigo, descricao, exc_info=True,
            )
        return -1
    except Exception as exc:
        logger.error(
            "[MEMBROS] Erro genérico ao consultar membros do grupo %s: %s",
            GRUPO_META_ID, exc, exc_info=True,
        )
        return -1

def _carregar_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in _DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            logger.error("Erro ao carregar config.json: %s", e)
    return _DEFAULT_CONFIG.copy()

def _salvar_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Erro ao salvar config.json: %s", e)

def _audit(user_id: int, acao: str, detalhes: str = "") -> None:
    """Grava uma linha de auditoria em audit.log."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = f"[{ts}] user={user_id} | acao={acao}"
    if detalhes:
        linha += f" | {detalhes}"
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception as e:
        logger.error("Falha ao gravar audit.log: %s", e)

def _carregar_participantes() -> dict:
    """Retorna dict {str(user_id): {nome, username, inscrito_em}}"""
    if os.path.exists(PARTICIPANTS_FILE):
        try:
            with open(PARTICIPANTS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Erro ao carregar participants.json: %s", e)
    return {}

def _salvar_participantes(data: dict) -> None:
    try:
        with open(PARTICIPANTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Erro ao salvar participants.json: %s", e)

# Timer global de reivindicação (cancelável)
_timer_reivindicacao: threading.Timer | None = None

# ---------------------------------------------------------------------------
# Inicialização do bot e Gemini
# ---------------------------------------------------------------------------
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
_gemini_model = genai.GenerativeModel('gemini-pro')

_start_time = datetime.datetime.now()  # uptime tracking

# ---------------------------------------------------------------------------
# Palavras-chave para busca de produtos
# ---------------------------------------------------------------------------
KEYWORDS = [
    "Impressora 3D",
    "Resina 3D",
    "Filamento 3D",
    "Creality",
    "Elegoo",
    "Anycubic",
    "Bambulab",
    "PLA",
    "ABS",
    "3d Printer",
    "3D Resin",
    "3D Filament",
    "Resin 3D",
    "Resin Printer",
    "FDM Printer",
    "Sunlu",
    "Jayo",       
]
_keyword_index = 0

PRECO_MIN = 2500 # $25
PRECO_MAX = 400000 # $4000

# ---------------------------------------------------------------------------
# Feeds RSS de impressão 3D
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    "https://all3dp.com/feed/",
    "https://3dprinting.com/feed/",
    "https://www.3dnatives.com/en/feed/",
    "https://hackaday.com/tag/3d-printing/feed/",
]

# ---------------------------------------------------------------------------
# Helpers AliExpress Affiliate API
# ---------------------------------------------------------------------------

def _ali_sign(params: dict, secret: str) -> str:
    """Gera a assinatura MD5 para a API do AliExpress."""
    sorted_params = sorted(params.items())
    base_string = secret + "".join(f"{k}{v}" for k, v in sorted_params) + secret
    return hashlib.md5(base_string.encode("utf-8")).heigest().upper()

def _ali_request(method: str, extra_params: dict) -> dict:
    """Faz uma chamada à API de afiliados do AliExpress."""
    url = "https://api-sg.aliexpress.com/sync"
    params = {
        "app_key": ALI_APP_KEY,
        "method": method,
        "sign_method": "md5",
        "timestamp": str(int(time.time() * 1000)),
        "format": "json",
        "v": "2.0",
        **extra_params,
    }
    params["sign"] = _ali_sign(params, ALI_APP_SECRET)
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()

def buscar_produtos_aliexpress(keyword: str) -> list[dict]:
    """Busca produtos na API de afiliados do AliExpress."""
    data = _ali_request(
        "aliexpress.affiliate.product.query",
        {
            "keywords": keyword,
            "tracking_id": ALI_TRACKING_ID,
            "fields": "product_id,product_title,sale_price,original_price,product_main_image_url,promotion_link",
            "page_size": "50",
            "page_no": "1",
            "sort": "LAST_VOLUME_DESC",
            "currency": "USD", # Alterado para USD
            "target_language": "PT",
            "target_currency": "USD", # Alterado para USD
            "min_sale_price": str(PRECO_MIN / 100), # Convertendo para USD
            "max_sale_price": str(PRECO_MAX / 100), # Convertendo para USD
        },
    )
    produtos = (
        data.get("aliexpress_affiliate_product_query_response", {})
        .get("resp_result", {})
        .get("result", {})
        .get("products", {})
        .get("product", [])
    )
    return produtos

def filtrar_por_preco(produtos: list[dict]) -> list[dict]:
    """Filtra produtos dentro da faixa de preço desejada."""
    filtrados = []
    for p in produtos:
        try:
            preco = float(p.get("sale_price", 0))
            # Preços da API são em centavos, então dividimos por 100 para comparar com PRECO_MIN/MAX em dólares
            if (PRECO_MIN / 100) <= preco <= (PRECO_MAX / 100):
                filtrados.append(p)
        except (ValueError, TypeError):
            continue
    return filtrados

def gerar_link_afiliado(product_id: str) -> str | None:
    """Gera um link de afiliado encurtado para o produto."""
    return _gerar_link_afiliado_url(f"https://www.aliexpress.com/item/{product_id}.html")

def _gerar_link_afiliado_url(url: str) -> str | None:
    """Gera link de afiliado para qualquer URL do AliExpress (produto ou busca)."""
    try:
        data = _ali_request(
            "aliexpress.affiliate.link.generate",
            {
                "promotion_link_type": "0",
                "source_values": url,
                "tracking_id": ALI_TRACKING_ID,
            },
        )
        links = (
            data.get("aliexpress_affiliate_link_generate_response", {})
            .get("resp_result", {})
            .get("result", {})
            .get("promotion_links", {})
            .get("promotion_link", [])
        )
        if links:
            return links[0].get("promotion_link")
    except Exception as e:
        logger.error("Erro ao gerar link de afiliado: %s", e)
    return None

# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

def gemini_copy_promocao(titulo: str, preco_original: float, preco_desconto: float) -> str:
    """Gera copy de promoção com o Gemini. Em caso de falha retorna template local."""
    prompt = (
        "Aja como um maker experiente, entusiasmado e muito descontraído. "
        "Escreva uma copy CURTA (máximo 500 caracteres) recomendando este produto de Impressão 3D para amigos. "
        "Estrutura obrigatória: "
        "1. Uma linha de título chamativo com emojis. "
        "2. Dois ou três frases explicando o benefício principal do produto. "
        "Regras OBRIGATÓRIAS: "
        "- Use SOMENTE texto puro, sem markdown (*texto*, **texto**), sem HTML, sem colchetes. "
        "- Não inclua preços nem links no texto (o sistema adiciona isso automaticamente). "
        "- Máximo 500 caracteres no total.\n\n"
        f"Produto: {titulo}"
    )
    try:
        response = _gemini_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.warning("Gemini indisponível para copy de produto, usando template local: %s", e)
        titulo_curto = titulo[:80] + "..." if len(titulo) > 80 else titulo
        desconto_pct = int((1 - preco_desconto / preco_original) * 100) if preco_original > 0 else 0
        if desconto_pct >= 10:
            return (
                f"🔥 Oferta imperdível para a galera da impressão 3D!\n"
                f"{titulo_curto}\n"
                f"Desconto de {desconto_pct}% — aproveite enquanto dura! 🎯🖨️"
            )
        return (
            f"🖨️ Achado do dia para makers!\n"
            f"{titulo_curto}\n"
            f"Preço ótimo, qualidade garantida. Corre antes que acabe! 🚀"
        )

def gemini_dica_educativa() -> str:
    """Solicita ao Gemini uma dica educativa sobre impressão 3D."""
    prompt = (
        "Aja como um maker experiente trocando ideia com amigos no WhatsApp. "
        "Dê uma dica técnica de impressão 3D de forma extremamente humana e descontraída. "
        "Termine com uma pergunta rápida. Seja breve e use emojis."
    )
    response = _gemini_model.generate_content(prompt)
    return response.text.strip()

def gemini_resumir_noticia(titulo: str, conteudo: str) -> str:
    """Resume uma notícia de impressão 3D com o Gemini."""
    prompt = (
        "Aja como um maker experiente. Resuma esta notícia do mundo da impressão 3D de forma simples, "
        "humana e descontraída. Termine com uma pergunta para engajamento. "
        "Seja breve e não use jargões difíceis.\n\n"
        f"Título: {titulo}\nConteúdo: {conteudo[:1500]}"
    )
    response = _gemini_model.generate_content(prompt)
    return response.text.strip()

# ---------------------------------------------------------------------------
# Envio de mensagens para os canais
# ---------------------------------------------------------------------------

def enviar_mensagem(texto: str) -> None:
    """Envia uma mensagem para todos os canais de destino."""
    for canal in CANAIS_DESTINO:
        try:
            bot.send_message(canal, texto, parse_mode="Markdown")
            logger.info("Mensagem enviada para %s", canal)
        except Exception as e:
            logger.error("Erro ao enviar mensagem para %s: %s", canal, e)

def enviar_mensagem_com_foto(texto: str, foto_url: str) -> None:
    """Envia uma mensagem com foto para todos os canais de destino."""
    for canal in CANAIS_DESTINO:
        try:
            bot.send_photo(canal, foto_url, caption=texto, parse_mode="Markdown")
            logger.info("Foto enviada para %s", canal)
        except Exception as e:
            logger.warning("Falha ao enviar foto para %s, tentando só texto: %s", canal, e)
            try:
                bot.send_message(canal, texto, parse_mode="Markdown")
            except Exception as e2:
                logger.error("Erro ao enviar texto de fallback para %s: %s", canal, e2)

def enviar_promocao(texto_html: str, link_afiliado: str, foto_url: str) -> None:
    """Envia promoção com foto + botão inline. Garante sempre foto, copy e link afiliado."""
    teclado = telebot.types.InlineKeyboardMarkup()
    teclado.add(telebot.types.InlineKeyboardButton("🛒 Ver Oferta no AliExpress", url=link_afiliado))

    # Telegram: caption de foto ≤ 1024 chars. Se for maior, envia só texto.
    if len(texto_html) > 1024:
        logger.warning("Texto da promoção muito longo (%d chars), enviando sem foto.", len(texto_html))
        enviar_mensagem(texto_html)
        return

    # Tenta enviar com foto
    enviado = False
    for canal in CANAIS_DESTINO:
        try:
            bot.send_photo(canal, photo=foto_url, caption=texto_html, parse_mode="HTML", reply_markup=teclado)
            logger.info("Promoção (foto+botão) enviada para %s", canal)
            enviado = True
        except Exception as e:
            logger.warning("[PROMO] send_photo falhou para %s: %s", canal, e)

    # Tentativa 2: mensagem de texto completa + botão (sem foto)
    if not enviado:
        try:
            bot.send_message(canal, texto_html[:4090],
                             parse_mode="HTML", reply_markup=teclado)
            logger.info("Promoção (texto+botão) enviada para %s", canal)
            enviado = True
        except Exception as e:
            logger.warning("[PROMO] send_message HTML falhou para %s: %s", canal, e)

    # Tentativa 3: texto puro sem parse_mode + botão (último recurso)
    if not enviado:
        try:
            texto_puro = texto_html.replace("<s>", "").replace("</s>", "") \
                                   .replace("<b>", "").replace("</b>", "") \
                                   .replace("<i>", "").replace("</i>", "")
            bot.send_message(canal, texto_puro[:4090], reply_markup=teclado)
            logger.info("Promoção (texto puro+botão) enviada para %s", canal)
            enviado = True
        except Exception as e:
            logger.warning("[PROMO] send_message puro falhou para %s: %s", canal, e)


# ---------------------------------------------------------------------------
# Funções de agendamento
# ---------------------------------------------------------------------------

def agendar_promocao():
    global _keyword_index
    keyword = KEYWORDS[_keyword_index]
    _keyword_index = (_keyword_index + 1) % len(KEYWORDS)

    logger.info("Buscando promoções para a palavra-chave: %s", keyword)
    produtos = buscar_produtos_aliexpress(keyword)
    produtos_filtrados = filtrar_por_preco(produtos)

    if not produtos_filtrados:
        logger.info("Nenhuma promoção encontrada para %s na faixa de preço.", keyword)
        return

    produto = random.choice(produtos_filtrados)

    product_title = produto.get("product_title", "Produto sem título")
    product_image = produto.get("product_main_image_url", "")
    product_id = produto.get("product_id")
    original_price = float(produto.get("original_price", 0))
    sale_price = float(produto.get("sale_price", 0))

    # Preços da API são em centavos, então dividimos por 100
    original_price_usd = original_price / 100
    sale_price_usd = sale_price / 100

    # Gerar copy com Gemini
    copy = gemini_copy_promocao(product_title, original_price_usd, sale_price_usd)

    # Gerar link de afiliado
    link_afiliado = gerar_link_afiliado(product_id)
    if not link_afiliado:
        logger.error("Falha ao gerar link de afiliado para o produto %s", product_id)
        return

    message_text = (
        f"✨ **PROMOÇÃO IMPERDÍVEL!** ✨\n\n"
        f"{copy}\n\n"
        f"💰 De: <s>US$ {original_price_usd:.2f}</s>\n"
        f"🔥 Por: **US$ {sale_price_usd:.2f}**\n"
        f"🔗 [Compre agora!]({link_afiliado})"
    )

    enviar_promocao(message_text, link_afiliado, product_image)

def agendar_noticia():
    logger.info("Buscando notícias de impressão 3D...")
    feed_url = random.choice(RSS_FEEDS)
    feed = feedparser.parse(feed_url)

    if not feed.entries:
        logger.info("Nenhuma notícia encontrada no feed: %s", feed_url)
        return

    noticia = random.choice(feed.entries)
    titulo = noticia.title
    link = noticia.link
    conteudo = noticia.summary if hasattr(noticia, 'summary') else noticia.title

    resumo_ia = gemini_resumir_noticia(titulo, conteudo)

    message_text = (
        f"📰 **NOTÍCIA DO MUNDO 3D!** 📰\n\n"
        f"{resumo_ia}\n\n"
        f"Leia mais: [Aqui]({link})"
    )
    enviar_mensagem(message_text)

def agendar_dica():
    logger.info("Gerando dica de impressão 3D...")
    dica_ia = gemini_dica_educativa()
    message_text = f"💡 **DICA MAKER!** 💡\n\n{dica_ia}"
    enviar_mensagem(message_text)

# ---------------------------------------------------------------------------
# Comandos do bot
# ---------------------------------------------------------------------------

def _is_admin(message) -> bool:
    return message.from_user.id == ADMIN_ID

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    _audit(message.from_user.id, "start/menu")
    if _is_admin(message):
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("🎰 Iniciar Sorteio", callback_data='iniciar_sorteio'),
            telebot.types.InlineKeyboardButton("📊 Status do Bot", callback_data='status_bot'),
            telebot.types.InlineKeyboardButton("⚙️ Configurar Meta", callback_data='config_meta'),
            telebot.types.InlineKeyboardButton("🛒 Forçar Promoção", callback_data='forcar_promocao'),
            telebot.types.InlineKeyboardButton("🛠️ Ferramentas Maker", callback_data='ferramentas_maker'),
            telebot.types.InlineKeyboardButton("📝 Ver Inscritos", callback_data='ver_inscritos'),
            telebot.types.InlineKeyboardButton("🏓 Ping / Saúde", callback_data='ping_saude'),
            telebot.types.InlineKeyboardButton("🔄 Resetar Sorteio", callback_data='resetar_sorteio'),
            telebot.types.InlineKeyboardButton("❌ Fechar Menu", callback_data='fechar_menu')
        )
        bot.send_message(message.chat.id, "👋 Olá Mago! Seu painel de controle está aqui:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, 
                         "👋 Olá! Sou o bot de promoções 3D. "
                         "Fique ligado para as melhores ofertas e sorteios! "
                         "Use /participar para entrar no sorteio atual.")

@bot.message_handler(commands=['participar'])
def participar_sorteio(message):
    _audit(message.from_user.id, "participar")
    user_id = str(message.from_user.id)
    participantes = _carregar_participantes()
    
    if user_id in participantes:
        bot.reply_to(message, "Você já está participando do sorteio atual! Boa sorte! 🍀")
        return

    # Verifica se o usuário é membro do canal de verificação
    try:
        chat_member = bot.get_chat_member(CANAL_VERIFICACAO, message.from_user.id)
        if chat_member.status not in ['member', 'administrator', 'creator']:
            bot.reply_to(message, 
                         f"Para participar do sorteio, você precisa ser membro do canal {CANAL_VERIFICACAO}. "
                         "Entre no canal e tente novamente! 😉")
            return
    except Exception as e:
        logger.error("Erro ao verificar membro do canal %s: %s", CANAL_VERIFICACAO, e)
        bot.reply_to(message, "Ocorreu um erro ao verificar sua participação. Tente novamente mais tarde.")
        return

    participantes[user_id] = {
        "nome": message.from_user.first_name,
        "username": message.from_user.username,
        "inscrito_em": datetime.datetime.now().isoformat()
    }
    _salvar_participantes(participantes)
    bot.reply_to(message, "🎉 Parabéns! Você entrou para o sorteio! Boa sorte! 🍀")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    _audit(call.from_user.id, "callback_query", call.data)
    if not _is_admin(call):
        bot.answer_callback_query(call.id, "Apenas administradores podem usar este menu.")
        return

    if call.data == 'iniciar_sorteio':
        iniciar_sorteio_admin(call.message)
    elif call.data == 'status_bot':
        status_bot_admin(call.message)
    elif call.data == 'config_meta':
        config_meta_admin(call.message)
    elif call.data == 'forcar_promocao':
        bot.answer_callback_query(call.id, "Forçando promoção...")
        agendar_promocao()
        bot.send_message(call.message.chat.id, "Promoção forçada e enviada!")
    elif call.data == 'ferramentas_maker':
        bot.answer_callback_query(call.id, "Ferramentas Maker (em breve)!")
    elif call.data == 'ver_inscritos':
        ver_inscritos_admin(call.message)
    elif call.data == 'ping_saude':
        ping_saude_admin(call.message)
    elif call.data == 'resetar_sorteio':
        resetar_sorteio_admin(call.message)
    elif call.data == 'fechar_menu':
        bot.delete_message(call.message.chat.id, call.message.message_id)

def iniciar_sorteio_admin(message):
    cfg = _carregar_config()
    participantes = _carregar_participantes()
    num_participantes = len(participantes)
    meta = cfg["META_SORTEIO"]
    premio = cfg["PREMIO_ATUAL"]

    if num_participantes < meta:
        bot.send_message(message.chat.id, 
                         f"A meta de {meta} participantes ainda não foi atingida. "
                         f"Temos {num_participantes} inscritos. Faltam {meta - num_participantes}!\n"
                         f"Prêmio atual: {premio}")
        return

    if cfg["VENCEDOR_PENDENTE"]:
        bot.send_message(message.chat.id, 
                         "Já existe um sorteio pendente de reivindicação. "
                         "Aguarde o vencedor atual reivindicar ou resete o sorteio.")
        return

    vencedor_id = random.choice(list(participantes.keys()))
    vencedor = participantes[vencedor_id]

    bot.send_message(message.chat.id, 
                     f"🎉 **TEMOS UM VENCEDOR!** 🎉\n\n"
                     f"Parabéns a @{vencedor['username']} ({vencedor['nome']})! "
                     f"Você ganhou: **{premio}**!\n\n"
                     "O vencedor tem 24 horas para reivindicar o prêmio. "
                     "Entraremos em contato!", parse_mode="Markdown")
    
    # Inicia timer de reivindicação
    deadline = datetime.datetime.now() + datetime.timedelta(hours=24)
    cfg["VENCEDOR_PENDENTE"] = {
        "user_id": vencedor_id,
        "nome": vencedor['nome'],
        "username": vencedor['username'],
        "premio": premio,
        "deadline_iso": deadline.isoformat()
    }
    _salvar_config(cfg)

    global _timer_reivindicacao
    if _timer_reivindicacao:
        _timer_reivindicacao.cancel()
    _timer_reivindicacao = threading.Timer(24 * 3600, _verificar_reivindicacao_automatica, args=[vencedor_id])
    _timer_reivindicacao.start()

def _verificar_reivindicacao_automatica(vencedor_id_original):
    cfg = _carregar_config()
    if cfg["VENCEDOR_PENDENTE"] and cfg["VENCEDOR_PENDENTE"]["user_id"] == vencedor_id_original:
        bot.send_message(ADMIN_ID, 
                         f"🚨 **ALERTA: Vencedor anterior (@{cfg['VENCEDOR_PENDENTE']['username']}) não reivindicou o prêmio!**\n"
                         "O sorteio foi resetado automaticamente. Você pode iniciar um novo.")
        _resetar_sorteio_dados()

def status_bot_admin(message):
    cfg = _carregar_config()
    participantes = _carregar_participantes()
    num_membros_grupo = _get_membros_grupo()

    uptime = datetime.datetime.now() - _start_time
    uptime_str = str(uptime).split('.')[0] # Remove microssegundos

    status_text = (
        f"📊 **STATUS DO BOT** 📊\n\n"
        f"**Uptime:** {uptime_str}\n"
        f"**Membros no Grupo ({GRUPO_META_ID}):** {num_membros_grupo}\n"
        f"**Participantes do Sorteio:** {len(participantes)}\n"
        f"**Meta do Sorteio:** {cfg['META_SORTEIO']}\n"
        f"**Prêmio Atual:** {cfg['PREMIO_ATUAL']}\n"
    )
    if cfg["VENCEDOR_PENDENTE"]:
        vencedor = cfg["VENCEDOR_PENDENTE"]
        deadline = datetime.datetime.fromisoformat(vencedor["deadline_iso"])
        tempo_restante = deadline - datetime.datetime.now()
        horas = int(tempo_restante.total_seconds() // 3600)
        minutos = int((tempo_restante.total_seconds() % 3600) // 60)
        status_text += (
            f"**Vencedor Pendente:** @{vencedor['username']} ({vencedor['nome']})\n"
            f"**Prêmio:** {vencedor['premio']}\n"
            f"**Tempo Restante:** {horas}h {minutos}m\n"
        )
    bot.send_message(message.chat.id, status_text, parse_mode="Markdown")

def config_meta_admin(message):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton("✏️ Editar Meta de Inscritos", callback_data='editar_meta_sorteio'),
        telebot.types.InlineKeyboardButton("🎁 Editar Prêmio do Sorteio", callback_data='editar_premio_sorteio')
    )
    bot.send_message(message.chat.id, "O que você gostaria de configurar?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ['editar_meta_sorteio', 'editar_premio_sorteio'])
def handle_config_meta_callback(call):
    _audit(call.from_user.id, "handle_config_meta_callback", call.data)
    if not _is_admin(call):
        bot.answer_callback_query(call.id, "Apenas administradores podem usar este menu.")
        return

    if call.data == 'editar_meta_sorteio':
        msg = bot.send_message(call.message.chat.id, "Por favor, digite a nova meta de inscritos para o sorteio:")
        bot.register_next_step_handler(msg, process_nova_meta)
    elif call.data == 'editar_premio_sorteio':
        msg = bot.send_message(call.message.chat.id, "Por favor, digite o novo prêmio para o sorteio:")
        bot.register_next_step_handler(msg, process_novo_premio)

def process_nova_meta(message):
    _audit(message.from_user.id, "process_nova_meta", message.text)
    if not _is_admin(message):
        bot.send_message(message.chat.id, "Apenas administradores podem configurar a meta.")
        return
    try:
        nova_meta = int(message.text)
        if nova_meta <= 0:
            raise ValueError("A meta deve ser um número positivo.")
        cfg = _carregar_config()
        cfg["META_SORTEIO"] = nova_meta
        cfg["ALERTA_90_ENVIADO"] = False # Reseta o alerta de 90%
        _salvar_config(cfg)
        bot.send_message(message.chat.id, f"Meta de inscritos atualizada para {nova_meta}!")
    except ValueError:
        bot.send_message(message.chat.id, "Valor inválido. Por favor, digite um número inteiro positivo.")

def process_novo_premio(message):
    _audit(message.from_user.id, "process_novo_premio", message.text)
    if not _is_admin(message):
        bot.send_message(message.chat.id, "Apenas administradores podem configurar o prêmio.")
        return
    novo_premio = message.text.strip()
    if not novo_premio:
        bot.send_message(message.chat.id, "O prêmio não pode ser vazio.")
        return
    cfg = _carregar_config()
    cfg["PREMIO_ATUAL"] = novo_premio
    _salvar_config(cfg)
    bot.send_message(message.chat.id, f"Prêmio do sorteio atualizado para: {novo_premio}!")

def ver_inscritos_admin(message):
    participantes = _carregar_participantes()
    if not participantes:
        bot.send_message(message.chat.id, "Nenhum participante inscrito ainda.")
        return
    
    inscritos_list = [f"@{p['username']} ({p['nome']})" for p in participantes.values()]
    inscritos_text = "📝 **LISTA DE INSCRITOS** 📝\n\n" + "\n".join(inscritos_list)
    bot.send_message(message.chat.id, inscritos_text, parse_mode="Markdown")

def ping_saude_admin(message):
    uptime = datetime.datetime.now() - _start_time
    uptime_str = str(uptime).split('.')[0] # Remove microssegundos
    bot.send_message(message.chat.id, f"🏓 **Pong!** Bot online há {uptime_str}")

def resetar_sorteio_admin(message):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Sim, Resetar", callback_data='confirmar_reset_sorteio'),
        telebot.types.InlineKeyboardButton("❌ Não, Cancelar", callback_data='cancelar_reset_sorteio')
    )
    bot.send_message(message.chat.id, "Tem certeza que deseja resetar o sorteio? Isso apagará todos os participantes e o vencedor pendente.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ['confirmar_reset_sorteio', 'cancelar_reset_sorteio'])
def handle_reset_sorteio_callback(call):
    _audit(call.from_user.id, "handle_reset_sorteio_callback", call.data)
    if not _is_admin(call):
        bot.answer_callback_query(call.id, "Apenas administradores podem resetar o sorteio.")
        return

    if call.data == 'confirmar_reset_sorteio':
        _resetar_sorteio_dados()
        bot.edit_message_text("Sorteio resetado com sucesso! Todos os participantes e o vencedor pendente foram removidos.", 
                              call.message.chat.id, call.message.message_id)
    elif call.data == 'cancelar_reset_sorteio':
        bot.edit_message_text("Reset do sorteio cancelado.", 
                              call.message.chat.id, call.message.message_id)

def _resetar_sorteio_dados():
    _salvar_participantes({})
    cfg = _carregar_config()
    cfg["VENCEDOR_PENDENTE"] = None
    cfg["ALERTA_90_ENVIADO"] = False
    _salvar_config(cfg)
    global _timer_reivindicacao
    if _timer_reivindicacao:
        _timer_reivindicacao.cancel()
        _timer_reivindicacao = None

# ---------------------------------------------------------------------------
# Agendamento de tarefas
# ---------------------------------------------------------------------------

def _job_thread():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Agendamentos
schedule.every(30).minutes.do(agendar_promocao)
schedule.every(30).minutes.do(agendar_noticia)
schedule.every(30).minutes.do(agendar_dica)
schedule.every(1).hour.do(lambda: logger.info("Checando meta de sorteio...") or _checar_meta_sorteio())

def _checar_meta_sorteio():
    cfg = _carregar_config()
    if cfg["VENCEDOR_PENDENTE"]:
        return # Não checa meta se já tem sorteio pendente

    num_membros = _get_membros_grupo()
    meta = cfg["META_SORTEIO"]

    if num_membros == -1:
        logger.warning("Não foi possível obter o número de membros do grupo. Pulando checagem de meta.")
        return

    if num_membros >= meta:
        logger.info("Meta de %d membros atingida! Iniciando sorteio automático.", meta)
        # Envia mensagem para o admin iniciar o sorteio
        bot.send_message(ADMIN_ID, 
                         f"🎉 **META ATINGIDA!** 🎉\n\n"
                         f"O grupo {GRUPO_META_ID} atingiu a meta de {meta} membros! "
                         "Use o comando /iniciar_sorteio no painel para sortear o vencedor!", 
                         parse_mode="Markdown")
    elif num_membros >= meta * 0.9 and not cfg["ALERTA_90_ENVIADO"]:
        logger.info("90%% da meta de %d membros atingida! Enviando alerta.", meta)
        bot.send_message(ADMIN_ID, 
                         f"🔔 **QUASE LÁ!** 🔔\n\n"
                         f"O grupo {GRUPO_META_ID} está com {num_membros} membros, "
                         f"atingindo 90% da meta de {meta}! Faltam apenas {meta - num_membros} para o sorteio!", 
                         parse_mode="Markdown")
        cfg["ALERTA_90_ENVIADO"] = True
        _salvar_config(cfg)

# ---------------------------------------------------------------------------
# Health Check para Render (Flask) e inicialização do bot
# ---------------------------------------------------------------------------

app = Flask(__name__)

@app.route('/health')
def health_check():
    return 'OK', 200

def start_flask_app():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

if __name__ == '__main__':
    # Inicia o Flask em uma thread separada
    flask_thread = threading.Thread(target=start_flask_app)
    flask_thread.start()

    # Inicia o agendador em uma thread separada
    scheduler_thread = threading.Thread(target=_job_thread)
    scheduler_thread.start()

    # Inicia o bot em polling
    logger.info("Bot iniciando polling...")
    bot.remove_webhook()
    bot.polling(none_stop=True, interval=0, timeout=20)
    logger.info("Bot encerrado.")
