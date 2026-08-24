# AGENTS.md

Guia para agentes de IA trabalhando neste repositório.

## Projeto

Mineração de questões dos vestibulares USP (FUVEST), UNESP, UNICAMP e UNIVESP
(alvo principal): baixar PDFs, extrair texto e imagens, particionar em
questões (JSON em `data/json/`), classificar (área/tema) via IA e, no futuro,
armazenar em SQLite e pontuar dificuldade via IA (function calling), para
alimentar um estudo adaptativo numa interface Streamlit.

Documentação de referência:
- `docs/base-do-projeto.md` — arquitetura, schema, pastas, uso de IA, riscos.
- `docs/plano-de-acao.md` — execução por fases, critérios de go/no-go.

## Ambiente (validado nesta máquina)

- **O host NÃO tem `python3`/`uv`/`pip` instalados.** Todo código Python roda
  via Docker. `sudo` exige senha (não instalar pacotes no host).
- Imagem base para Python: `gemini-runner`
  (`python:3.14-slim` + `google-genai` + `requests`, ver `tools/gemini/Dockerfile`).
- Utilitários PDF (poppler-utils: `pdftotext`, `pdfinfo`, `pdftoppm`) rodam na
  imagem `alpine:latest` via `apk add --no-cache poppler-utils`.
- Chave de IA: variável `API_KEY` em `.env` (não versionado). Modelo validado:
  **`gemini-3.5-flash-lite`** (via API REST do Google AI Studio).
  `gemini-3.7-flash` dispara rate-limit nesta conta (cota ~15 RPM / 250 TPM) e
  deve ser evitado.

## O que funciona / o que não funciona na extração

- **NÃO funciona:** a ferramenta nativa de leitura de PDF do agente de CLI
  (modelo atual não aceita anexo PDF). Também **não** usar `pdftotext` nos
  cadernos de questões (layout quebrado).
- **Funciona (validado):** ler os PDFs com o Gemini → o modelo transcreve o
  caderno e o gabarito e retorna o JSON estruturado diretamente
  (`response_mime_type=application/json`). Única exceção: `pdftotext -layout`
  é útil apenas para **conferir gabaritos oficiais** (arquivos de texto).

## Pipeline de extração IA (validado — UNIVESP 2017–2024 e FUVEST 1ª fase 2010–2026)

Fluxo implementado em `tools/gemini/` (Python, roda via `gemini-runner`):

1. **upload**: `client.files.upload(pdf)` para o caderno de questões e o gabarito.
2. **extract**: `extract.py <label>` monta o prompt (regras de transcrição +
   catálogo `data/assuntos.json` + schema JSON) e chama
   `models/<modelo>:generateContent` com `file_data.file_uri`. Uma chamada por
   exame (~1–2,5 min), com retry/backoff em 429/500/503.
3. **saídas**:
   - `data/json/<label>_questoes.json` — schema completo (ver
     `docs/base-do-projeto.md` §6).
   - `data/json/<label>_imagens.json` — bbox das figuras/grandes
     imagens (`pagina`, `tipo`, `elemento`, `bbox`).
4. **validate**: `validate.py` confere gabarito contra o PDF oficial, cobertura
   sequencial, schema e strings de área/assunto contra o catálogo.
5. **repair**: `repair.py` casa assuntos divergentes com a string EXATA do
   catálogo (usar quando `validate.py` acusar "assunto fora do catálogo").
6. **gabfix** (SÓ FUVEST): `gabfix.py [labels...]` sobrescreve `gabarito`/`anulada`
   dos JSONs a partir do **texto oficial** do gabarito (`tmp/gabaritos/<label>_gabarito.txt`,
   gerado com `pdftotext -layout`), imprimindo as divergências para conferência.
   Usar depois do `extract` e antes/junto do `repair`.
7. **fix_paginas**: `fix_paginas.py [labels...]` grava `pagina` (1-indexada,
   pág. 1 = capa) em cada questão do `_questoes.json` e corrige `pagina` inválida
   no `_imagens.json`. Fontes: página do bbox (figuras) → localização pelo texto
   do enunciado no PDF (a extração da FUVEST insere `¬` entre palavras; a
   normalização converte não-alfanuméricos em espaço, senão a busca falha) →
   "Página N:" na `midia` → interpolação pelas páginas conhecidas. Roda sozinho
   no `run_all.py` após o `extract`. **O app NÃO lê PDF em runtime: a página vem
   dos JSONs.**

### Particularidades FUVEST (1ª fase — 90 questões, 5 alternativas)

- **Labels**: `fuvest_<ano>` (2010–2026), sem semestre (caderno é anual).
- **Gabarito multi-versão**: o PDF de gabarito traz as respostas de TODAS as
  versões em colunas separadas (`PROVA V/K/Q/X/Z` até 2024; `PROVA V1/V2/V3/V4`
  em 2025–26). O caderno anexado é a versão **V** (2010–2024) / **V1** (2025–26);
  o prompt instrui o modelo a usar SOMENTE essa coluna. Conferência: o
  `validate.py` cruza com o texto oficial (`tmp/gabaritos/*.txt`).
- **Anuladas**: marcadas com `*` ou a palavra `ANULADA` na coluna → `gabarito:
  null`, `anulada: true` (ex.: FUVEST 2014 Q51, 2016 Q43, 2022 Q54/Q81, 2026 Q3).
- **PDFs antigos (2 colunas, ex.: 2010)**: o modelo perde questões no meio da
  página em chamadas por faixa; usar **1 chamada p/ todo o PDF** (`STEP=<total>`)
  para esses anos — foi o que tornou o 2010 íntegro (90/90).
- **Erro comum de gabarito**: o modelo às vezes lê errado algumas células da
  coluna ou devolve letras em MAIÚSCULA. O `gabfix.py` corrige a partir do texto
  oficial; MAIÚSCULAS são normalizadas a minúsculas.
- **Área inválida**: `Estatística`→`Matemática` e `Geologia`→`Geografia` já foram
  mapeados; o catálogo usa áreas top-level sem esses nomes.
- **Enunciado curto**: questões de figura têm enunciado enxuto (ex.: "A charge",
  "As curvas", "Os gráficos revelam") — não são erro; o validador só acusa se for
  curto E sem `midia`.

> **O `bbox` NÃO é `[x0,y0,x1,y1]` 0–100.** O modelo gravou `[y0,x0,y1,x1]`
> em escala **0–1000** (permil). Converter com:
> `x0=bbox[1]/1000*W ; y0=bbox[0]/1000*H ; x1=bbox[3]/1000*W ; y1=bbox[2]/1000*H`
> (origem canto sup. esquerdo). Detalhes em `docs/base-do-projeto.md` §6.3.

Resultado extraído e validado (gabaritos 100% conferidos):
- **UNIVESP**: 9 exames, 525 questões (516 objetivas + 9 redações).
- **FUVEST 1ª fase**: 17 exames (2010–2026), 90 questões cada → **1530 questões**.
- Total: 26 exames, **2055 questões**. Atualizar a validade ao adicionar exames.

## App de estudo (Streamlit — parcialmente funcional)

- `app/study.py`: interface — para cada questão, mostra **questão em cima**
  (enunciado, textos de apoio, alternativas + gabarito) e **página embaixo** no
  viewer pan/zoom.
- `app/panzoom.py`:
  - Exibe a página via JPEG pré-renderizado
    (`data/paginas/<label>/p<NNN>.jpg`, gerado por
    `tools/gemini/render_pages.py` — max 1400px, qualidade 75, **JPEG colorido**)
    num `st.iframe` `<div>` com `transform: translate+scale`.
  - Arrastar (pan) + zoom (roda, duplo-clique, botões `+`/`−`); botões
    "Página inteira" e "Enquadrar questão".
  - **Não lê PDF**: a página vem do campo `pagina` dos JSONs (gravado por
    `fix_paginas.py`); `app/study.py` usa `pagina` do JSON → página do bbox →
    "Página N:" na `midia` → interpolação pelas páginas conhecidas.
- **Como rodar:** `docker compose up vestibular-app` → porta `8501` (na rede
  `web` do nginx-proxy-manager). O serviço usa `app/study.py` como comando.

## Servidor MCP (tutor + acervo para o AnythingLLM)

- `src/vestibular/mcp/server.py` expõe o motor de estudo e o acervo como
  ferramentas MCP via **SSE** (`FastMCP`): famílias **tutor**
  (`proxima_questao`, `responder`, `progresso`, `niveis_por_tema`, todas com
  parâmetro `usuario`, default `"eu"`) e **acervo** (read-only:
  `listar_exames`, `relatorio_provas`, `buscar_questoes`, `gabarito_exame`).
  `relatorio_provas(vestibular='univesp', nivel='todos', limite=0)` devolve o
  relatório de frequência real (contagens de `classificacoes`, inclui redação;
  uma questão pode contar em mais de um tema) com scoring por área/tema e por
  exame — usar para escolher as áreas mais cobradas.
- **Como rodar:** `docker compose up -d vestibular-mcp` → serviço interno na
  rede `web`, URL `http://vestibular-mcp:8891/sse`, **sem porta publicada no
  host** (AnythingLLM alcança só pela rede).
- **Interface REST (FastAPI):** o `main()` envolve o transport SSE do FastMCP
  numa app FastAPI — `/sse` e `/messages/` intactos p/ clientes MCP nativos;
  acrescenta `/openapi.json`, `/docs`, `/.well-known/mcp.json`,
  `GET /api/tools` e `POST /api/consultar` (`{"tool": "<nome>", "params": {...}}`
  chama a MESMA tool MCP e devolve o JSON). CORS liberado (`*`, sem credenciais)
  p/ o Gemini Web conectar pelo navegador. Dependência: extra `.[mcp]` agora
  inclui `fastapi`.
- **AnythingLLM:** Workspace → MCP servers → stream/SSE URL acima; provider do
  agente configurado no próprio AnythingLLM. O sistema usa o mesmo
  `data/vestibular.db` (volume `./data` compartilhado com o app).
- **Regra do gabarito:** as tools devolvem o gabarito sempre (decisão do
  usuário). O system prompt do agente no workspace deve instruir: "não revelar
  o gabarito antes de o aluno responder"; a descrição de `proxima_questao`
  repete o aviso.
- Instalação do pacote `mcp`: extra opcional `.[mcp]` no pyproject; o
  `Dockerfile` já instala `pip install -e ".[mcp]"`.
- Para testar clientes MCP sem expor o serviço: `docker run --network web ...`
  com a imagem base + `pip install mcp`, apontando para
  `http://vestibular-mcp:8891/sse` (usar cópia do DB em `tmp/`, nunca o real).

## Regras do ambiente

- Rodar comandos com `docker run`. Exemplo de extração completa:
  ```bash
  docker run --rm -w /work/tools/gemini \
    -e API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)" \
    -e MODEL=gemini-3.5-flash-lite \
    -v "$PWD":/work gemini-runner \
    python run_all.py univesp_2025
  ```
- Nunca commitar segredos, PDFs ou dados brutos (`.env`, `tmp/` e quase todo
  `data/` são ignorados). Exceções versionadas: `data/assuntos.json` (catálogo
  curado), `data/json/*_questoes.json` + `*_imagens.json` (dados extraídos e
  validados) e `data/paginas/*/*.jpg` (páginas renderizadas p/ o app). De
  resto, só código, docs e `tools/` são versionados.
- Não commitar a chave de API nem expor `figuras` recortadas de provas
  (`data/imagens/`) fora do repo; páginas completas em `data/paginas/` são
  versionadas.

## Estrutura

```
docs/              # base do projeto + plano de ação
src/               # pipeline Python puro (download, extract, parse, db, ia/, estudo)
app/               # Streamlit (interface de estudo) — study.py + panzoom.py
tools/gemini/      # toolkit validado: extração via Gemini (Dockerfile, extract/validate/repair/gabfix/fix_paginas/run_all)
data/              # QUASE NÃO VERSIONADA: pdfs, json/ e paginas/ (exceção:
                   # assuntos.json, data/json/*_questoes.json + *_imagens.json e
                   # data/paginas/*/*.jpg versionados), imagens/, vestibular.db
tmp/               # NÃO VERSIONADA: rascunhos/scratch (gabaritos extraídos p/ conferência)
scripts/           # CLI (click): ingest, classify, score (esqueleto)
```

## Schema (SQLite — motor de estudo em `src/vestibular/estudo/`)

Tabelas implementadas no motor: `vestibulares`, `questoes`, `classificacoes`,
`dificuldades` (score IA — ainda sem seed), `item_params` (b Rasch),
`fsrs_estados`, `habilidades` (θ MAP por área), `niveis_usuarios` (score/racha/
contagem por `(usuario, tema)`) e `tentativas`. Pendente do plano original:
`ia/dificuldade` (score), `ia/classificar`, `ia/feedback`.

## Como rodar / verificar

- Extração de um exame (`label` = `univesp_2021`, `univesp_2018_1s`, ...):
  `tools/gemini/run_all.py <labels...>`
- Validação dos JSONs: `tools/gemini/validate.py`
- Reparo dos assuntos: `tools/gemini/repair.py`
- Gravar `pagina` nas questões (rodar quando o PDF ainda existe; o `run_all.py`
  já roda ao extrair): `tools/gemini/fix_paginas.py [labels...]`
- Páginas JPEG do app (re-gerar ao adicionar exame):
  `docker run --rm -v "$PWD/data:/app/data" -w /app vestibular-app:latest \
  python tools/gemini/render_pages.py`
- App de estudo (Fase 2 parcial): `docker compose up vestibular-app` → porta 8501.
- MCP (AnythingLLM): `docker compose up -d vestibular-mcp` →
  `http://vestibular-mcp:8891/sse` (só rede interna `web`).
- Sincronizar o plano de estudos com o Trilium (`tools/trilium/sync_plano.py`,
  CLIENTE MCP do Trilium — não confundir com o vestibular-mcp acima, que é o
  servidor; token ETAPI em `TRILIUM_TOKEN` no `.env`, criar em Opções > ETAPI):
  ```bash
  docker run --rm --network host --env-file .env \
    -v "$PWD":/work -w /work vestibular-app:latest \
    python tools/trilium/sync_plano.py --dry-run
  ```
  Sem `--dry-run` aplica; `--check` valida (exit 0 se consistente).
- Auto-recorte de figuras (opcional): `tools/gemini/extract_images.py <labels...>`
  → `data/imagens/` (não é usado pelo app).
- Lint/format (quando adicionado): `ruff check .` e `ruff format .`.

## Convenções

- Python 3.11+, tipagem leve quando útil, sem comentários redundantes.
- Não criar arquivos fora do plano (docs/*.md) sem necessidade.
- Mudanças de arquitetura passam pelos docs primeiro; seguir o schema e as
  decisões de `docs/base-do-projeto.md`.
