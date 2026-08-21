# Vestibular — Mineração de Questões

> Repositório **privado** para estudo pessoal com IA.

Pipeline para minerar questões dos vestibulares **USP (FUVEST)**, **UNESP**,
**UNICAMP** e **UNIVESP** (alvo principal), classificá-las por **área/tema**,
pontuar **dificuldade** via IA e disponibilizar **estudo adaptativo** com
feedback.

## Visão geral

```
PDFs oficiais → OCR/texto + imagens → questões → SQLite
                                        ↓
                     IA (function calling)
                     ├─ classificação (área/tema)
                     ├─ dificuldade (low-thinking)
                     └─ feedback ao errar
                                        ↓
                     App Streamlit — estudo adaptativo por tema/nível
```

## Stack

- **Linguagem:** Python 3.11+
- **Pipeline:** Python puro + SQLite (sem framework web na ingesta)
- **Extração:** PyMuPDF (texto/imagens), PaddleOCR (PDFs escaneados)
- **IA:** function calling para classificar / pontuar / feedback
- **Interface de estudo:** Streamlit
- **Deploy:** Docker Compose (rede `web` externa), Nginx Proxy Manager

## Como rodar

### Docker (pipeline + app)

```bash
# build das imagens
docker compose build

# subir o app Streamlit
docker compose up -d vestibular-app

# rodar a CLI de ingesta (sob demanda)
docker compose --profile ingest run vestibular-pipeline <comando>
```

O `app` escuta em `0.0.0.0:8501` na rede `web` (externa, gerenciada pelo NPM),
**sem portas expostas no host**. Acessar via reverse proxy apontando para o
container `vestibular-app:8501`.

### CLI

```bash
uv run scripts/cli.py --help
uv run scripts/cli.py ingest
uv run scripts/cli.py classify
uv run scripts/cli.py score
```

## Estrutura

```
docs/            # base do projeto + plano de ação
src/             # downloader, extractor, parser, db, ia/, estudo
app/             # Streamlit (interface de estudo)
scripts/         # CLI (click): ingest, classify, score
data/            # NÃO VERSIONADA: PDFs, json/, imagens/, vestibular.db
```

## Notas importantes

- **`data/` não é versionada** (PDFs, JSONs, imagens e o SQLite ficam lá).
  Só código e docs vão para o git.
- Configuração local (chaves de IA) vai em `.env` (não versionado); copie de
  `.env.example`.
- Este é um projeto **pessoal/privado** para estudo. Não redistribua os PDFs
  ou o banco de questões.

## Documentação

- `docs/base-do-projeto.md` — arquitetura, schema, pastas, uso de IA, riscos.
- `docs/plano-de-acao.md` — execução por fases e critérios de go/no-go.
- `AGENTS.md` — guia para agentes de IA trabalhando no repositório.

## Status

Implementação em andamento — **Fase 1** (prova de conceito do pipeline).
