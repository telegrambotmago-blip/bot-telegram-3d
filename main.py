import os
import json
import time
import random
import hashlib
import html as html_mod
import logging

# --- TESTE DE DIAGNÓSTICO ---
print("--- DIAGNÓSTICO DE VARIÁVEIS ---")
print(f"Variáveis encontradas: {list(os.environ.keys())}")
if "TELEGRAM_TOKEN" in os.environ:
    print("✅ TELEGRAM_TOKEN encontrado!")
else:
    print("❌ TELEGRAM_TOKEN NÃO FOI ENCONTRADO!")
print("-------------------------------")

import threading
import datetime
import schedule
import feedparser
import requests
import telebot
from google import genai
from flask import Flask

# --- SERVIDOR PARA MANTER ONLINE 24/7 ---
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot is alive!", 200

def run_flask():
    server.run(host='0.0.0.0', port=10000)

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Variáveis de ambiente (configuradas nos Secrets do Replit)
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
ALI_APP_KEY      = os.environ["ALI_APP_KEY"]
ALI_APP_SECRET   = os.environ["ALI_APP_SECRET"]
ALI_TRACKING_ID  = os.environ["ALI_TRACKING_ID"]

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
_gemini = genai.Client(api_key=GEMINI_API_KEY)

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

PRECO_MIN = 250000
PRECO_MAX = 70000000

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
    return hashlib.md5(base_string.encode("utf-8")).hexdigest().upper()


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
            "currency": "BRL",
            "target_language": "PT",
            "target_currency": "BRL",
            "min_sale_price": "25",
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
            if PRECO_MIN <= preco <= PRECO_MAX:
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
        response = _gemini.models.generate_content(model="gemini-2.5-flash", contents=prompt)
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
    response = _gemini.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return response.text.strip()


def gemini_resumir_noticia(titulo: str, conteudo: str) -> str:
    """Resume uma notícia de impressão 3D com o Gemini."""
    prompt = (
        "Aja como um maker experiente. Resuma esta notícia do mundo da impressão 3D de forma simples, "
        "humana e descontraída. Termine com uma pergunta para engajamento. "
        "Seja breve e não use jargões difíceis.\n\n"
        f"Título: {titulo}\nConteúdo: {conteudo[:1500]}"
    )
    response = _gemini.models.generate_content(model="gemini-2.5-flash", contents=prompt)
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

    # Telegram: caption de foto ≤ 1024 chars; mensagem de texto ≤ 4096 chars
    CAPTION_MAX = 1020
    caption = texto_html if len(texto_html) <= CAPTION_MAX else texto_html[:CAPTION_MAX - 3] + "..."

    for canal in CANAIS_DESTINO:
        enviado = False

        # Tentativa 1: foto com caption + botão (caminho ideal)
        if foto_url:
            try:
                bot.send_photo(canal, foto_url, caption=caption,
                               parse_mode="HTML", reply_markup=teclado)
                logger.info("Promoção (foto+caption) enviada para %s", canal)
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
            except Exception as e:
                logger.error("[PROMO] Todas as tentativas falharam para %s: %s", canal, e)


# ---------------------------------------------------------------------------
# Lógica 1: Promoções AliExpress
# ---------------------------------------------------------------------------

def _montar_e_enviar_produto(produto: dict, keyword: str) -> None:
    """Monta e envia a promoção de um produto AliExpress já selecionado."""
    titulo         = produto.get("product_title", "Produto sem título")
    preco_desconto = float(produto.get("sale_price", 0))
    preco_original = float(produto.get("original_price") or produto.get("sale_price", 0))
    imagem         = produto.get("product_main_image_url", "")
    link           = produto.get("promotion_link", "")
    product_id     = str(produto.get("product_id", ""))

    link_afiliado = gerar_link_afiliado(product_id) if product_id else None
    if not link_afiliado:
        link_afiliado = link or f"https://www.aliexpress.com/wholesale?SearchText={keyword.replace(' ', '+')}"

    # Gemini gera texto puro — escapamos para HTML antes de montar o template
    copy_raw    = gemini_copy_promocao(titulo, preco_original, preco_desconto)
    copy_safe   = html_mod.escape(copy_raw)[:500]   # garante máx 500 chars, sem HTML malformado
    preco_html  = f"<s>De R$ {preco_original:.2f}</s>  →  <b>Por R$ {preco_desconto:.2f}</b>"
    texto_html  = f"{copy_safe}\n\n{preco_html}\n\n<i>#impressao3d #oferta #3dprinting</i>"
    enviar_promocao(texto_html, link_afiliado, imagem)


def _postar_promocao_fallback(keyword: str) -> None:
    """Fallback: Gemini escreve sobre a keyword e posta link de busca afiliada."""
    logger.info("[FALLBACK] Postando promoção via Gemini para keyword '%s'", keyword)
    prompt = (
        f"Aja como um maker entusiasmado numa comunidade de impressão 3D. "
        f"Escreva uma mensagem animada e curta recomendando que a galera pesquise "
        f"'{keyword}' no AliExpress agora, porque sempre tem oferta boa por lá. "
        f"Use emojis, seja muito empolgado e humano. Não invente preços específicos."
    )
    try:
        texto_gemini = _gemini.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        ).text.strip()
    except Exception as e:
        logger.error("[FALLBACK] Gemini falhou: %s", e)
        texto_gemini = (
            f"🛒 Ei, galera! Dá uma olhada nas ofertas de <b>{keyword}</b> no AliExpress! "
            f"Tá cheio de coisa boa com preço incrível. Corre lá! 🚀🖨️"
        )

    busca_url = f"https://www.aliexpress.com/wholesale?SearchText={keyword.replace(' ', '+')}"
    link_afiliado = _gerar_link_afiliado_url(busca_url) or busca_url

    texto_html = (
        f"{texto_gemini}\n\n"
        f"<i>#impressao3d #oferta #aliexpress #3dprinting</i>"
    )
    enviar_promocao(texto_html, link_afiliado, "")


def postar_promocao() -> None:
    """Busca produto no AliExpress e posta. Sempre posta algo — 3 camadas de fallback."""
    global _keyword_index

    produto_escolhido: dict | None = None
    keyword_usada = KEYWORDS[_keyword_index % len(KEYWORDS)]

    # ── Camada 1 & 2: percorre todas as keywords até achar produto ────────────
    for tentativa in range(len(KEYWORDS)):
        keyword = KEYWORDS[_keyword_index % len(KEYWORDS)]
        _keyword_index += 1
        logger.info("Buscando produtos AliExpress — keyword: '%s' (tentativa %d/%d)",
                    keyword, tentativa + 1, len(KEYWORDS))
        try:
            todos = buscar_produtos_aliexpress(keyword)
        except Exception as e:
            logger.error("Erro na API AliExpress para '%s': %s", keyword, e)
            continue

        if not todos:
            logger.warning("API não retornou nenhum produto para '%s'", keyword)
            continue

        # Tenta com filtro de preço primeiro (camada 1)
        filtrados = filtrar_por_preco(todos)
        if filtrados:
            produto_escolhido = filtrados[0]
            keyword_usada = keyword
            logger.info("Produto encontrado com filtro de preço — '%s'", keyword)
            break

        # Sem filtro: usa o produto mais barato disponível (camada 2)
        try:
            produto_escolhido = min(todos, key=lambda p: float(p.get("sale_price", 9999)))
            keyword_usada = keyword
            logger.info("Produto encontrado sem filtro de preço — '%s'", keyword)
            break
        except Exception:
            continue

    # ── Camada 3: fallback total via Gemini ───────────────────────────────────
    if not produto_escolhido:
        logger.warning("Nenhum produto encontrado em %d keywords. Usando fallback Gemini.", len(KEYWORDS))
        try:
            _postar_promocao_fallback(keyword_usada)
        except Exception as e:
            logger.error("Fallback Gemini também falhou: %s", e)
        return

    # Produto encontrado — monta e envia
    try:
        _montar_e_enviar_produto(produto_escolhido, keyword_usada)
    except Exception as e:
        logger.error("Erro ao montar promoção do produto: %s", e)
        try:
            _postar_promocao_fallback(keyword_usada)
        except Exception as e2:
            logger.error("Fallback Gemini também falhou: %s", e2)


# ---------------------------------------------------------------------------
# Lógica 2: Conteúdo Educativo
# ---------------------------------------------------------------------------

def postar_educativo() -> None:
    """Gera e posta uma dica educativa sobre impressão 3D."""
    logger.info("Gerando conteúdo educativo...")
    try:
        dica = gemini_dica_educativa()
        texto = f"💡 *Dica do Maker*\n\n{dica}\n\n_#impressao3d #dica #3dprinting_"
        enviar_mensagem(texto)
    except Exception as e:
        logger.error("Erro em postar_educativo: %s", e)


# ---------------------------------------------------------------------------
# Lógica 3: Notícias via RSS
# ---------------------------------------------------------------------------

_feed_index = 0


def postar_noticia() -> None:
    """Lê um feed RSS e posta uma notícia resumida."""
    global _feed_index
    feed_url = RSS_FEEDS[_feed_index % len(RSS_FEEDS)]
    _feed_index += 1

    logger.info("Lendo feed RSS: %s", feed_url)
    try:
        feed = feedparser.parse(feed_url)
        entradas = feed.get("entries", [])

        if not entradas:
            logger.warning("Feed vazio: %s", feed_url)
            return

        entrada = entradas[0]
        titulo  = entrada.get("title", "Sem título")
        link    = entrada.get("link", "")
        summary = entrada.get("summary", entrada.get("description", ""))

        resumo = gemini_resumir_noticia(titulo, summary)

        texto = (
            f"📰 *Novidade no Mundo 3D*\n\n"
            f"{resumo}\n\n"
            f"🔗 [Leia mais]({link})\n\n"
            f"_#impressao3d #noticias #3dprinting_"
        )
        enviar_mensagem(texto)

    except Exception as e:
        logger.error("Erro em postar_noticia: %s", e)


# ---------------------------------------------------------------------------
# Agendamento
# ---------------------------------------------------------------------------
# Distribuição de 20 posts/dia, intercalados para nunca coincidir:
#
# Promoções  (8x/dia, a cada ~3h): 00:00 03:00 06:00 09:00 12:00 15:00 18:00 21:00
# Educativo  (6x/dia, a cada ~4h): 01:00 05:00 09:00 - usamos offset de 30min
#            Na prática:           01:30 05:30 09:30 13:30 17:30 21:30
# Notícias   (6x/dia, a cada ~4h): 02:30 06:30 10:30 14:30 18:30 22:30
# ---------------------------------------------------------------------------

HORARIOS_PROMOCAO = [
    "00:00", "03:00", "06:00", "09:00",
    "12:00", "15:00", "18:00", "21:00",
]

HORARIOS_EDUCATIVO = [
    "01:30", "05:30", "09:30",
    "13:30", "17:30", "21:30",
]

HORARIOS_NOTICIAS = [
    "02:30", "06:30", "10:30",
    "14:30", "18:30", "22:30",
]


def postar_top10_canais() -> None:
    """Posta o placar do sorteio nos canais uma vez por dia (somente se houver inscritos)."""
    participantes = _carregar_participantes()
    if not participantes:
        logger.info("Top10 agendado: sem inscritos, pulando.")
        return

    cfg      = _carregar_config()
    inscritos = len(participantes)
    meta      = cfg["META_SORTEIO"]
    premio    = cfg["PREMIO_ATUAL"]
    membros   = _get_membros_grupo()
    faltam    = max(0, meta - membros) if membros >= 0 else "?"
    membros_str = str(membros) if membros >= 0 else "?"

    linhas = [f"🏆 *Sorteio Alisemtaxa — Placar Diário*\n"]
    linhas.append(f"🎁 Prêmio: *{premio}*")
    linhas.append(f"👥 Membros no grupo: {membros_str}/{meta} — faltam {faltam} para o sorteio!")
    linhas.append(f"📋 Inscritos concorrendo: {inscritos}\n")

    medalhas = ["🥇", "🥈", "🥉"]
    for i, (uid, dados) in enumerate(list(participantes.items())[:10]):
        icone    = medalhas[i] if i < 3 else f"{i+1}."
        nome     = dados.get("nome", "Anônimo")
        username = f" (@{dados['username']})" if dados.get("username") else ""
        linhas.append(f"{icone} {nome}{username}")

    if faltam > 0:
        linhas.append(f"\n📢 Faltam *{faltam}* pessoas para o sorteio!\nEnvie /participar para @AliexpressSemTaxaBot e garanta sua vaga! 🍀")
    else:
        linhas.append("\n🚀 *Meta atingida!* O sorteio acontece em breve!")

    texto = "\n".join(linhas)
    for canal in CANAIS_DESTINO:
        try:
            bot.send_message(canal, texto, parse_mode="Markdown")
            logger.info("Top10 postado em %s", canal)
        except Exception as e:
            logger.error("Erro ao postar top10 em %s: %s", canal, e)


def verificar_alerta_90pct() -> None:
    """Verifica se o canal atingiu 90% da meta e envia aviso único nos canais."""
    cfg     = _carregar_config()
    meta    = cfg.get("META_SORTEIO", 1000)
    enviado = cfg.get("ALERTA_90_ENVIADO", False)
    membros = _get_membros_grupo()

    if membros < 0:
        logger.warning("[ALERTA 90%%] Não foi possível obter membros do grupo.")
        return

    limiar = int(meta * 0.90)
    faltam = meta - membros

    # Reseta flag se membros caíram abaixo do limiar (ex: meta foi redefinida)
    if membros < limiar and enviado:
        cfg["ALERTA_90_ENVIADO"] = False
        _salvar_config(cfg)
        return

    # Meta já atingida — não envia alerta de 90%
    if membros >= meta:
        return

    # Dentro da janela de 90%~99% e ainda não enviou
    if membros >= limiar and not enviado:
        premio = cfg.get("PREMIO_ATUAL", "surpresa 🎁")
        texto = (
            f"🔥🔥🔥 *QUASE LÁ, GALERA!* 🔥🔥🔥\n\n"
            f"O canal já tem *{membros} membros* e a meta é *{meta}*!\n"
            f"Faltam só *{faltam} pessoas* para o sorteio de *{premio}* ser liberado!\n\n"
            f"📣 Chama seus amigos makers agora e garanta a participação de vocês!\n"
            f"👉 Use /participar no @AliexpressSemTaxaBot para se inscrever! 🍀"
        )
        for canal in CANAIS_DESTINO:
            try:
                bot.send_message(canal, texto, parse_mode="Markdown")
                logger.info("[ALERTA 90%%] Aviso de 90%% enviado para %s (%d/%d membros)", canal, membros, meta)
            except Exception as e:
                logger.error("[ALERTA 90%%] Erro ao enviar para %s: %s", canal, e)

        try:
            bot.send_message(
                ADMIN_ID,
                f"📊 *Alerta de meta:* {membros}/{meta} membros ({int(membros/meta*100)}%%)\n"
                f"Faltam {faltam} para liberar o sorteio. Aviso enviado nos canais.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("[ALERTA 90%%] Falha ao notificar admin: %s", e)

        cfg["ALERTA_90_ENVIADO"] = True
        _salvar_config(cfg)


def configurar_agendamentos() -> None:
    for horario in HORARIOS_PROMOCAO:
        schedule.every().day.at(horario).do(postar_promocao)
        logger.info("Promoção agendada para %s", horario)

    for horario in HORARIOS_EDUCATIVO:
        schedule.every().day.at(horario).do(postar_educativo)
        logger.info("Educativo agendado para %s", horario)

    for horario in HORARIOS_NOTICIAS:
        schedule.every().day.at(horario).do(postar_noticia)
        logger.info("Notícia agendada para %s", horario)

    schedule.every().day.at("12:00").do(postar_top10_canais)
    logger.info("Top10 do sorteio agendado para 12:00")

    schedule.every(2).hours.do(verificar_alerta_90pct)
    logger.info("Verificação de alerta 90%% agendada a cada 2 horas")


# ---------------------------------------------------------------------------
# Comandos do bot (/testar)
# ---------------------------------------------------------------------------

def _teclado_teste() -> telebot.types.InlineKeyboardMarkup:
    """Cria o teclado inline do menu de testes."""
    teclado = telebot.types.InlineKeyboardMarkup(row_width=1)
    teclado.add(
        telebot.types.InlineKeyboardButton("🛒 Promoção AliExpress",      callback_data="teste:promocao"),
        telebot.types.InlineKeyboardButton("💡 Dica Educativa",           callback_data="teste:educativo"),
        telebot.types.InlineKeyboardButton("📰 Notícia RSS",              callback_data="teste:noticia"),
        telebot.types.InlineKeyboardButton("🚀 Disparar os Três",         callback_data="teste:tudo"),
        telebot.types.InlineKeyboardButton("📊 Placar Top10 nos Canais",  callback_data="teste:top10"),
        telebot.types.InlineKeyboardButton("🎰 Testar Sorteio Completo",  callback_data="teste:sorteio"),
    )
    return teclado


def _executar_teste(nome: str, funcao) -> str:
    """Executa um módulo de teste e retorna a mensagem de resultado."""
    try:
        funcao()
        return f"✅ {nome} postado no canal com sucesso!"
    except Exception as e:
        logger.error("Erro no teste '%s': %s", nome, e)
        return f"❌ Erro em {nome}: {e}"


@bot.message_handler(commands=["testar", "start"])
def cmd_menu(message: telebot.types.Message) -> None:
    logger.info("Comando recebido: /%s (user: %s)", message.text.lstrip("/").split()[0], message.from_user.id)
    bot.send_message(
        message.chat.id,
        "🧪 Modo de Teste — escolha o que disparar agora:",
        reply_markup=_teclado_teste(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("teste:"))
def callback_teste(call: telebot.types.CallbackQuery) -> None:
    acao = call.data.split(":")[1]
    logger.info("Botão pressionado: %s (user: %s)", acao, call.from_user.id)

    bot.answer_callback_query(call.id, "⏳ Processando...")

    if acao == "promocao":
        bot.send_message(call.message.chat.id, "🛒 Buscando promoção no AliExpress, aguarde...")
        resultado = _executar_teste("Promoção", postar_promocao)

    elif acao == "educativo":
        bot.send_message(call.message.chat.id, "💡 Gerando dica com o Gemini, aguarde...")
        resultado = _executar_teste("Educativo", postar_educativo)

    elif acao == "noticia":
        bot.send_message(call.message.chat.id, "📰 Buscando notícia no RSS, aguarde...")
        resultado = _executar_teste("Noticia", postar_noticia)

    elif acao == "tudo":
        bot.send_message(call.message.chat.id, "🚀 Disparando os três módulos, aguarde...")
        resultados = []
        for nome, funcao in [("Promocao", postar_promocao), ("Educativo", postar_educativo), ("Noticia", postar_noticia)]:
            resultados.append(_executar_teste(nome, funcao))
        resultado = "\n".join(resultados)

    elif acao == "top10":
        bot.send_message(call.message.chat.id, "📊 Postando placar nos canais, aguarde...")
        resultado = _executar_teste("Placar Top10", postar_top10_canais)

    elif acao == "sorteio":
        if not _is_admin(call.from_user.id):
            resultado = "❌ Teste de sorteio exclusivo para o admin."
        else:
            resultado = _simular_sorteio_teste(
                chat_id  = call.message.chat.id,
                user_id  = call.from_user.id,
                nome     = call.from_user.first_name or "Admin",
                username = call.from_user.username or "",
            )

    else:
        resultado = "❓ Ação desconhecida."

    bot.send_message(call.message.chat.id, resultado, parse_mode="Markdown")
    bot.send_message(call.message.chat.id, "Deseja testar mais algum?", reply_markup=_teclado_teste())


# ---------------------------------------------------------------------------
# Helpers de autorização
# ---------------------------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def _is_membro_canal(user_id: int) -> bool:
    try:
        membro = bot.get_chat_member(CANAL_VERIFICACAO, user_id)
        return membro.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("Não foi possível verificar membro %s: %s", user_id, e)
        return False


def _tem_foto_perfil(user_id: int) -> bool:
    try:
        fotos = bot.get_user_profile_photos(user_id, limit=1)
        return fotos.total_count > 0
    except Exception as e:
        logger.warning("Não foi possível verificar foto de %s: %s", user_id, e)
        return False


# ---------------------------------------------------------------------------
# Módulo de Sorteio
# ---------------------------------------------------------------------------

def _sortear_vencedor(chat_id: int | None = None) -> None:
    """
    Sorteia um vencedor da lista, notifica e inicia o timer de 48h.
    chat_id: se fornecido, todas as notificações também vão para esse chat
             (útil quando chamado de um comando direto no privado).
    """
    global _timer_reivindicacao

    def _enviar(dest: int, texto: str) -> None:
        """Envia mensagem com try/except e log de erro."""
        try:
            bot.send_message(dest, texto, parse_mode="Markdown")
        except Exception as exc:
            logger.error("Falha ao enviar para %s: %s", dest, exc)

    def _log_step(passo: str) -> None:
        logger.info("[SORTEIO] %s", passo)

    _log_step("Lendo lista de participantes...")
    participantes = _carregar_participantes()
    if not participantes:
        _log_step("Nenhum participante encontrado — abortando.")
        _enviar(chat_id or ADMIN_ID, "⚠️ Nenhum participante inscrito para sortear.")
        return

    _log_step(f"{len(participantes)} participante(s) encontrado(s). Carregando config...")
    cfg = _carregar_config()

    _log_step("Escolhendo vencedor aleatório...")
    uid, dados = random.choice(list(participantes.items()))
    nome     = dados.get("nome", "Desconhecido")
    deadline = datetime.datetime.now() + datetime.timedelta(hours=48)
    _log_step(f"Vencedor escolhido: {nome} (ID {uid})")

    _log_step("Salvando vencedor pendente no config...")
    cfg["VENCEDOR_PENDENTE"] = {
        "user_id":      uid,
        "nome":         nome,
        "deadline_iso": deadline.isoformat(),
    }
    _salvar_config(cfg)

    _log_step("Montando mensagem festiva...")
    msg_vencedor = (
        "🎊🎊🎊 *ATENÇÃO, ATENÇÃO!* 🎊🎊🎊\n\n"
        "🥁🥁🥁 _Rufem os tambores, senhoras e senhores!_ 🥁🥁🥁\n\n"
        f"E o grande sortudo de hoje é... 🎤✨\n\n"
        f"🌟🌟🌟 *{nome.upper()}* 🌟🌟🌟\n\n"
        f"🏆 Parabéns! Você ganhou: *{cfg['PREMIO_ATUAL']}*\n\n"
        "📩 Você tem *48 horas* para enviar /reivindicar aqui no privado e garantir seu prêmio.\n"
        f"⏰ Prazo: {deadline.strftime('%d/%m/%Y às %H:%M')}\n\n"
        "👏👏👏 _Um aplauso ao nosso campeão!_ 👏👏👏"
    )

    msg_admin = (
        f"🎰 *Sorteio realizado!*\n\n"
        f"🌟 Vencedor: *{nome}* (ID `{uid}`)\n"
        f"🏆 Prêmio: {cfg['PREMIO_ATUAL']}\n"
        f"⏰ Prazo: 48h → {deadline.strftime('%d/%m/%Y às %H:%M')}\n\n"
        "_Aguardando /reivindicar do vencedor._"
    )

    # Notifica o vencedor em privado
    _log_step(f"Enviando mensagem festiva para o vencedor (ID {uid})...")
    _enviar(int(uid), msg_vencedor)

    # Notifica o chat de origem (onde /sortear foi digitado)
    if chat_id and int(chat_id) != int(uid):
        _log_step(f"Enviando resumo para o chat de origem ({chat_id})...")
        _enviar(chat_id, msg_vencedor)   # festiva também no chat do admin
        _enviar(chat_id, msg_admin)

    # Notifica admin (separado, caso chat_id seja diferente)
    if int(ADMIN_ID) != int(uid) and (not chat_id or int(chat_id) != int(ADMIN_ID)):
        _log_step("Enviando resumo executivo para o ADMIN_ID...")
        _enviar(ADMIN_ID, msg_admin)

    # Inicia timer de 48h
    _log_step("Iniciando timer de 48h para reivindicação...")
    if _timer_reivindicacao and _timer_reivindicacao.is_alive():
        _timer_reivindicacao.cancel()
    _timer_reivindicacao = threading.Timer(48 * 3600, _verificar_reivindicacao, args=[uid])
    _timer_reivindicacao.daemon = True
    _timer_reivindicacao.start()

    _log_step("✅ Sorteio concluído com sucesso!")


def _verificar_reivindicacao(uid_esperado: str) -> None:
    """Chamado após 48h. Se não reivindicou, sorteia outro."""
    cfg = _carregar_config()
    vencedor = cfg.get("VENCEDOR_PENDENTE")

    if vencedor and str(vencedor.get("user_id")) == str(uid_esperado):
        logger.warning("Vencedor %s não reivindicou em 48h. Sorteando novo.", uid_esperado)
        cfg["VENCEDOR_PENDENTE"] = None
        _salvar_config(cfg)

        bot.send_message(
            ADMIN_ID,
            f"⏰ O vencedor {vencedor['nome']} (ID {uid_esperado}) não reivindicou o prêmio em 48h.\n"
            "Sorteando novo vencedor automático...",
        )
        # Remove o antigo participante e sorteia de novo
        participantes = _carregar_participantes()
        participantes.pop(str(uid_esperado), None)
        _salvar_participantes(participantes)
        _sortear_vencedor()


def _simular_sorteio_teste(chat_id: int, user_id: int, nome: str, username: str) -> str:
    """
    Simulação isolada do sorteio para testes.
    - Não toca nos canais principais.
    - Não inicia timer real de 48h.
    - Envia tudo direto ao chat_id informado.
    - Formata o resultado com estilo de animador de auditório.
    """
    uid_str = str(user_id)

    # Prepara participante de teste (bypass total de verificações)
    _salvar_participantes({
        uid_str: {
            "nome":        nome,
            "username":    username,
            "inscrito_em": datetime.datetime.now().isoformat(),
        }
    })
    cfg = _carregar_config()
    cfg["META_SORTEIO"]      = 1
    cfg["PREMIO_ATUAL"]      = "🧪 Prêmio de Teste"
    cfg["VENCEDOR_PENDENTE"] = None
    _salvar_config(cfg)

    bot.send_message(
        chat_id,
        f"✅ Participante registrado: *{nome}*\n"
        "Meta: 1 | Prêmio: 🧪 Prêmio de Teste\n\n"
        "🥁 _Rufem os tambores..._",
        parse_mode="Markdown",
    )

    # Sorteia da lista (sempre vai ser o próprio admin no teste)
    participantes = _carregar_participantes()
    uid_sorteado, dados = random.choice(list(participantes.items()))
    nome_vencedor = dados.get("nome", "Desconhecido")
    premio        = cfg["PREMIO_ATUAL"]
    deadline      = datetime.datetime.now() + datetime.timedelta(hours=48)

    # Salva vencedor pendente (para /reivindicar funcionar no teste)
    cfg["VENCEDOR_PENDENTE"] = {
        "user_id":      uid_sorteado,
        "nome":         nome_vencedor,
        "deadline_iso": deadline.isoformat(),
    }
    _salvar_config(cfg)

    # Inicia timer CURTO (5 min) para o teste não deixar lixo por 48h
    global _timer_reivindicacao
    if _timer_reivindicacao and _timer_reivindicacao.is_alive():
        _timer_reivindicacao.cancel()
    _timer_reivindicacao = threading.Timer(300, _verificar_reivindicacao, args=[uid_sorteado])
    _timer_reivindicacao.daemon = True
    _timer_reivindicacao.start()

    logger.info("[TESTE] Sorteio simulado — vencedor: %s (%s)", nome_vencedor, uid_sorteado)

    # Mensagem festiva de animador de auditório (enviada direto ao chat do teste)
    bot.send_message(
        chat_id,
        "🎊🎊🎊 *ATENÇÃO, ATENÇÃO!* 🎊🎊🎊\n\n"
        "🥁🥁🥁 _Rufem os tambores, senhoras e senhores!_ 🥁🥁🥁\n\n"
        f"E o grande sortudo de hoje é... 🎤✨\n\n"
        f"🌟🌟🌟 *{nome_vencedor.upper()}* 🌟🌟🌟\n\n"
        f"🏆 Parabéns! Você ganhou: *{premio}*\n\n"
        "📩 Você tem *48 horas* para enviar /reivindicar aqui no privado.\n"
        f"⏰ Prazo: {deadline.strftime('%d/%m/%Y às %H:%M')}\n\n"
        "👏👏👏 _Um aplauso ao nosso campeão!_ 👏👏👏",
        parse_mode="Markdown",
    )

    return (
        "🎰 *Simulação concluída com sucesso!*\n\n"
        f"🏆 Vencedor sorteado: *{nome_vencedor}*\n"
        "⏱️ Timer de teste: 5 minutos (em vez de 48h)\n\n"
        "👉 Agora envie /reivindicar para testar a etapa de reivindicação.\n"
        "_Após o teste, use /resetar\\_sorteio para limpar._"
    )


@bot.message_handler(commands=["participar"])
def cmd_participar(message: telebot.types.Message) -> None:
    """Inscreve o usuário no sorteio (somente via privado)."""
    if message.chat.type != "private":
        nome = message.from_user.first_name or "amigo"
        teclado = telebot.types.InlineKeyboardMarkup()
        teclado.add(telebot.types.InlineKeyboardButton(
            "📩 Participar no Privado",
            url="https://t.me/AliexpressSemTaxaBot?start=sorteio",
        ))
        # Tenta apagar a mensagem do grupo (requer bot admin)
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        bot.send_message(
            message.chat.id,
            f"👋 *{nome}*, a inscrição no sorteio é feita no privado!\n"
            "Clique no botão abaixo para entrar:",
            parse_mode="Markdown",
            reply_markup=teclado,
        )
        return

    uid  = str(message.from_user.id)
    nome = message.from_user.first_name or "Sem nome"

    # Verifica se é membro do canal
    if not _is_membro_canal(message.from_user.id):
        bot.reply_to(
            message,
            f"❌ Para participar você precisa ser membro do canal.\n"
            f"👉 Entre aqui: t.me/gruposecretodomago e tente novamente.",
            parse_mode="Markdown",
        )
        return

    participantes = _carregar_participantes()
    if uid in participantes:
        bot.reply_to(message, "✅ Você já está inscrito no sorteio! Boa sorte! 🍀")
        return

    participantes[uid] = {
        "nome":        nome,
        "username":    message.from_user.username or "",
        "inscrito_em": datetime.datetime.now().isoformat(),
    }
    try:
        with open(PARTICIPANTS_FILE, "w", encoding="utf-8") as f:
            json.dump(participantes, f, ensure_ascii=False, indent=4)
        logger.info("Participante %s (%s) gravado em participants.json.", nome, uid)
    except Exception as e:
        logger.error("Erro CRÍTICO ao salvar participants.json: %s", e)
        bot.reply_to(
            message,
            "⚠️ Erro interno ao registrar sua inscrição. Tente novamente em instantes.",
        )
        return

    cfg = _carregar_config()
    inscritos   = len(participantes)
    meta        = cfg["META_SORTEIO"]
    premio      = cfg["PREMIO_ATUAL"]
    membros     = _get_membros_grupo()
    faltam      = max(0, meta - membros) if membros >= 0 else None
    membros_str = str(membros) if membros >= 0 else "?"

    logger.info("Novo participante: %s (%s). Inscritos: %d | Membros grupo: %s", nome, uid, inscritos, membros_str)

    if faltam is None:
        progresso = f"📊 Membros no grupo: {membros_str} / Meta: {meta}"
    elif faltam == 0:
        progresso = "🚀 Meta atingida! O sorteio será realizado em breve!"
    else:
        progresso = f"📊 Membros no grupo: {membros_str}/{meta} — faltam {faltam} para a meta!"

    bot.reply_to(
        message,
        f"🎉 *{nome}*, você está inscrito no sorteio!\n\n"
        f"🏆 Prêmio: *{premio}*\n"
        f"📋 Inscritos: {inscritos}\n"
        f"{progresso}",
        parse_mode="Markdown",
    )

    # Notifica admin quando meta de membros for atingida
    if membros >= 0 and membros >= meta:
        bot.send_message(
            ADMIN_ID,
            f"🎯 *Meta atingida!* {membros}/{meta} membros no grupo @gruposecretodomago.\n"
            f"📋 Inscritos no sorteio: {inscritos}\n"
            f"Use /sortear para realizar o sorteio.",
            parse_mode="Markdown",
        )


@bot.message_handler(commands=["reivindicar"])
def cmd_reivindicar(message: telebot.types.Message) -> None:
    """Vencedor confirma o prêmio via privado."""
    if message.chat.type != "private":
        bot.reply_to(message, "📩 Envie este comando em privado para reivindicar seu prêmio.")
        return

    cfg      = _carregar_config()
    vencedor = cfg.get("VENCEDOR_PENDENTE")

    if not vencedor:
        bot.reply_to(message, "ℹ️ Não há prêmio pendente de reivindicação no momento.")
        return

    if str(message.from_user.id) != str(vencedor["user_id"]):
        bot.reply_to(message, "❌ Você não é o vencedor atual do sorteio.")
        return

    # Cancela timer
    global _timer_reivindicacao
    if _timer_reivindicacao and _timer_reivindicacao.is_alive():
        _timer_reivindicacao.cancel()

    cfg["VENCEDOR_PENDENTE"] = None
    _salvar_config(cfg)

    nome = message.from_user.first_name or vencedor["nome"]
    logger.info("Prêmio reivindicado por %s (%s)", nome, message.from_user.id)

    bot.reply_to(
        message,
        f"🏆 Prêmio confirmado, *{nome}*! Parabéns!\n\n"
        "O admin entrará em contato para entregar o prêmio. 🎊",
        parse_mode="Markdown",
    )
    bot.send_message(
        ADMIN_ID,
        f"✅ *{nome}* (ID {message.from_user.id}) reivindicou o prêmio!\n"
        "Entre em contato para entregar.",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["sortear"])
def cmd_sortear(message: telebot.types.Message) -> None:
    """Admin realiza o sorteio manualmente."""
    if not _is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Comando exclusivo para admins.")
        return

    bot.reply_to(message, "🎰 Iniciando sorteio... acompanhe o log do terminal.")
    try:
        _sortear_vencedor(chat_id=message.chat.id)
    except Exception as exc:
        logger.error("[SORTEIO] Erro inesperado: %s", exc, exc_info=True)
        bot.send_message(
            message.chat.id,
            f"❌ *Erro no sorteio:*\n`{exc}`\n\nVeja o log completo no terminal do Replit.",
            parse_mode="Markdown",
        )


@bot.message_handler(commands=["setar_sorteio"])
def cmd_setar_sorteio(message: telebot.types.Message) -> None:
    """Admin configura meta e prêmio: /setar_sorteio 500 Impressora Bambu A1"""
    if not _is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Comando exclusivo para admins.")
        return

    partes = message.text.split(maxsplit=2)
    if len(partes) < 3:
        bot.reply_to(message, "Uso: /setar\\_sorteio [meta] [descrição do prêmio]\nEx: /setar\\_sorteio 500 Impressora Bambu A1")
        return

    try:
        meta   = int(partes[1])
        premio = partes[2].strip()
    except ValueError:
        bot.reply_to(message, "❌ A meta precisa ser um número inteiro.")
        return

    cfg = _carregar_config()
    cfg["META_SORTEIO"]       = meta
    cfg["PREMIO_ATUAL"]       = premio
    cfg["ALERTA_90_ENVIADO"]  = False   # reseta para nova meta poder disparar o aviso
    _salvar_config(cfg)

    logger.info("Admin atualizou sorteio: meta=%d, prêmio=%s", meta, premio)
    bot.reply_to(
        message,
        f"✅ Sorteio atualizado!\n🎯 Meta: *{meta}* membros no canal\n🏆 Prêmio: *{premio}*",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["ping"])
def cmd_ping(message: telebot.types.Message) -> None:
    """Verifica se o bot está online e exibe um resumo rápido de saúde."""
    agora     = datetime.datetime.now()
    uptime    = agora - _start_time
    horas     = int(uptime.total_seconds() // 3600)
    minutos   = int((uptime.total_seconds() % 3600) // 60)
    segundos  = int(uptime.total_seconds() % 60)

    cfg           = _carregar_config()
    participantes = _carregar_participantes()
    membros       = _get_membros_grupo()
    meta          = cfg["META_SORTEIO"]
    premio        = cfg["PREMIO_ATUAL"]

    membros_str = str(membros) if membros >= 0 else "indisponível"
    faltam      = max(0, meta - membros) if membros >= 0 else "?"

    sorteio_ok = membros >= 0 and membros >= meta
    sorteio_ico = "🟢" if sorteio_ok else "🔴"

    bot.reply_to(
        message,
        f"🏓 *Pong!* Bot está online e respondendo.\n\n"
        f"⏱️ *Uptime:* {horas}h {minutos}m {segundos}s\n"
        f"📅 *Iniciado em:* {_start_time.strftime('%d/%m/%Y às %H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎁 *Prêmio:* {premio}\n"
        f"👥 *Canal:* {membros_str} / {meta} membros\n"
        f"📋 *Inscritos:* {len(participantes)}\n"
        f"{sorteio_ico} *Sorteio:* {'liberado' if sorteio_ok else f'bloqueado — faltam {faltam}'}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"_Todos os sistemas operacionais._ ✅",
        parse_mode="Markdown",
    )
    logger.info("Ping respondido para user %s — uptime %dh%dm", message.from_user.id, horas, minutos)


@bot.message_handler(commands=["status"])
def cmd_status(message: telebot.types.Message) -> None:
    """Admin vê status completo do sorteio."""
    if not _is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Comando exclusivo para admins.")
        return

    cfg           = _carregar_config()
    participantes = _carregar_participantes()
    inscritos     = len(participantes)
    meta          = cfg["META_SORTEIO"]
    premio        = cfg["PREMIO_ATUAL"]
    membros       = _get_membros_grupo()
    faltam_meta   = max(0, meta - membros) if membros >= 0 else "?"
    vencedor      = cfg.get("VENCEDOR_PENDENTE")

    membros_str = str(membros) if membros >= 0 else "erro ao consultar"

    linhas = [
        f"📊 *Status do Sorteio*",
        f"🏆 Prêmio: {premio}",
        f"",
        f"👥 *Membros no Grupo:* {membros_str} / Meta: {meta}  (faltam {faltam_meta})",
        f"📋 *Inscritos no sorteio:* {inscritos}",
        "",
    ]

    if vencedor:
        deadline = datetime.datetime.fromisoformat(vencedor["deadline_iso"])
        linhas.append(f"⏳ Vencedor pendente: *{vencedor['nome']}* (ID {vencedor['user_id']})")
        linhas.append(f"⏰ Prazo: {deadline.strftime('%d/%m/%Y às %H:%M')}")
    else:
        linhas.append("🎰 Nenhum vencedor pendente.")

    if participantes:
        linhas.append("\n👤 *Últimos 10 inscritos:*")
        for uid, dados in list(participantes.items())[-10:]:
            username = f"@{dados['username']}" if dados.get("username") else "sem @"
            linhas.append(f"  • {dados['nome']} ({username})")

    bot.reply_to(message, "\n".join(linhas), parse_mode="Markdown")


@bot.message_handler(commands=["resetar_sorteio"])
def cmd_resetar_sorteio(message: telebot.types.Message) -> None:
    """Admin limpa a lista de participantes para um novo sorteio."""
    if not _is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Comando exclusivo para admins.")
        return

    global _timer_reivindicacao
    if _timer_reivindicacao and _timer_reivindicacao.is_alive():
        _timer_reivindicacao.cancel()

    _salvar_participantes({})

    cfg = _carregar_config()
    cfg["VENCEDOR_PENDENTE"] = None
    _salvar_config(cfg)

    logger.info("Admin resetou o sorteio.")
    bot.reply_to(message, "♻️ Sorteio resetado! Lista de participantes limpa. Pronto para nova rodada.")


@bot.message_handler(commands=["top10"])
def cmd_top10(message: telebot.types.Message) -> None:
    """Placar público com os primeiros inscritos no sorteio."""
    participantes = _carregar_participantes()
    cfg           = _carregar_config()
    total         = len(participantes)
    meta          = cfg["META_SORTEIO"]
    premio        = cfg["PREMIO_ATUAL"]
    membros       = _get_membros_grupo()
    faltam        = max(0, meta - membros) if membros >= 0 else max(0, meta - total)
    membros_str   = str(membros) if membros >= 0 else "?"

    if not participantes:
        bot.reply_to(
            message,
            f"📋 Ainda não há inscritos no sorteio!\n\n"
            f"🏆 Prêmio: *{premio}*\n"
            f"🎯 Meta: *{meta}* membros no canal\n\n"
            f"Seja o primeiro! Envie /participar no privado.",
            parse_mode="Markdown",
        )
        return

    linhas = [f"🏆 *Sorteio Alisemtaxa — Top Inscritos*\n"]
    linhas.append(f"🎁 Prêmio: *{premio}*")
    linhas.append(f"👥 Total no canal: {membros_str}/{meta} — faltam {faltam}")
    linhas.append(f"📋 Inscritos concorrendo: {total}\n")

    medalhas = ["🥇", "🥈", "🥉"]
    for i, (uid, dados) in enumerate(list(participantes.items())[:10]):
        icone    = medalhas[i] if i < 3 else f"{i+1}."
        nome     = dados.get("nome", "Anônimo")
        username = f" (@{dados['username']})" if dados.get("username") else ""
        linhas.append(f"{icone} {nome}{username}")

    if faltam > 0:
        linhas.append(f"\n📢 Faltam *{faltam}* pessoas no canal! Use /participar e garanta sua vaga!")
    else:
        linhas.append("\n🚀 *Meta atingida!* O sorteio acontece em breve!")

    bot.reply_to(message, "\n".join(linhas), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /anunciar — Fluxo interativo de configuração + publicação do sorteio
# ---------------------------------------------------------------------------

def _anunciar_passo_meta(message: telebot.types.Message) -> None:
    """Passo 1: recebe a nova META e pergunta o prêmio."""
    if not _is_admin(message.from_user.id):
        return

    texto = message.text.strip()
    try:
        nova_meta = int(texto)
        if nova_meta <= 0:
            raise ValueError
    except ValueError:
        msg = bot.send_message(
            message.chat.id,
            "❌ Meta inválida. Digite apenas um número inteiro maior que zero.\n"
            "Ex: `500`",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, _anunciar_passo_meta)
        return

    # Guarda meta temporariamente no chat_data via closure usando dict mutável
    _anunciar_estado[message.from_user.id] = {"meta": nova_meta}

    msg = bot.send_message(
        message.chat.id,
        f"✅ Meta: *{nova_meta}* participantes.\n\n"
        "🏆 Agora me diga qual é o *prêmio*:\n"
        "_(ex: Impressora Bambu A1, Filamento 1kg, R$100 em créditos)_",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, _anunciar_passo_premio)


def _anunciar_passo_premio(message: telebot.types.Message) -> None:
    """Passo 2: recebe o prêmio e mostra preview para confirmação."""
    if not _is_admin(message.from_user.id):
        return

    premio = message.text.strip()
    if not premio:
        msg = bot.send_message(message.chat.id, "❌ O prêmio não pode ser vazio. Tente novamente:")
        bot.register_next_step_handler(msg, _anunciar_passo_premio)
        return

    estado = _anunciar_estado.get(message.from_user.id, {})
    estado["premio"] = premio
    _anunciar_estado[message.from_user.id] = estado

    meta = estado["meta"]
    preview = _montar_anuncio(meta, premio)

    teclado = telebot.types.InlineKeyboardMarkup()
    teclado.add(
        telebot.types.InlineKeyboardButton("✅ Publicar nos canais", callback_data="anunciar:confirmar"),
        telebot.types.InlineKeyboardButton("✏️ Recomeçar", callback_data="anunciar:cancelar"),
    )

    bot.send_message(
        message.chat.id,
        f"👀 *Pré-visualização do anúncio:*\n\n{preview}\n\n"
        "Tudo certo? Escolha abaixo:",
        parse_mode="Markdown",
        reply_markup=teclado,
    )


def _montar_anuncio(meta: int, premio: str) -> str:
    """Monta o texto do anúncio do sorteio."""
    return (
        f"🎰 *SORTEIO ALISEMTAXA* 🎰\n\n"
        f"🏆 Prêmio: *{premio}*\n\n"
        f"📋 Como participar:\n"
        f"1️⃣ Seja membro do grupo\n"
        f"2️⃣ Envie /participar para @AliexpressSemTaxaBot\n\n"
        f"🎯 O sorteio acontece quando o canal atingir *{meta} membros*!\n\n"
        f"⚡ Quanto mais rápido você se inscrever, mais tempo sua participação fica válida!\n\n"
        f"Boa sorte a todos! 🍀"
    )


# Estado temporário entre passos (por user_id)
_anunciar_estado: dict = {}


@bot.message_handler(commands=["anunciar"])
def cmd_anunciar(message: telebot.types.Message) -> None:
    """Admin inicia o fluxo interativo de configuração e publicação do sorteio."""
    if not _is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Comando exclusivo para admins.")
        return

    cfg = _carregar_config()
    msg = bot.send_message(
        message.chat.id,
        f"📢 *Publicar Sorteio nos Canais*\n\n"
        f"Configuração atual:\n"
        f"• Meta: *{cfg['META_SORTEIO']}* participantes\n"
        f"• Prêmio: *{cfg['PREMIO_ATUAL']}*\n\n"
        f"1️⃣ Qual a *nova meta* de participantes?\n"
        f"_(Digite apenas o número, ex: `500`)_",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, _anunciar_passo_meta)


@bot.callback_query_handler(func=lambda call: call.data.startswith("anunciar:"))
def callback_anunciar(call: telebot.types.CallbackQuery) -> None:
    """Confirmação ou cancelamento da publicação do sorteio."""
    if not _is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Acesso negado.")
        return

    acao   = call.data.split(":")[1]
    estado = _anunciar_estado.get(call.from_user.id, {})

    if acao == "cancelar" or not estado:
        _anunciar_estado.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "Cancelado.")
        bot.edit_message_text(
            "❌ Publicação cancelada. Use /anunciar para recomeçar.",
            call.message.chat.id,
            call.message.message_id,
        )
        return

    if acao == "confirmar":
        meta   = estado.get("meta")
        premio = estado.get("premio")
        _anunciar_estado.pop(call.from_user.id, None)

        if not meta or not premio:
            bot.answer_callback_query(call.id, "Dados incompletos. Tente /anunciar novamente.")
            return

        # Salva no config.json
        cfg = _carregar_config()
        cfg["META_SORTEIO"] = meta
        cfg["PREMIO_ATUAL"]  = premio
        _salvar_config(cfg)
        _audit(call.from_user.id, "CONFIG_META", f"meta={meta} premio={premio}")

        bot.answer_callback_query(call.id, "✅ Publicando...")
        bot.edit_message_text(
            f"⏳ Publicando nos {len(CANAIS_DESTINO)} canais...",
            call.message.chat.id,
            call.message.message_id,
        )

        texto_anuncio = _montar_anuncio(meta, premio)
        teclado_canal = telebot.types.InlineKeyboardMarkup()
        teclado_canal.add(
            telebot.types.InlineKeyboardButton(
                "✋ Participar do Sorteio",
                url="https://t.me/AliexpressSemTaxaBot?start=sorteio",
            )
        )

        erros = []
        for canal in CANAIS_DESTINO:
            try:
                bot.send_message(
                    canal,
                    texto_anuncio,
                    parse_mode="Markdown",
                    reply_markup=teclado_canal,
                )
                logger.info("Anúncio de sorteio publicado em %s", canal)
            except Exception as e:
                logger.error("Erro ao publicar anúncio em %s: %s", canal, e)
                erros.append(canal)

        if erros:
            resumo = f"⚠️ Publicado com erros nos canais: {', '.join(erros)}"
        else:
            resumo = f"✅ Anúncio publicado em *{len(CANAIS_DESTINO)} canais*!\n\nMeta: {meta} | Prêmio: {premio}"

        bot.send_message(call.message.chat.id, resumo, parse_mode="Markdown")
        logger.info("Admin publicou sorteio: meta=%d, prêmio=%s", meta, premio)


# ---------------------------------------------------------------------------
# Ferramentas Maker
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["custo"])
def cmd_custo(message: telebot.types.Message) -> None:
    """/custo [horas] [gramas] [preco_kg]"""
    partes = message.text.split()
    if len(partes) != 4:
        bot.reply_to(
            message,
            "📐 *Calculadora de Custo de Impressão*\n\n"
            "Uso: /custo \\[horas\\] \\[gramas\\] \\[preco\\_kg\\]\n"
            "Ex: `/custo 4 85 120`\n\n"
            "_horas = tempo de impressão | gramas = peso da peça | preco\\_kg = R$ por kg de filamento_",
            parse_mode="MarkdownV2",
        )
        return

    try:
        horas    = float(partes[1])
        gramas   = float(partes[2])
        preco_kg = float(partes[3])
    except ValueError:
        bot.reply_to(message, "❌ Use apenas números. Ex: `/custo 4 85 120`", parse_mode="Markdown")
        return

    custo_material  = (gramas / 1000) * preco_kg
    custo_energia   = horas * 0.30           # ~0,30 kWh médio de impressora 3D
    margem_energia  = custo_energia * 0.10   # 10% de margem
    custo_total     = custo_material + custo_energia + margem_energia
    custo_sugerido  = custo_total * 2        # sugestão de venda (2x)

    bot.reply_to(
        message,
        f"📐 *Custo de Impressão*\n\n"
        f"🧵 Material ({gramas}g de filamento): R$ {custo_material:.2f}\n"
        f"⚡ Energia ({horas}h): R$ {custo_energia:.2f}\n"
        f"📈 Margem energia (10%): R$ {margem_energia:.2f}\n"
        f"──────────────────\n"
        f"💰 *Custo total: R$ {custo_total:.2f}*\n"
        f"🏷️ Preço sugerido de venda (2×): R$ {custo_sugerido:.2f}",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /ajuda — Menu de tópicos técnicos com Gemini
# ---------------------------------------------------------------------------

_TOPICOS_AJUDA = {
    "mesa":      "Mesa não gruda",
    "zwobble":   "Z-Wobble / Linhas tortas",
    "suporte":   "Configuração de Suporte",
    "retract":   "Retração e Stringing",
    "resina":    "Cura e Tempo de Exposição (Resina)",
    "filamento": "Como armazenar Filamento",
}


def _teclado_ajuda() -> telebot.types.InlineKeyboardMarkup:
    teclado = telebot.types.InlineKeyboardMarkup(row_width=2)
    botoes  = [
        telebot.types.InlineKeyboardButton(texto, callback_data=f"ajuda:{chave}")
        for chave, texto in _TOPICOS_AJUDA.items()
    ]
    teclado.add(*botoes)
    return teclado


@bot.message_handler(commands=["ajuda"])
def cmd_ajuda(message: telebot.types.Message) -> None:
    logger.info("Comando /ajuda (user: %s)", message.from_user.id)
    bot.send_message(
        message.chat.id,
        "🛠️ *Central de Ajuda para Makers*\n\nEscolha o tópico que está com dificuldade:",
        parse_mode="Markdown",
        reply_markup=_teclado_ajuda(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("ajuda:"))
def callback_ajuda(call: telebot.types.CallbackQuery) -> None:
    chave  = call.data.split(":")[1]
    topico = _TOPICOS_AJUDA.get(chave, chave)

    bot.answer_callback_query(call.id, f"🔍 Consultando Gemini sobre: {topico}...")
    bot.send_message(call.message.chat.id, f"⏳ Gerando resposta sobre *{topico}*...", parse_mode="Markdown")

    try:
        prompt = (
            f"Você é um maker experiente e professor didático de impressão 3D. "
            f"Explique de forma clara, amigável e com emojis como resolver o problema: '{topico}'. "
            f"Use linguagem simples, dê passos práticos e termine com uma dica extra. "
            f"Seja detalhado mas não prolixo."
        )
        resposta = _gemini.models.generate_content(model="gemini-2.5-flash", contents=prompt).text.strip()
        bot.send_message(
            call.message.chat.id,
            f"🛠️ *{topico}*\n\n{resposta}\n\n_Precisa de mais ajuda? Use /ajuda_",
            parse_mode="Markdown",
            reply_markup=_teclado_ajuda(),
        )
        logger.info("Ajuda Gemini entregue para tópico '%s'", topico)
    except Exception as e:
        logger.error("Erro no callback de ajuda '%s': %s", chave, e)
        bot.send_message(call.message.chat.id, f"❌ Erro ao consultar o Gemini: {e}")


# ---------------------------------------------------------------------------
# Boas-vindas automáticas para novos membros
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=["new_chat_members"])
def cmd_boas_vindas(message: telebot.types.Message) -> None:
    """Envia boas-vindas a cada novo membro que entrar no grupo."""
    cfg    = _carregar_config()
    premio = cfg.get("PREMIO_ATUAL", "em breve!")
    meta   = cfg.get("META_SORTEIO", 1000)

    for novo in message.new_chat_members:
        if novo.is_bot:
            continue

        nome = novo.first_name or "Membro"
        logger.info("Novo membro: %s (%s) no chat %s", nome, novo.id, message.chat.id)

        teclado = telebot.types.InlineKeyboardMarkup()
        teclado.add(
            telebot.types.InlineKeyboardButton(
                "🎰 Participar do Sorteio",
                url="https://t.me/AliexpressSemTaxaBot?start=sorteio",
            )
        )

        try:
            bot.send_message(
                message.chat.id,
                f"👋 Bem-vindo(a), *{nome}*! Que bom ter você aqui! 🎉\n\n"
                f"Você entrou na comunidade certa — aqui a gente compartilha as *melhores ofertas* "
                f"do AliExpress sem taxas e dicas de impressão 3D. 🖨️\n\n"
                f"🏆 Temos um sorteio rolando!\n"
                f"Prêmio atual: *{premio}*\n"
                f"Meta: *{meta}* participantes\n\n"
                f"👇 Clique abaixo para se inscrever no sorteio agora: no privado digite o comando /participar",
                parse_mode="Markdown",
                reply_markup=teclado,
            )
        except Exception as e:
            logger.error("Erro ao enviar boas-vindas para %s: %s", nome, e)


# ---------------------------------------------------------------------------
# /menu — Central de Controle (admin only)
# ---------------------------------------------------------------------------

def _teclado_menu() -> telebot.types.InlineKeyboardMarkup:
    teclado = telebot.types.InlineKeyboardMarkup(row_width=2)
    teclado.add(
        telebot.types.InlineKeyboardButton("🎰 Iniciar Sorteio",    callback_data="menu:sortear"),
        telebot.types.InlineKeyboardButton("📊 Status do Bot",       callback_data="menu:status"),
        telebot.types.InlineKeyboardButton("⚙️ Configurar Meta",    callback_data="menu:config_meta"),
        telebot.types.InlineKeyboardButton("🛒 Forçar Promoção",    callback_data="menu:promocao"),
        telebot.types.InlineKeyboardButton("🛠️ Ferramentas Maker",  callback_data="menu:maker"),
        telebot.types.InlineKeyboardButton("📋 Ver Inscritos",       callback_data="menu:inscritos"),
        telebot.types.InlineKeyboardButton("🏓 Ping / Saúde",        callback_data="menu:ping"),
        telebot.types.InlineKeyboardButton("🔄 Resetar Sorteio",    callback_data="menu:resetar"),
        telebot.types.InlineKeyboardButton("❌ Fechar Menu",         callback_data="menu:fechar"),
    )
    return teclado


def _teclado_maker_menu() -> telebot.types.InlineKeyboardMarkup:
    teclado = telebot.types.InlineKeyboardMarkup(row_width=1)
    teclado.add(
        telebot.types.InlineKeyboardButton("📐 Calculadora de Custo",  callback_data="menu:maker_custo"),
        telebot.types.InlineKeyboardButton("🛠️ Ajuda Técnica Gemini", callback_data="menu:maker_ajuda"),
        telebot.types.InlineKeyboardButton("⬅️ Voltar ao Menu",        callback_data="menu:voltar"),
    )
    return teclado


@bot.message_handler(commands=["menu"])
def cmd_menu_controle(message: telebot.types.Message) -> None:
    if not _is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Menu exclusivo para o admin.")
        return
    logger.info("Admin abriu /menu (chat: %s)", message.chat.id)
    bot.send_message(
        message.chat.id,
        "🛠️ *Central de Controle Mago 3D*\nEscolha uma operação:",
        parse_mode="Markdown",
        reply_markup=_teclado_menu(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("menu:"))
def callback_menu(call: telebot.types.CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Acesso negado.")
        return

    acao = call.data.split(":")[1]
    logger.info("[MENU] Ação: %s (user: %s)", acao, call.from_user.id)

    # ── Fechar ──────────────────────────────────────────────────────────────
    if acao == "fechar":
        bot.answer_callback_query(call.id, "Menu fechado.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            bot.edit_message_text("_Menu fechado._", call.message.chat.id,
                                  call.message.message_id, parse_mode="Markdown")
        return

    # ── Voltar ao menu principal ─────────────────────────────────────────────
    if acao == "voltar":
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "🛠️ *Central de Controle Mago 3D*\nEscolha uma operação:",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=_teclado_menu(),
        )
        return

    # ── Iniciar Sorteio ──────────────────────────────────────────────────────
    if acao == "sortear":
        cfg     = _carregar_config()
        meta    = cfg["META_SORTEIO"]
        membros = _get_membros_grupo()

        # Bloqueia se a meta de membros do canal ainda não foi atingida
        if membros >= 0 and membros < meta:
            bot.answer_callback_query(call.id, f"❌ Meta não atingida: {membros}/{meta}")
            bot.send_message(
                call.message.chat.id,
                f"❌ *Meta não atingida!*\n\n"
                f"👥 Total no canal: *{membros}* / Meta: *{meta}*\n"
                f"Faltam *{meta - membros}* membros para liberar o sorteio.\n\n"
                f"_O vencedor será sorteado apenas entre os inscritos no /participar._",
                parse_mode="Markdown",
            )
            return

        participantes = _carregar_participantes()
        if not participantes:
            bot.answer_callback_query(call.id, "⚠️ Sem participantes inscritos!")
            bot.send_message(call.message.chat.id,
                "⚠️ Não há participantes inscritos.\n"
                "Use /anunciar para divulgar o sorteio primeiro.")
            return
        bot.answer_callback_query(call.id, "🎰 Sorteando...")
        bot.send_message(call.message.chat.id, "🎰 Iniciando sorteio...")
        try:
            _sortear_vencedor(chat_id=call.message.chat.id)
            _audit(call.from_user.id, "SORTEAR", f"inscritos={len(participantes)}")
        except Exception as exc:
            logger.error("[MENU:sortear] %s", exc, exc_info=True)
            bot.send_message(call.message.chat.id,
                f"❌ *Erro no sorteio:*\n`{exc}`", parse_mode="Markdown")
        return

    # ── Status do Bot ────────────────────────────────────────────────────────
    if acao == "status":
        bot.answer_callback_query(call.id, "📊 Carregando status...")
        cfg           = _carregar_config()
        participantes = _carregar_participantes()
        inscritos     = len(participantes)
        meta          = cfg["META_SORTEIO"]
        premio        = cfg["PREMIO_ATUAL"]
        membros       = _get_membros_grupo()
        faltam_meta   = max(0, meta - membros) if membros >= 0 else "?"
        vencedor      = cfg.get("VENCEDOR_PENDENTE")

        membros_str = str(membros) if membros >= 0 else "erro ao consultar"

        linhas = [
            "📊 *Status do Bot — Mago 3D*\n",
            f"🏆 Prêmio: {premio}",
            f"",
            f"👥 *Membros no Grupo:* {membros_str} / Meta: {meta}  (faltam {faltam_meta})",
            f"📋 *Inscritos no sorteio:* {inscritos}",
        ]
        if vencedor:
            dl = datetime.datetime.fromisoformat(vencedor["deadline_iso"])
            linhas.append(f"\n⏳ Vencedor pendente: *{vencedor['nome']}* até {dl.strftime('%d/%m %H:%M')}")
        else:
            linhas.append("\n🎰 Sem vencedor pendente.")

        if participantes:
            linhas.append(f"\n👤 Últimos 5 inscritos:")
            for uid, d in list(participantes.items())[-5:]:
                user = f"@{d['username']}" if d.get("username") else "sem @"
                linhas.append(f"  • {d['nome']} ({user})")

        bot.send_message(call.message.chat.id, "\n".join(linhas),
                         parse_mode="Markdown", reply_markup=_teclado_menu())
        return

    # ── Configurar Meta ──────────────────────────────────────────────────────
    if acao == "config_meta":
        bot.answer_callback_query(call.id)
        cfg = _carregar_config()
        msg = bot.send_message(
            call.message.chat.id,
            f"⚙️ *Configurar Sorteio*\n\n"
            f"Configuração atual:\n"
            f"• Meta: *{cfg['META_SORTEIO']}* participantes\n"
            f"• Prêmio: *{cfg['PREMIO_ATUAL']}*\n\n"
            f"1️⃣ Qual a *nova meta*? _(só o número, ex: `500`)_",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, _anunciar_passo_meta)
        return

    # ── Forçar Promoção ──────────────────────────────────────────────────────
    if acao == "promocao":
        bot.answer_callback_query(call.id, "🛒 Buscando promoção...")
        bot.send_message(call.message.chat.id,
            "🛒 Buscando oferta no AliExpress, aguarde...")
        try:
            postar_promocao()
            _audit(call.from_user.id, "FORCAR_PROMOCAO")
            bot.send_message(call.message.chat.id,
                "✅ Promoção disparada nos canais!", reply_markup=_teclado_menu())
        except Exception as exc:
            logger.error("[MENU:promocao] %s", exc, exc_info=True)
            bot.send_message(call.message.chat.id,
                f"❌ *Erro na promoção:*\n`{exc}`", parse_mode="Markdown")
        return

    # ── Ferramentas Maker ────────────────────────────────────────────────────
    if acao == "maker":
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "🛠️ *Ferramentas Maker*\nEscolha uma ferramenta:",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=_teclado_maker_menu(),
        )
        return

    if acao == "maker_custo":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "📐 *Calculadora de Custo de Impressão*\n\n"
            "Envie o comando com os valores:\n"
            "`/custo [horas] [gramas] [preco_kg]`\n\n"
            "Ex: `/custo 4 85 120`\n"
            "_horas = tempo | gramas = peso | preco\\_kg = R$ por kg_",
            parse_mode="Markdown",
            reply_markup=_teclado_maker_menu(),
        )
        return

    if acao == "maker_ajuda":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "🛠️ *Central de Ajuda para Makers*\n\nEscolha o tópico:",
            parse_mode="Markdown",
            reply_markup=_teclado_ajuda(),
        )
        return

    # ── Ver Inscritos ────────────────────────────────────────────────────────
    if acao == "inscritos":
        bot.answer_callback_query(call.id, "📋 Carregando lista...")
        participantes = _carregar_participantes()
        cfg    = _carregar_config()
        total  = len(participantes)
        meta   = cfg["META_SORTEIO"]
        premio = cfg["PREMIO_ATUAL"]

        if not participantes:
            bot.send_message(call.message.chat.id,
                f"📋 *Inscritos no Sorteio*\n\n"
                f"Nenhum participante ainda.\n"
                f"🏆 Prêmio: {premio} | 🎯 Meta: {meta}",
                parse_mode="Markdown", reply_markup=_teclado_menu())
            return

        # Monta lista completa em blocos de 30 (limite seguro do Telegram)
        linhas_header = [
            f"📋 *Lista de Inscritos — {total}/{meta}*",
            f"🏆 Prêmio: {premio}\n",
        ]
        bloco  = list(linhas_header)
        blocos = []
        for i, (uid, dados) in enumerate(participantes.items(), 1):
            username = f" @{dados['username']}" if dados.get("username") else ""
            inscrito = dados.get("inscrito_em", "")[:10]
            linha    = f"{i}. {dados['nome']}{username} _(ID {uid} — {inscrito})_"
            bloco.append(linha)
            if len(bloco) >= 32:          # flush a cada 30 entradas
                blocos.append("\n".join(bloco))
                bloco = []
        if bloco:
            blocos.append("\n".join(bloco))

        for parte in blocos:
            try:
                bot.send_message(call.message.chat.id, parte,
                                 parse_mode="Markdown")
            except Exception as exc:
                logger.error("Erro ao enviar bloco de inscritos: %s", exc)

        bot.send_message(call.message.chat.id,
            f"✅ Total: *{total}* inscritos | Faltam *{max(0, meta-total)}* para a meta.",
            parse_mode="Markdown", reply_markup=_teclado_menu())
        return

    # ── Resetar Sorteio ──────────────────────────────────────────────────────
    if acao == "resetar":
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Acesso negado — apenas o admin pode resetar.")
            return

        bot.answer_callback_query(call.id, "⏳ Resetando...")

        global _timer_reivindicacao
        if _timer_reivindicacao and _timer_reivindicacao.is_alive():
            _timer_reivindicacao.cancel()
            _timer_reivindicacao = None

        try:
            with open(PARTICIPANTS_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=4)
            logger.info("[RESET] participants.json limpo com sucesso pelo admin %s.", call.from_user.id)
            _audit(call.from_user.id, "RESETAR_SORTEIO", "participants.json zerado")
        except Exception as e:
            logger.error("[RESET] Falha ao limpar participants.json: %s", e)
            bot.send_message(
                call.message.chat.id,
                f"❌ *Erro ao resetar o sorteio:*\n`{e}`",
                parse_mode="Markdown",
            )
            return

        cfg = _carregar_config()
        cfg["VENCEDOR_PENDENTE"] = None
        _salvar_config(cfg)

        bot.send_message(
            call.message.chat.id,
            "♻️ *Sorteio resetado com sucesso!*\n\n"
            "✅ `participants.json` limpo — dicionário vazio gravado.\n"
            "✅ Vencedor pendente removido do `config.json`.\n"
            "✅ Timer de reivindicação cancelado.\n\n"
            "_Pronto para uma nova rodada!_",
            parse_mode="Markdown",
            reply_markup=_teclado_menu(),
        )
        return

    # ── Ping / Saúde ─────────────────────────────────────────────────────────
    if acao == "ping":
        bot.answer_callback_query(call.id, "🏓 Verificando saúde...")
        agora    = datetime.datetime.now()
        uptime   = agora - _start_time
        horas    = int(uptime.total_seconds() // 3600)
        minutos  = int((uptime.total_seconds() % 3600) // 60)
        segundos = int(uptime.total_seconds() % 60)

        cfg           = _carregar_config()
        participantes = _carregar_participantes()
        membros       = _get_membros_grupo()
        meta          = cfg["META_SORTEIO"]
        premio        = cfg["PREMIO_ATUAL"]

        membros_str = str(membros) if membros >= 0 else "indisponível"
        faltam      = max(0, meta - membros) if membros >= 0 else "?"
        sorteio_ok  = membros >= 0 and membros >= meta
        sorteio_ico = "🟢" if sorteio_ok else "🔴"

        bot.send_message(
            call.message.chat.id,
            f"🏓 *Pong!* Bot está online e respondendo.\n\n"
            f"⏱️ *Uptime:* {horas}h {minutos}m {segundos}s\n"
            f"📅 *Iniciado em:* {_start_time.strftime('%d/%m/%Y às %H:%M')}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🎁 *Prêmio:* {premio}\n"
            f"👥 *Canal:* {membros_str} / {meta} membros\n"
            f"📋 *Inscritos:* {len(participantes)}\n"
            f"{sorteio_ico} *Sorteio:* {'liberado' if sorteio_ok else f'bloqueado — faltam {faltam}'}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"_Todos os sistemas operacionais._ ✅",
            parse_mode="Markdown",
            reply_markup=_teclado_menu(),
        )
        return

    bot.answer_callback_query(call.id, "❓ Ação desconhecida.")


# ---------------------------------------------------------------------------
# Loop de agendamento (thread secundária)
# ---------------------------------------------------------------------------

def _loop_agendamento() -> None:
    while True:
        schedule.run_pending()
        time.sleep(30)


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("🤖 Bot de Impressão 3D iniciando...")

    
    # Inicia o servidor Flask em uma thread separada para a Render não desligar o bot
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Servidor de monitoramento ativo na porta 10000")

    # Remove webhook e aguarda conexões anteriores expirarem no lado do Telegram
    for tentativa in range(1, 6):
        try:
            bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook removido com sucesso.")
            break
        except Exception as e:
            logger.warning("Tentativa %d — falha ao remover webhook: %s", tentativa, e)
            time.sleep(5)

    # Aguarda a conexão de getUpdates anterior expirar no Telegram (até ~35 s)
    logger.info("Aguardando 10 s para garantir que não há outra instância ativa...")
    time.sleep(10)

    configurar_agendamentos()

    agendamento_thread = threading.Thread(target=_loop_agendamento, daemon=True)
    agendamento_thread.start()

    logger.info("✅ Agendamentos e polling de comandos ativos!")

    # Loop de polling com recuperação automática de erro 409
    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=20,
                skip_pending=True,
                logger_level=logging.WARNING,
            )
        except Exception as exc:
            if "409" in str(exc):
                logger.warning(
                    "Conflito 409 detectado (outra instância ainda ativa). "
                    "Aguardando 30 s antes de tentar novamente..."
                )
                time.sleep(30)
            else:
                logger.error("Erro inesperado no polling: %s — reiniciando em 10 s.", exc)
                time.sleep(10)


if __name__ == "__main__":
    main()
