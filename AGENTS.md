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
- **Chave de IA**: variável `API_KEY` em `.env` (não versionado) — Google AI
  Studio. Modelo validado: **`gemini-3.5-flash-lite`** (via API REST do Google
  AI Studio). `gemini-3.7-flash` dispara rate-limit nesta conta (cota ~15 RPM /
  250 TPM) e deve ser evitado. Para o `score_dificuldade` usa-se o router
  OpenAI-compatível do OpenCode Go: endpoint padrão
  `OPENCODE_BASE_URL=http://100.90.193.17:18905` (IP Tailscale da LAN — em
  Docker usar `--network host`; `OPENCODE_API_KEY` opcional no `.env`).

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
8. **score_dificuldade**: `score_dificuldade.py` pontua dificuldade via IA em
   duas passadas sem repetir chamadas no redeploy (as respostas ficam nos JSONs
   versionados — chaves `respostas_ia`, `score_ia` e `bbox_questao`):
   - `passada1` (router OpenCode Go / vision; `BACKEND=gemini` volta ao Gemini)
     é OPCIONAL: envia cada página com figura e devolve o `bbox_questao` (caixa
     de TODO o bloco da questão, em permil `[y0,x0,y1,x1]`). Os crops saíram
     pouco confiáveis — não é usado no fluxo padrão.
   - `passada2` (router OpenCode Go, OpenAI-compatível) — **texto puro**:
     resolve as questões SEM imagem (`deepseek-v4-flash`), N tentativas por
     questão, paralelo com backoff (`CONCURRENCY`; `thinking` desligado por
     padrão — o `smoke` avisa se o router o reativar). Questões com imagem são
     ignoradas; `--com-imagem` liga a re-resolução via crop do `bbox_questao`
     verificado (`deepseek-v4-flash-vision-exp`).
   - `seed`: grava `score_ia` nos JSONs e semeia `dificuldades` +
     `item_params.b` (logit suavizado, κ=4, só itens com `n_obs=0`) no SQLite.
     Após um deploy do zero, basta rodar `seed` — sem novas chamadas.
   - `status`/`smoke`/`qa`: cobertura por exame, teste do router e validação
     geométrica dos bbox (`--montagem` exporta os crops para conferência).
9. **extract_features**: `extract_features.py` extrai features de complexidade
   cognitiva (bloom_level, logic_steps, interdisciplinary,
   distractor_plausibility, inversion_command, reading_load,
   requires_formula_recall, prior_knowledge_dependency) via router OpenCode Go
   (modelo `hy3`, `MODEL_FEATURES`). Envia as questões SEM imagem em lotes de
   `--batch` (default 10) por mensagem; o modelo devolve JSON chaveado pelo
   `numero` da questão (`{"questoes": {"<numero>": {...}}}`), o que garante o
   mapeamento de volta. Persiste em `features_ia` (com `modelo` e `data`) nos
   `_questoes.json` versionados; `--com-midia` inclui questões com figura
   usando a descrição textual, `--limite N` limita (teste), `--force` refaz.
   Comandos: `status`, `extract`.

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
  viewer pan/zoom. Modos: **Estudar** (adaptativo), **Revisão** (fila de
  pendências), **Explorar** e **Estatísticas** — o painel de estatísticas
  (`app/estatisticas.py`, SQL direto no `data/vestibular.db`) mostra visão
  geral (aproveitamento, dificuldade média b, temas vencidos), evolução por
  dia, desempenho por área/θ, por tema (score/racha/lapses/estado FSRS), por
  exame, fila de revisão FSRS e o histórico detalhado de tentativas.
- **Modo Estudar (adaptativo):** o pool de candidatos é o **catálogo inteiro**
  (`motiva._temas_pool`), sem portão FSRS; o sorteio é ponderado por
  prioridade = 0,4·frequência (UNIVESP) + 0,4·fraqueza + 0,2·exploração. A
  fraqueza usa `1 − score` do tema quando `contagem >=
  MIN_TENTATIVAS_REVISAO` (3); abaixo do portão usa `1 − sigmoid(θ da área)`
  (`rasch._sigmoid`). Questão do tema sorteado via `seletor.escolher_aleatoria`
  (inéditas primeiro); `responder()` atualiza FSRS/θ/b/nível como antes.
- **Modo Revisão:** fila dedicada via `motiva.proxima_revisao` (temas
  **vencidos** do FSRS — só o grupo due de `fsrs.vencidos()`, fora do cap) +
  `seletor.escolher_revisao`: questão **já vista** — pendências do caderno de
  erros (última resposta `correta=0` ou `grau_certeza IN (duvida, chute)`)
  primeiro, depois acertos antigos; **nunca** inéditas. Contadores
  "X vencidos · Y pendências" via `motiva.resumo_revisao`.
- **Política do FSRS por tema** (`src/vestibular/estudo/fsrs_config.py`):
  tema só ganha card com `MIN_TENTATIVAS_REVISAO=3` respostas (antes é
  "explorável", `vencimento=None`, fora das filas das Estatísticas);
  `CAP_REVISOES_SESSAO=5` (cap do subgrupo due em `vencidos()`);
  `desired_retention=0.87`; `learning_steps`/`relearning_steps = 1 dia`;
  parâmetros FSRS-6 padrão. Um único `Scheduler` via `make_scheduler()`
  compartilhado por `fsrs.py` e `app/estatisticas.py` (R consistente).
  `revisar()` retorna `{"vencimento": None, "estado": "exploracao"}` para
  temas sub-portão (guardar no app — `vencimento.isoformat()` estoura).
  Colunas de `fsrs_estados`/`niveis_usuarios` são a fonte do portão (JOIN com
  `n.contagem >= MIN` em `resumo`/`revisoes`/`retencao`).
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
  curado) e `data/json/*_questoes.json` + `*_imagens.json` (dados extraídos e
  validados). De resto, só código, docs e `tools/` são versionados. As páginas
  `data/paginas/` NÃO são versionadas (são derivadas dos PDFs, regeráveis via
  `render_pages.py`) — nunca commitá-las.
- Não commitar a chave de API nem expor `figuras` recortadas de provas
  (`data/imagens/`) fora do repo; `data/paginas/` também não vai ao git.

## Estrutura

```
docs/              # base do projeto + plano de ação
src/               # pipeline Python puro (download, extract, parse, db, ia/, estudo)
app/               # Streamlit (interface de estudo) — study.py + panzoom.py
tools/gemini/      # toolkit validado: extração via Gemini (Dockerfile, extract/validate/repair/gabfix/fix_paginas/run_all)
data/              # QUASE NÃO VERSIONADA: pdfs, json/ e paginas/ (exceção:
                   # assuntos.json e data/json/*_questoes.json + *_imagens.json
                   # versionados), imagens/, vestibular.db
tmp/               # NÃO VERSIONADA: rascunhos/scratch (gabaritos extraídos p/ conferência)
scripts/           # CLI (click): ingest, classify, score (esqueleto)
```

## Schema (SQLite — motor de estudo em `src/vestibular/estudo/`)

Tabelas implementadas no motor: `vestibulares`, `questoes`, `classificacoes`,
`dificuldades` (score IA — ainda sem seed), `item_params` (b Rasch),
`fsrs_estados`, `habilidades` (θ MAP por área), `niveis_usuarios` (score/racha/
contagem por `(usuario, tema)`) e `tentativas` (caderno de erros: colunas
nullable `grau_certeza` `conviccao|duvida|chute`, `causa_erro`
`teoria|pegadinha|atencao` e `sintese_ativa`, preenchidas via `motiva.responder`
(certeza) + `motiva.anotar_erro`/MCP `anotar_erro` (causa/síntese pós-conferência);
acertos convictos e registros antigos ficam com NULL, sem afetar TRI/FSRS).
Pendente do plano original:
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
- Pontuar dificuldade via IA (passada 2 router, texto puro → seed no DB; os
  JSONs versionados guardam `respostas_ia`/`score_ia`/`bbox_questao`, então num
  deploy do zero basta o `seed`, sem chamadas novas). Questões com imagem são
  ignoradas por padrão (`--com-imagem` liga a pontuação delas):
  ```bash
  docker run --rm --network host -v "$PWD":/work -w /work \
    -e DB_PATH=data/vestibular.db \
    -e MODEL_TEXT="hy3,mimo-v2.5" -e TEMPERATURE=0.7 \
    -e MAX_TENTATIVAS=4 -e CONCURRENCY=8 \
    vestibular-app:latest python tools/gemini/score_dificuldade.py \
      passada2 fuvest_2024   # ou seed / status / smoke / qa
  ```
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
- Limpar círculos verdes de "provas resolvidas" (fonte Curso Objetivo — ENEM
  2011–2014 tinham o círculo marcando a alternativa correta; inviabilizava o
  estudo): `tools/gemini/limpar_circulos.py [labels...]` remove só o traço
  verde ao nível vetorial (Forms XObject esvaziados em 2012–2014; ops de traço
  removidas do conteúdo das páginas em 2011). Verifica 0 círculos/pixels verdes
  residuais e texto idêntico antes/depois. `--check` reporta sem escrever;
  `--out DIR` grava em outro diretório; `--render` re-renderiza
  `data/paginas/<label>/`. Padrão: limpa no lugar com backup em
  `tmp/bkp_pdfs/`. Rodar com a imagem do app:
  `docker run --rm -v "$PWD":/work -w /work vestibular-app:latest \
  python tools/gemini/limpar_circulos.py enem_2013_1dia enem_2013_2dia`
- Lint/format (quando adicionado): `ruff check .` e `ruff format .`.

## Convenções

- Python 3.11+, tipagem leve quando útil, sem comentários redundantes.
- Não criar arquivos fora do plano (docs/*.md) sem necessidade.
- Mudanças de arquitetura passam pelos docs primeiro; seguir o schema e as
  decisões de `docs/base-do-projeto.md`.
