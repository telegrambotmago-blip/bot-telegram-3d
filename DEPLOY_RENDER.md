# Guia de Deployment no Render — Bot Telegram 24/7

Este guia explica como hospedar seu bot no **Render** para que rode **24/7 gratuitamente**.

## Por que Render?

- ✅ **Gratuito** — Plano free inclui 750 horas/mês (mais que suficiente para 24/7)
- ✅ **Sem hibernação** — Serviços web não hibernam no Render
- ✅ **Simples** — Deploy direto do GitHub com um clique
- ✅ **Confiável** — Uptime excelente para bots
- ✅ **Suporta Python** — Runtime Python 3.11 nativo

## Pré-requisitos

1. **Conta no GitHub** (você já tem)
2. **Conta no Render** (gratuita em https://render.com)
3. **Token do Telegram** (do BotFather)
4. **Chaves da API** (Gemini, AliExpress)

## Passo 1: Preparar o Repositório

O repositório já está pronto! Verificamos que temos:
- ✅ `render.yaml` — Configuração de deploy
- ✅ `requirements.txt` — Dependências Python
- ✅ `main.py` — Código do bot
- ✅ `Dockerfile` — Alternativa para Docker

## Passo 2: Criar Conta no Render

1. Acesse https://render.com
2. Clique em **Sign Up** (ou **Sign in with GitHub**)
3. Autentique com sua conta do GitHub
4. Autorize o Render a acessar seus repositórios

## Passo 3: Conectar o Repositório

1. No painel do Render, clique em **New +**
2. Selecione **Web Service**
3. Clique em **Connect a repository**
4. Procure por `bot-telegram-3d`
5. Clique em **Connect**

## Passo 4: Configurar o Serviço

Na página de criação do serviço, preencha:

| Campo | Valor |
|-------|-------|
| **Name** | `aliexpress-sem-taxa-bot` |
| **Environment** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Plan** | `Free` |
| **Auto-deploy** | `Yes` (ativar) |

## Passo 5: Adicionar Variáveis de Ambiente

Antes de fazer deploy, adicione as variáveis de ambiente:

1. Na página do serviço, vá em **Environment**
2. Clique em **Add Environment Variable**
3. Adicione cada uma das variáveis abaixo:

### Variáveis Obrigatórias

```
TELEGRAM_TOKEN = seu_token_do_bot_aqui
```

### Variáveis Recomendadas

```
GEMINI_API_KEY = sua_chave_gemini_aqui
ALI_APP_KEY = sua_app_key_aqui
ALI_APP_SECRET = seu_app_secret_aqui
ALI_TRACKING_ID = seu_tracking_id_aqui
```

**Dica:** Copie do arquivo `.env.example` e substitua pelos valores reais.

## Passo 6: Fazer Deploy

1. Clique em **Create Web Service**
2. Aguarde o build completar (leva 2-5 minutos)
3. Você verá a mensagem "Your service is live" quando estiver pronto

## Passo 7: Verificar se Está Funcionando

1. No painel do Render, copie a URL do serviço (ex: `https://aliexpress-sem-taxa-bot.onrender.com`)
2. Abra a URL no navegador — deve mostrar "Bot is alive!"
3. Teste o bot no Telegram enviando `/ping`

## Atualizar o Bot

Sempre que você fizer mudanças no código:

1. Faça commit e push para o GitHub
2. O Render detecta automaticamente (se auto-deploy estiver ativado)
3. Faz rebuild e redeploy automaticamente

## Troubleshooting

### Bot não está respondendo

**Problema:** Envio mensagens mas o bot não responde.

**Solução:**
1. Verifique se `TELEGRAM_TOKEN` está correto
2. Abra a URL do serviço no navegador — deve mostrar "Bot is alive!"
3. Verifique os logs no Render:
   - Vá em **Logs** no painel do serviço
   - Procure por erros

### Erro "Build failed"

**Problema:** O deploy falha durante o build.

**Solução:**
1. Verifique se `requirements.txt` está correto
2. Verifique os logs de build no Render
3. Certifique-se de que todas as dependências estão listadas

### Serviço hibernou

**Problema:** O bot parou de responder após algumas horas.

**Solução:**
- Render não hiberna serviços web no plano free
- Se parou, pode ser erro no código
- Verifique os logs para encontrar a causa

### Variáveis de ambiente não funcionam

**Problema:** Bot inicia mas não consegue acessar as APIs.

**Solução:**
1. Verifique se as variáveis estão configuradas no Render (não no `.env`)
2. Reinicie o serviço: **Manual Deploy** → **Deploy latest commit**
3. Verifique se os valores estão corretos (sem espaços extras)

## Monitoramento

### Ver Logs em Tempo Real

1. No painel do Render, clique em **Logs**
2. Veja as mensagens do bot em tempo real
3. Procure por erros ou avisos

### Reiniciar o Serviço

Se o bot travar:
1. Vá em **Settings**
2. Clique em **Restart service**
3. Aguarde reiniciar (30 segundos)

### Atualizar Variáveis de Ambiente

1. Vá em **Environment**
2. Edite a variável
3. Clique em **Save**
4. O serviço reinicia automaticamente

## Upgrade Futuro

Se precisar de mais recursos:

- **Plano Starter** (~$7/mês): Mais CPU, RAM e sem limite de horas
- **Plano Pro** (~$12/mês): Ainda melhor performance
- **Docker**: Deploy em qualquer lugar (Railway, Heroku, etc.)

## Suporte

Se tiver problemas:

1. Verifique os logs no Render
2. Teste o bot localmente: `python main.py`
3. Verifique as variáveis de ambiente
4. Consulte a documentação do Render: https://render.com/docs

## Próximos Passos

Após o bot estar rodando:

1. Teste todos os comandos (`/ping`, `/participar`, `/sortear`, etc.)
2. Configure os canais de destino
3. Defina a meta de sorteio (`/setar_sorteio`)
4. Comece a postar promoções!

---

**Seu bot está pronto para rodar 24/7!** 🚀
