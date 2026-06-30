# Bot Telegram 3D — Impressão 3D + AliExpress Affiliate

Um bot Telegram completo e robusto para gerenciar sorteios, promover produtos do AliExpress, compartilhar dicas educativas e notícias sobre impressão 3D.

## Funcionalidades Principais

### 🎰 Sistema de Sorteio
- Inscrição automática de participantes via `/participar`
- Sorteio automático quando a meta de membros é atingida
- Prazo de 48 horas para reivindicação do prêmio
- Verificação automática de deadlines via scheduler (não em memória)
- Sincronização thread-safe de dados

### 🛒 Promoções AliExpress
- Busca automática de produtos via API de afiliados
- Geração de copy de promoção com Gemini AI
- Postagem automática em múltiplos canais
- Fallback inteligente em 3 camadas

### 💡 Conteúdo Educativo
- Dicas de impressão 3D geradas com Gemini
- Notícias de RSS feeds sobre impressão 3D
- Calculadora de custo de impressão

### 🛠️ Ferramentas para Makers
- Calculadora de custo de impressão (material + energia)
- Central de ajuda técnica com Gemini
- Tópicos: mesa, Z-wobble, suporte, retração, resina, armazenamento

### 👨‍💼 Painel de Controle Admin
- Menu interativo com todos os comandos
- Visualização de status em tempo real
- Configuração de meta e prêmio
- Gerenciamento de participantes

## Requisitos

- Python 3.11+
- Variáveis de ambiente:
  - `TELEGRAM_TOKEN`: Token do bot Telegram
  - `GEMINI_API_KEY`: Chave da API Google Gemini (opcional, mas recomendado)
  - `ALI_APP_KEY`: Chave da API AliExpress Affiliate
  - `ALI_APP_SECRET`: Secret da API AliExpress Affiliate
  - `ALI_TRACKING_ID`: ID de rastreamento do AliExpress

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/telegrambotmago-blip/bot-telegram-3d.git
cd bot-telegram-3d

# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis de ambiente
export TELEGRAM_TOKEN="seu_token_aqui"
export GEMINI_API_KEY="sua_chave_aqui"
# ... outras variáveis

# Executar o bot
python main.py
```

## Deployment

### Docker
```bash
docker build -t bot-telegram-3d .
docker run -e TELEGRAM_TOKEN="seu_token" \
           -e GEMINI_API_KEY="sua_chave" \
           -e ALI_APP_KEY="sua_chave" \
           -e ALI_APP_SECRET="seu_secret" \
           -e ALI_TRACKING_ID="seu_id" \
           bot-telegram-3d
```

### Replit
1. Clone o repositório no Replit
2. Configure as variáveis de ambiente em **Secrets**
3. Execute com `python main.py`

### Render / Railway
1. Conecte o repositório
2. Configure as variáveis de ambiente
3. O bot rodará na porta 10000 (Flask keep-alive)

## Arquitetura

### Concorrência Thread-Safe
- Uso de `threading.RLock()` para proteção de acesso aos arquivos JSON
- Handlers do Telegram rodam em threads separadas
- Scheduler roda em thread dedicada
- Sincronização garantida para evitar corrupção de dados

### Persistência
- `config.json`: Configuração do sorteio (meta, prêmio, vencedor pendente)
- `participants.json`: Lista de participantes inscritos
- `audit.log`: Log de auditoria de ações administrativas

### Agendamento
- Promoções: 8 vezes por dia (00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00)
- Educativo: 6 vezes por dia
- Notícias: 6 vezes por dia
- Placar Top10: 1 vez por dia (12:00)
- Verificação de deadline: A cada 30 minutos

## Comandos

### Usuários
- `/participar` — Inscrever-se no sorteio
- `/reivindicar` — Reivindicar prêmio após sorteio
- `/top10` — Ver placar de inscritos
- `/ping` — Verificar status do bot
- `/ajuda` — Central de ajuda técnica
- `/custo` — Calculadora de custo de impressão

### Admin
- `/menu` — Painel de controle
- `/sortear` — Realizar sorteio manualmente
- `/setar_sorteio` — Configurar meta e prêmio
- `/status` — Ver status completo
- `/resetar_sorteio` — Limpar lista de participantes
- `/anunciar` — Publicar sorteio nos canais
- `/testar` — Menu de testes

## Correções Recentes (v1.1.0)

✅ **Sincronização Thread-Safe**: Adicionado `threading.RLock()` para proteção de acesso aos arquivos JSON  
✅ **Verificação de Deadline**: Substituído timer em memória por scheduler que verifica deadlines persistidos  
✅ **Tratamento de Variáveis de Ambiente**: Uso de `os.getenv()` com valores padrão para evitar crashes  
✅ **Polling Simplificado**: Removido loop redundante, delegando retry para Telebot nativo  
✅ **Fallback Gemini**: Templates locais quando API não está disponível  

## Estrutura de Arquivos

```
bot-telegram-3d/
├── main.py                 # Código principal do bot
├── requirements.txt        # Dependências Python
├── pyproject.toml         # Configuração do projeto
├── Dockerfile             # Imagem Docker
├── .gitignore             # Arquivos ignorados pelo Git
├── config.json            # Configuração do sorteio
├── participants.json      # Lista de participantes
├── audit.log              # Log de auditoria
└── README.md              # Este arquivo
```

## Troubleshooting

### Bot não responde
- Verifique se `TELEGRAM_TOKEN` está correto
- Verifique logs no terminal
- Certifique-se de que a porta 10000 está acessível (Flask keep-alive)

### Erro 409 (Conflict)
- Significa que outra instância do bot está rodando
- Mate o processo anterior: `pkill -f "python main.py"`

### Gemini não funciona
- Verifique se `GEMINI_API_KEY` está configurada
- O bot funcionará com templates locais se a chave não estiver disponível

### Participantes desaparecem
- Verifique permissões de arquivo em `participants.json`
- Confirme que o bot tem permissão de escrita no diretório

## Contribuindo

Sinta-se livre para abrir issues e pull requests com melhorias, correções e novas funcionalidades!

## Licença

MIT

## Autor

Desenvolvido com ❤️ para a comunidade de makers de impressão 3D.
