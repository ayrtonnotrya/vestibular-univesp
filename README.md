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
                    App Streamlit — estudo (viewer pan/zoom da página)
```

**Status atual:**
- **Extração validada:** 9 exames UNIVESP 2017–2024 em `data/json/` (525
  questões: 516 objetivas + 9 redações), gabaritos 100% conferidos.
- **App de estudo funcional** (`app/study.py`): mostra cada questão com a
  página original em **viewer pan/zoom** — arrastar + zoom in/out, já
  enquadrada no `bbox` da figura quando existe.

Nesta sessão o foco migrou de **auto-recorte das figuras** para **exibição da
página inteira em pan/zoom** (curadoria/manipulação manual, muito mais
robusta). Ver `docs/base-do-projeto.md` §6 e `app/panzoom.py`.

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

## App de estudo (Funcional — Fase 2 parcial)

Disponível via `docker compose up vestibular-app` (porta 8501). Para cada
questão, o app mostra, nesta ordem:

1. **Questão** (em cima): enunciado, textos de apoio, alternativas (radio +
   botão Responder) com gabarito.
2. **Página original** (embaixo): viewer **pan/zoom** da página do PDF
   (renderizada na hora com PyMuPDF), que abre **enquadrada no `bbox`** da
   figura quando existe.

**Viewer pan/zoom** (`app/panzoom.py`):
- Arrastar para mover (pan); **zoom in/out** pela roda do mouse, duplo-clique
  e botões `+`/`−`.
- Botões "**Página inteira**" (fit total) e "**Enquadrar questão**" (volta ao
  bbox).
- A página é renderizada on-demand do PDF (não depende de PNG pré-renderizado),
  com cache por exame.

**Página de cada questão:** quando há `bbox` (em `figuras_coordenadas`), usa
`figs[0]["pagina"]`; senão, o helper `question_page()` localiza a página a
partir do **texto do enunciado** (busca normalizada no texto das páginas do
PDF), o que permite mostrar a página correta mesmo em questões **sem mídia**.

### Passo a passo (sem compilar nada)

```bash
# 0) (só se a rede de proxy ainda não existir)
docker network create web

# 1) construir a imagem (uma vez)
docker compose build vestibular-app

# 2) subir o app
docker compose up -d vestibular-app
# -> http://localhost:8501 (ou via domínio do proxy, se configurado)
```

> O serviço já cai publicado na rede `web` (nginx-proxy-manager) do compose —
> basta apontar um host para `vestibular-app:8501`.

> **Sobre `data/imagens/`:** hoje é gerada apenas pelo utilitário opcional
> `extract_images.py`. O app de estudo **não** usa esses PNGs — ele mostra a
> página inteira em pan/zoom (on-demand). Pode apagar `data/imagens/` se não
> precisar dos recortes em lote.

## Extração automática de figuras (utilitário opcional)

`tools/gemini/extract_images.py` faz auto-recorte das figuras a partir do
`bbox` do JSON, refinando com a geometria real do PDF (PyMuPDF) — imagens
raster (`get_image_info`) e clusters vetoriais (`get_drawings`). É uma
alternativa em lote quando se quiser os PNGs recortados, mas o app de estudo
**não** depende dele (usa pan/zoom da página).

```bash
docker run --rm -w /work/tools/gemini -v "$PWD":/work gemini-runner \
  python extract_images.py univesp_2024
# saída: data/imagens/univesp_2024/q<num>_<indice>_<tipo>.png
```

## Stack

- **Pipeline de ingesta:** IA Gemini (`google-genai`) → JSON → SQLite (planejado)
- **Classificação/score:** IA via function calling (planejado)
- **Interface de estudo:** Streamlit em `app/` (`study.py` — funcional; pan/zoom)
- **Runtime de desenvolvimento:** Docker (`gemini-runner`; `vestibular-app` via compose)

## Estrutura

```
docs/              # base do projeto + plano de ação
src/               # pipeline Python puro (download, extract, parse, db, ia/, estudo)
app/               # Streamlit (interface de estudo) — study.py + panzoom.py
tools/gemini/      # toolkit VALIDADO de extração via Gemini (Dockerfile, extract/run_all/validate/repair)
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

- [x] **Extração UNIVESP 2017–2024** via IA (9 exames, 525 questões —
      gabaritos 100% conferidos).
- [x] **App de estudo** (`app/study.py`) com viewer **pan/zoom** da página
      (enquadrada no `bbox` da figura) — funcional.
- [ ] Importação para SQLite, classificação/score (function calling).
- [ ] **Fase 2 completa:** estudo adaptativo por tema/nível + feedback da IA.
- [ ] **Fase 3:** escala para FUVEST, UNESP, UNICAMP.
