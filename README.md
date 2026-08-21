# Vestibular — Mineração de Questões

> Repositório **privado** para estudo pessoal com IA.

Pipeline para minerar questões dos vestibulares **USP (FUVEST)**, **UNESP**,
**UNICAMP** e **UNIVESP** (alvo principal), classificá-las por **área/tema**,
pontuar **dificuldade** via IA e disponibilizar **estudo adaptativo** com
feedback.

## Visão geral

```
PDFs oficiais → IA (Gemini) transcreve e estrutura → JSON por exame
                                                    ↓
                          validação (gabarito oficial + catálogo)
                                                    ↓
                     SQLite → IA (function calling) → classificação/dificuldade
                                                    ↓
                     App Streamlit — estudo adaptativo por tema/nível
```

**Status atual:** o primeiro elo está **funcionando e validado** — extração
completa dos 9 exames UNIVESP 2017–2024 em `data/json/` (525 questões:
516 objetivas + 9 redações), gabaritos 100% conferidos contra os PDFs oficiais.

## Extração de questões (validado — Fase 1 parcial)

O que funciona hoje é a extração **via IA (Gemini)**: o modelo lê os PDFs
nativamente (caderno + gabarito) e devolve o JSON estruturado das questões.
Não usar `pdftotext`/OCR para isso — quebra o layout dos cadernos.

### Pré-requisitos

- Docker (o host **não** tem Python; tudo roda em contêiner).
- Chave da API do Google AI Studio em `.env`:
  ```bash
  echo "API_KEY=AIza..." > .env
  ```
- Imagem `gemini-runner` (Python + SDK `google-genai`):
  ```bash
  docker build -t gemini-runner -f tools/gemini/Dockerfile tools/gemini/
  ```

### Rodar a extração de um exame

Cada exame é um par `data/univesp_<label>_questoes.pdf` +
`data/univesp_<label>_gabarito.pdf` (ex.: `univesp_2021`, `univesp_2018_1s`).

```bash
docker run --rm -w /work/tools/gemini \
  -e API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)" \
  -e MODEL=gemini-3.5-flash-lite \
  -v "$PWD":/work gemini-runner \
  python run_all.py univesp_2021
```

Saídas em `data/json/`:
- `univesp_2021_questoes.json` — schema completo por questão (enunciado,
  textos de apoio, mídia, alternativas, gabarito, áreas/assuntos).
- `univesp_2021_imagens.json` — localização (página + `bbox`) das figuras,
  gráficos, tabelas e cartuns.

### Validação e reparo

```bash
docker run --rm -w /work/tools/gemini \
  -e API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)" \
  -e MODEL=gemini-3.5-flash-lite \
  -v "$PWD":/work gemini-runner python validate.py   # gabarito + schema + catálogo
docker run --rm -w /work/tools/gemini \
  -e API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)" \
  -e MODEL=gemini-3.5-flash-lite \
  -v "$PWD":/work gemini-runner python repair.py     # casa assuntos ao catálogo exato
```

Detalhes do fluxo, modelo e limites (15 RPM/250 TPM) em `AGENTS.md`.

## Stack

- **Pipeline de ingesta:** IA Gemini (`google-genai`) → JSON → SQLite (planejado)
- **Classificação/score:** IA via function calling (planejado)
- **Interface de estudo:** Streamlit em `app/` (esqueleto — Fase 2)
- **Runtime de desenvolvimento:** Docker (`gemini-runner`, alpine+poppler)

## Estrutura

```
docs/              # base do projeto + plano de ação
src/               # pipeline Python puro (download, extract, parse, db, ia/, estudo)
app/               # Streamlit (interface de estudo)
tools/gemini/      # toolkit VALIDADO de extração via Gemini (Dockerfile, scripts)
scripts/           # CLI (click): ingest, classify, score (esqueleto)
data/              # NÃO VERSIONADA: PDFs, json/, imagens/, vestibular.db
tmp/               # NÃO VERSIONADA: rascunhos/scratch
```

## Notas importantes

- **`data/` e `tmp/` não são versionadas** (PDFs, JSONs, imagens e o SQLite
  ficam lá). Só código, docs e `tools/` vão para o git.
- Configuração local (chaves de IA) vai em `.env` (não versionado); copie de
  `.env.example`.
- Este é um projeto **pessoal/privado** para estudo. Não redistribua os PDFs
  ou o banco de questões.
- Modelo validado: `gemini-3.5-flash-lite`. Evite `gemini-3.7-flash` (rate
  limit ~15 RPM/250 TPM nesta conta).

## Documentação

- `docs/base-do-projeto.md` — arquitetura, schema, pastas, uso de IA, riscos.
- `docs/plano-de-acao.md` — execução por fases e critérios de go/no-go.
- `AGENTS.md` — guia para agentes de IA trabalhando no repositório.

## Status

- [x] **Fase 1 (parcial):** extração UNIVESP 2017–2024 via IA (9 exames,
      525 questões — gabaritos 100% conferidos).
- [ ] Importação para SQLite, classificação/score (function calling).
- [ ] **Fase 2:** app Streamlit de estudo adaptativo.
- [ ] **Fase 3:** escala para FUVEST, UNESP, UNICAMP.