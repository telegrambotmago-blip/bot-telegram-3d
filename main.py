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
from google import genai
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
# Variáveis de ambiente (configuradas nos Secrets do Replit)
# CORREÇÃO: Usar .get() com valores padrão para evitar KeyError
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
ALI_APP_KEY      = os.getenv("ALI_APP_KEY", "")
ALI_APP_SECRET   = os.getenv("ALI_APP_SECRET", "")
ALI_TRACKING_ID  = os.getenv("ALI_TRACKING_ID", "")

# Validação de variáveis críticas
if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN não foi configurado! O bot não pode iniciar.")
    raise ValueError("TELEGRAM_TOKEN é obrigatório")

if not GEMINI_API_KEY:
    logger.warning("⚠️ GEMINI_API_KEY não foi configurado. Algumas funcionalidades não funcionarão.")

# ---------------------------------------------------------------------------
# Canais de destino e constantes de admin
# ---------------------------------------------------------------------------
CANAIS_DESTINO    = ["@gruposecretodomago", "@AchadosSemImposto"]
ADMIN_ID          = 1166455103
CANAL_VERIFICACAO = "@gruposecretodomago"
GRUPO_META_ID     = "@gruposecretodomago"

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE       = os.path.join(BASE_DIR, "config.json")
PARTICIPANTS_FILE = os.path.join(BASE_DIR, "participants.json")
AUDIT_FILE        = os.path.join(BASE_DIR, "audit.log")

# ---------------------------------------------------------------------------
# CORREÇÃO: Adicionar Lock para sincronização de acesso aos arquivos JSON
# Isso previne race conditions quando múltiplas threads acessam os arquivos
# ---------------------------------------------------------------------------
_config_lock = threading.RLock()
_participants_lock = threading.RLock()
_audit_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Gestão de configuração e participantes (persistência em JSON)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "META_SORTEIO":       1000,
    "PREMIO_ATUAL":       "Sem prêmio definido",
    "VENCEDOR_PENDENTE":  None,
    "ALERTA_90_ENVIADO":  False,
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


def _load_config() -> dict:
    """Carrega config.json com sincronização de thread."""
    with _config_lock:
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error("Erro ao carregar config.json: %s", e)
        return _DEFAULT_CONFIG.copy()


def _save_config(config: dict) -> None:
    """Salva config.json com sincronização de thread."""
    with _config_lock:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.debug("config.json salvo com sucesso")
        except Exception as e:
            logger.error("Erro ao salvar config.json: %s", e)


def _load_participants() -> dict:
    """Carrega participants.json com sincronização de thread."""
    with _participants_lock:
        try:
            if os.path.exists(PARTICIPANTS_FILE):
                with open(PARTICIPANTS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error("Erro ao carregar participants.json: %s", e)
        return {}


def _save_participants(participants: dict) -> None:
    """Salva participants.json com sincronização de thread."""
    with _participants_lock:
        try:
            with open(PARTICIPANTS_FILE, "w", encoding="utf-8") as f:
                json.dump(participants, f, indent=2, ensure_ascii=False)
            logger.debug("participants.json salvo com sucesso")
        except Exception as e:
            logger.error("Erro ao salvar participants.json: %s", e)


def _audit_log(mensagem: str) -> None:
    """Registra uma mensagem no audit.log com sincronização de thread."""
    with _audit_lock:
        try:
            timestamp = datetime.datetime.now().isoformat()
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {mensagem}\n")
        except Exception as e:
            logger.error("Erro ao escrever em audit.log: %s", e)


# ---------------------------------------------------------------------------
# Inicialização do bot e Flask
# ---------------------------------------------------------------------------
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Health check para Render
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET", "HEAD"])
def health_check():
    """Health check para Render (evita que o serviço seja marcado como inativo)."""
    return "OK", 200


# ---------------------------------------------------------------------------
# Keywords e constantes de preço
# ---------------------------------------------------------------------------
KEYWORDS = [
    "3d",
    "3d resin",
    "fdm",
    "3d printer",
    "resin",
    "saturn 4 ultra",
    "mars 5 ultra",
    "bambulab",
    "photon p1",
    "Kobra X",
    "Kobra 4",
    "Century Carbon",
    "Creality",
    "Elegoor",
    "Anycubicr",
    "Sunlu",
    "Jayo",       
]
_keyword_index = 0

# Preços em centavos de real (USD $20 ≈ R$ 75 = 7500 centavos)
# Filtramos apenas produtos 3D de qualidade (filamentos, resinas, impressoras, peças)
PRECO_MIN = 7500      # R$ 75 / $20 USD (mínimo)
PRECO_MAX = 500000000 # R$ 5.000.000 (sem limite prático)

# ---------------------------------------------------------------------------
# Feeds RSS de impressão 3D
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    "https://all3dp.com/feed/",
    "https://3dprinting.com/feed/",
    "https://www.3dnatives.com/en/feed/",
]

# ---------------------------------------------------------------------------
# API AliExpress
# ---------------------------------------------------------------------------

def _ali_request(metodo: str, parametros: dict) -> dict:
    """Faz uma requisição à API do AliExpress."""
    try:
        url = "https://api-gw.oneplus.com/gw/trade/normal/achieve"
        timestamp = str(int(time.time() * 1000))
        
        payload = {
            "app_key": ALI_APP_KEY,
            "method": metodo,
            "timestamp": timestamp,
            "format": "json",
            "v": "2.0",
            "sign_type": "MD5",
            **parametros,
        }
        
        # Gerar assinatura MD5 (formato correto: secret + parametros + secret)
        # Ordenar os parâmetros e concatenar key=value&key=value...
        sorted_params = sorted(payload.items())
        sign_string = ALI_APP_SECRET + "".join([f"{k}{v}" for k, v in sorted_params]) + ALI_APP_SECRET
        payload["sign"] = hashlib.md5(sign_string.encode()).hexdigest().upper()
        
        logger.debug(f"API Request: {metodo} | Timestamp: {timestamp}")
        
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # Verificar se houve erro na resposta
        if "error_response" in data:
            error_msg = data.get("error_response", {}).get("msg", "Erro desconhecido")
            logger.error(f"Erro da API AliExpress: {error_msg}")
            return {}
        
        return data
    except Exception as e:
        logger.error("Erro na API AliExpress (%s): %s", metodo, e)
        return {}


def buscar_produtos_aliexpress(keyword: str) -> list[dict]:
    """Busca produtos no AliExpress por keyword."""
    try:
        data = _ali_request(
            "aliexpress.affiliate.productlist.get",
            {
                "app_signature": ALI_APP_SECRET,
                "fields": "product_id,product_title,product_main_image_url,original_price,sale_price,product_url",
                "keywords": keyword,
                "page_no": "1",
                "page_size": "50",
                "sort": "SALE_PRICE_ASC",
                "tracking_id": ALI_TRACKING_ID,
            },
        )
        produtos = (
            data.get("aliexpress_affiliate_productlist_get_response", {})
            .get("resp_result", {})
            .get("result", {})
            .get("products", {})
            .get("product", [])
        )
        return produtos
    except Exception as e:
        logger.error("Erro ao buscar produtos: %s", e)
        return []


def filtrar_por_preco(produtos: list[dict]) -> list[dict]:
    """
    Filtra produtos dentro da faixa de preço desejada e por categorias 3D.
    
    Critérios:
    - Preço entre PRECO_MIN ($20 USD / R$ 75) e PRECO_MAX
    - Produto deve ser de categorias 3D (filamentos, resinas, impressoras, peças)
    """
    # Palavras-chave que indicam produtos 3D de qualidade
    CATEGORIAS_3D = [
        "filament", "filamento", "pla", "abs", "petg", "nylon",
        "resin", "resina", "uv", "epoxy",
        "printer", "impressora", "creality", "elegoo", "anycubic", "bambu", "sunlu",
        "nozzle", "bico", "hotend", "extruder", "extrusora",
        "build plate", "plataforma", "bed", "cama",
        "parts", "peças", "component", "componente",
        "fdm", "sla", "dlp", "lcd",
    ]
    
    filtrados = []
    for p in produtos:
        try:
            preco = float(p.get("sale_price", 0))
            
            # Verifica faixa de preço
            if not (PRECO_MIN <= preco <= PRECO_MAX):
                continue
            
            # Verifica se é produto 3D (por nome ou descrição)
            titulo = str(p.get("product_title", "")).lower()
            descricao = str(p.get("description", "")).lower()
            texto_completo = f"{titulo} {descricao}"
            
            # Verifica se contém palavras-chave de categorias 3D
            eh_categoria_3d = any(palavra in texto_completo for palavra in CATEGORIAS_3D)
            
            if eh_categoria_3d:
                filtrados.append(p)
                logger.debug(f"✅ Produto 3D filtrado: {titulo[:50]} - R$ {preco/100:.2f}")
        except (ValueError, TypeError) as e:
            logger.debug(f"Erro ao filtrar produto: {e}")
            continue
    
    logger.info(f"Filtrados {len(filtrados)} produtos 3D de {len(produtos)} totais")
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
# Geração de conteúdo com Gemini
# ---------------------------------------------------------------------------

def gemini_copy_promocao(titulo: str, preco_original: float, preco_desconto: float) -> str:
    """Gera um copy atrativo para a promoção usando Gemini."""
    if not GEMINI_API_KEY:
        # Fallback sem Gemini
        desconto_pct = ((preco_original - preco_desconto) / preco_original * 100) if preco_original > 0 else 0
        return f"🔥 *{titulo}*\n💥 Desconto de {desconto_pct:.0f}%!"
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            f"Crie um copy curto e atrativo (máximo 2 linhas) para esta promoção de produto 3D:\n"
            f"Produto: {titulo}\n"
            f"Preço original: R$ {preco_original:.2f}\n"
            f"Preço com desconto: R$ {preco_desconto:.2f}\n"
            f"Use emojis e seja persuasivo. Responda apenas com o copy, sem explicações."
        )
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        logger.warning("Erro ao gerar copy com Gemini: %s", e)
        desconto_pct = ((preco_original - preco_desconto) / preco_original * 100) if preco_original > 0 else 0
        return f"🔥 *{titulo}*\n💥 Desconto de {desconto_pct:.0f}%!"


def gemini_dica_educativa() -> str:
    """Gera uma dica educativa sobre impressão 3D usando Gemini."""
    if not GEMINI_API_KEY:
        return "Dica: Sempre calibre sua impressora 3D antes de começar!"
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            "Gere uma dica educativa curta e prática sobre impressão 3D (máximo 3 linhas). "
            "Seja específico e útil. Responda apenas com a dica, sem explicações."
        )
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        logger.warning("Erro ao gerar dica com Gemini: %s", e)
        return "Dica: Sempre calibre sua impressora 3D antes de começar!"


# ---------------------------------------------------------------------------
# Envio de mensagens
# ---------------------------------------------------------------------------

def enviar_mensagem(texto: str) -> None:
    """Envia uma mensagem de texto para todos os canais de destino."""
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
                logger.error("Falha ao enviar texto para %s: %s", canal, e2)


# ---------------------------------------------------------------------------
# Lógica 1: Promoções AliExpress
# ---------------------------------------------------------------------------

def _montar_e_enviar_produto(produto: dict, keyword: str) -> None:
    """Monta e envia a promoção do produto para os canais."""
    try:
        titulo = produto.get("product_title", "Produto sem título")
        preco_original = float(produto.get("original_price", 0))
        preco_desconto = float(produto.get("sale_price", 0))
        foto = produto.get("product_main_image_url", "")
        product_id = produto.get("product_id", "")

        copy = gemini_copy_promocao(titulo, preco_original, preco_desconto)
        link = gerar_link_afiliado(product_id)

        if not link:
            link = f"https://www.aliexpress.com/item/{product_id}.html"

        texto = (
            f"{copy}\n\n"
            f"💰 De: R$ {preco_original:.2f}\n"
            f"🔥 Por: R$ {preco_desconto:.2f}\n"
            f"🔗 [Comprar no AliExpress]({link})\n\n"
            f"_#impressao3d #aliexpress #oferta_"
        )

        if foto:
            enviar_mensagem_com_foto(texto, foto)
        else:
            enviar_mensagem(texto)

        logger.info("Promoção enviada: %s", titulo)
    except Exception as e:
        logger.error("Erro ao montar promoção: %s", e)


def _postar_promocao_fallback(keyword: str) -> None:
    """Fallback: tenta novamente com todas as keywords ate encontrar um produto."""
    logger.warning("Fallback ativado: tentando novamente com todas as keywords...")
    
    # Tenta novamente com TODAS as keywords (sem limite de tentativas)
    for keyword_retry in KEYWORDS:
        try:
            todos = buscar_produtos_aliexpress(keyword_retry)
            if todos:
                # Tenta com filtro de preco
                filtrados = filtrar_por_preco(todos)
                if filtrados:
                    produto = filtrados[0]
                    logger.info("✅ Produto encontrado no fallback: %s", keyword_retry)
                    _montar_e_enviar_produto(produto, keyword_retry)
                    return
        except Exception as e:
            logger.debug(f"Fallback - erro com {keyword_retry}: {e}")
            continue
    
    # Se ainda assim nao encontrou, apenas registra (nao envia mensagem generica)
    logger.error("❌ Nenhum produto 3D valido encontrado apos multiplas tentativas")


def postar_promocao() -> None:
    """Busca produto no AliExpress e posta. Sempre posta algo — 3 camadas de fallback."""
    global _keyword_index

    produto_escolhido: dict | None = None
    keyword_usada = KEYWORDS[_keyword_index % len(KEYWORDS)]

    # Camada 1 & 2: percorre todas as keywords até achar produto
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

    # Camada 3: fallback total via template
    if not produto_escolhido:
        logger.warning("Nenhum produto encontrado em %d keywords. Usando fallback.", len(KEYWORDS))
        try:
            _postar_promocao_fallback(keyword_usada)
        except Exception as e:
            logger.error("Fallback também falhou: %s", e)
        return

    # Produto encontrado — monta e envia
    try:
        _montar_e_enviar_produto(produto_escolhido, keyword_usada)
    except Exception as e:
        logger.error("Erro ao montar promoção do produto: %s", e)
        try:
            _postar_promocao_fallback(keyword_usada)
        except Exception as e2:
            logger.error("Fallback também falhou: %s", e2)


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
    logger.info("Buscando notícia via RSS...")
    try:
        feed_url = RSS_FEEDS[_feed_index % len(RSS_FEEDS)]
        _feed_index += 1

        feed = feedparser.parse(feed_url)
        if not feed.entries:
            logger.warning("Feed vazio: %s", feed_url)
            return

        entrada = feed.entries[0]
        titulo = entrada.get("title", "Sem título")
        link = entrada.get("link", "")
        resumo = entrada.get("summary", "")

        # Remove HTML do resumo
        resumo_limpo = html_mod.unescape(resumo)
        resumo_limpo = resumo_limpo.replace("<p>", "").replace("</p>", "")
        resumo_limpo = resumo_limpo[:200] + "..." if len(resumo_limpo) > 200 else resumo_limpo

        texto = (
            f"📰 *{titulo}*\n\n"
            f"{resumo_limpo}\n\n"
            f"🔗 [Leia mais]({link})\n\n"
            f"_#impressao3d #noticia_"
        )
        enviar_mensagem(texto)
        logger.info("Notícia enviada: %s", titulo)
    except Exception as e:
        logger.error("Erro em postar_noticia: %s", e)


# ---------------------------------------------------------------------------
# Lógica 4: Sorteios
# ---------------------------------------------------------------------------

def _verificar_reivindicacao() -> None:
    """Verifica se há reivindicações expiradas e as remove."""
    config = _load_config()
    participants = _load_participants()

    vencedor_id = config.get("VENCEDOR_PENDENTE")
    if not vencedor_id:
        return

    vencedor_str = str(vencedor_id)
    if vencedor_str not in participants:
        logger.warning("Vencedor %s não encontrado em participants", vencedor_id)
        return

    vencedor = participants[vencedor_str]
    deadline = vencedor.get("deadline_reivindicacao")

    if not deadline:
        logger.warning("Vencedor %s sem deadline de reivindicação", vencedor_id)
        return

    try:
        deadline_dt = datetime.datetime.fromisoformat(deadline)
        agora = datetime.datetime.now()

        if agora > deadline_dt:
            logger.warning("Deadline de reivindicação expirado para %s", vencedor_id)
            bot.send_message(
                ADMIN_ID,
                f"⏰ Deadline expirado para {vencedor.get('nome', 'Desconhecido')} (ID: {vencedor_id}). "
                f"Prêmio retorna ao sorteio.",
            )
            del participants[vencedor_str]
            config["VENCEDOR_PENDENTE"] = None
            _save_participants(participants)
            _save_config(config)
    except Exception as e:
        logger.error("Erro ao verificar reivindicação: %s", e)


def _postar_top10_sorteio() -> None:
    """Posta o top 10 de participantes do sorteio."""
    participants = _load_participants()
    if not participants:
        logger.info("Nenhum participante para top 10")
        return

    sorted_participants = sorted(
        participants.items(),
        key=lambda x: x[1].get("pontos", 0),
        reverse=True,
    )[:10]

    texto = "🏆 *Top 10 do Sorteio*\n\n"
    for idx, (uid, dados) in enumerate(sorted_participants, 1):
        nome = dados.get("nome", "Desconhecido")
        pontos = dados.get("pontos", 0)
        texto += f"{idx}. {nome} — {pontos} pontos\n"

    texto += "\n_#sorteio #top10_"
    enviar_mensagem(texto)
    logger.info("Top 10 do sorteio enviado")


def _alerta_90_porcento() -> None:
    """Envia um alerta quando a meta do sorteio atinge 90%."""
    config = _load_config()
    participants = _load_participants()

    if config.get("ALERTA_90_ENVIADO"):
        logger.debug("Alerta de 90%% já foi enviado neste sorteio")
        return

    total_pontos = sum(p.get("pontos", 0) for p in participants.values())
    meta = config.get("META_SORTEIO", 1000)

    if total_pontos >= meta * 0.9:
        texto = (
            f"⚠️ *Atenção!*\n\n"
            f"O sorteio está em {(total_pontos / meta * 100):.0f}% da meta!\n"
            f"Faltam apenas {meta - total_pontos} pontos para o sorteio acontecer! 🎉\n\n"
            f"_#sorteio #meta_"
        )
        enviar_mensagem(texto)
        config["ALERTA_90_ENVIADO"] = True
        _save_config(config)
        logger.info("Alerta de 90%% enviado")


# ---------------------------------------------------------------------------
# Handlers de comandos do bot
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["start"])
def cmd_start(message):
    """Handler para /start."""
    args = message.text.split()
    
    # Se veio com parâmetro 'sorteio', executa participação automática
    if len(args) > 1 and args[1] == "sorteio":
        cmd_participar(message)
        return
    
    # Caso contrário, mostra menu de teste
    texto = (
        "👋 Olá! Sou o bot de promoções 3D.\n\n"
        "Comandos disponíveis:\n"
        "/testar — Testa uma promoção agora\n"
        "/participar — Participa do sorteio\n"
        "/status — Vê o status do sorteio\n"
    )
    bot.reply_to(message, texto)


@bot.message_handler(commands=["testar"])
def cmd_testar(message):
    """Handler para /testar — força uma promoção agora."""
    logger.info("Teste de promoção solicitado por %s", message.from_user.id)
    try:
        postar_promocao()
        bot.reply_to(message, "✅ Promoção de teste enviada!")
    except Exception as e:
        logger.error("Erro ao testar promoção: %s", e)
        bot.reply_to(message, f"❌ Erro: {e}")


@bot.message_handler(commands=["participar"])
def cmd_participar(message):
    """Handler para /participar — adiciona usuário ao sorteio."""
    user_id = message.from_user.id
    nome = message.from_user.first_name or "Usuário"
    
    participants = _load_participants()
    user_str = str(user_id)
    
    if user_str in participants:
        bot.reply_to(message, "✅ Você já está participando do sorteio!")
        return
    
    participants[user_str] = {
        "nome": nome,
        "pontos": 0,
        "data_inscricao": datetime.datetime.now().isoformat(),
    }
    _save_participants(participants)
    
    bot.reply_to(message, "🎉 Você entrou no sorteio! Boa sorte!")
    logger.info("Novo participante: %s (%s)", nome, user_id)


@bot.message_handler(commands=["status"])
def cmd_status(message):
    """Handler para /status — mostra status do sorteio."""
    config = _load_config()
    participants = _load_participants()
    
    total_pontos = sum(p.get("pontos", 0) for p in participants.values())
    meta = config.get("META_SORTEIO", 1000)
    
    texto = (
        f"📊 *Status do Sorteio*\n\n"
        f"Meta: {meta} pontos\n"
        f"Atual: {total_pontos} pontos\n"
        f"Progresso: {(total_pontos / meta * 100):.1f}%\n"
        f"Participantes: {len(participants)}\n"
    )
    bot.reply_to(message, texto)


# ---------------------------------------------------------------------------
# Agendamento de tarefas
# ---------------------------------------------------------------------------

def agendar_tarefas():
    """Agenda todas as tarefas recorrentes."""
    logger.info("Agendando tarefas...")

    # Promoções a cada 3 horas
    schedule.every(3).hours.do(postar_promocao)

    # Conteúdo educativo a cada 4 horas
    schedule.every(4).hours.do(postar_educativo)

    # Notícias a cada 6 horas
    schedule.every(6).hours.do(postar_noticia)

    # Top 10 do sorteio diariamente ao meio-dia
    schedule.every().day.at("12:00").do(_postar_top10_sorteio)

    # Verificação de deadline de reivindicação a cada 30 minutos
    schedule.every(30).minutes.do(_verificar_reivindicacao)

    # Alerta de 90% a cada 2 horas
    schedule.every(2).hours.do(_alerta_90_porcento)

    logger.info("✅ Agendamentos e polling de comandos ativos!")


def rodar_scheduler():
    """Loop que executa tarefas agendadas."""
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logger.error("Erro no scheduler: %s", e)
            time.sleep(5)


# ---------------------------------------------------------------------------
# Polling de mensagens do Telegram
# ---------------------------------------------------------------------------

def rodar_polling():
    """Loop de polling de mensagens do Telegram."""
    logger.info("Iniciando polling do Telegram...")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        logger.error("Erro no polling: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("🤖 Bot de Impressão 3D iniciando...")
    logger.info("✅ Servidor de monitoramento ativo na porta 10000")

    # Inicia Flask em thread separada
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000, debug=False), daemon=True).start()

    # Remove webhook anterior (se houver)
    try:
        bot.remove_webhook()
        time.sleep(1)
        logger.info("Webhook removido com sucesso.")
    except Exception as e:
        logger.warning("Erro ao remover webhook: %s", e)

    # Aguarda um pouco para garantir que não há outra instância
    logger.info("Aguardando 10 s para garantir que não há outra instância ativa...")
    time.sleep(10)

    # Agenda tarefas
    agendar_tarefas()

    # Inicia scheduler e polling em threads separadas
    scheduler_thread = threading.Thread(target=rodar_scheduler, daemon=True)
    polling_thread = threading.Thread(target=rodar_polling, daemon=True)

    scheduler_thread.start()
    polling_thread.start()

    # Mantém a aplicação rodando
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot interrompido pelo usuário")
