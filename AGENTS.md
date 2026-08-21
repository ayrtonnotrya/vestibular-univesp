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

## Pipeline de extração IA (validado — UNIVESP 2017–2024)

Fluxo implementado em `tools/gemini/` (Python, roda via `gemini-runner`):

1. **upload**: `client.files.upload(pdf)` para o caderno de questões e o gabarito.
2. **extract**: `extract.py <label>` monta o prompt (regras de transcrição +
   catálogo `data/assuntos.json` + schema JSON) e chama
   `models/<modelo>:generateContent` com `file_data.file_uri`. Uma chamada por
   exame (~1–2,5 min), com retry/backoff em 429/500/503.
3. **saídas**:
   - `data/json/univesp_<label>_questoes.json` — schema completo (ver
     `docs/base-do-projeto.md` §6).
   - `data/json/univesp_<label>_imagens.json` — bbox das figuras/grandes
     imagens (`pagina`, `tipo`, `elemento`, `bbox` em % da página).
4. **validate**: `validate.py` confere gabarito contra o PDF oficial, cobertura
   sequencial, schema e strings de área/assunto contra o catálogo.
5. **repair**: `repair.py` casa assuntos divergentes com a string EXATA do
   catálogo (usar quando `validate.py` acusar "assunto fora do catálogo").

Resultado já extraído e validado: 9 exames, 525 questões (516 objetivas +
9 redações), gabaritos 100% conferidos. Atualizar a validade ao adicionar exames.

## Regras do ambiente

- Rodar comandos com `docker run`. Exemplo de extração completa:
  ```bash
  docker run --rm -w /work/tools/gemini \
    -e API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)" \
    -e MODEL=gemini-3.5-flash-lite \
    -v "$PWD":/work gemini-runner \
    python run_all.py univesp_2025
  ```
- Nunca commitar segredos, PDFs ou dados (`.env`, `data/`, `tmp/` são
  ignorados). Nada é versionado exceto código, docs e `tools/`.
- Não commitar a chave de API nem expor `figuras` de provas fora do repo.

## Estrutura

```
docs/              # base do projeto + plano de ação
src/               # pipeline Python puro (download, extract, parse, db, ia/, estudo)
app/               # Streamlit (interface de estudo — Fase 2)
tools/gemini/      # toolkit validado: extração via Gemini (Dockerfile, extract/validate/repair/run_all)
data/              # NÃO VERSIONADA: pdfs, json/, imagens/, gabaritos, vestibular.db
tmp/               # NÃO VERSIONADA: rascunhos/scratch (gabaritos extraídos p/ conferência)
scripts/           # CLI (click): ingest, classify, score (esqueleto)
```

## Schema (SQLite — planejado em docs/base-do-projeto.md)

Tabelas previstas: `vestibulares`, `questoes`, `classificacoes`, `dificuldades`,
`niveis_usuarios`, `tentativas` (ainda não implementadas).

## Como rodar / verificar

- Extração de um exame (`label` = `univesp_2021`, `univesp_2018_1s`, ...):
  `tools/gemini/run_all.py <labels...>`
- Validação dos JSONs: `tools/gemini/validate.py`
- Reparo dos assuntos: `tools/gemini/repair.py`
- App de estudo (Fase 2): `streamlit run app/app.py` (esqueleto).
- Lint/format (quando adicionado): `ruff check .` e `ruff format .`.

## Convenções

- Python 3.11+, tipagem leve quando útil, sem comentários redundantes.
- Não criar arquivos fora do plano (docs/*.md) sem necessidade.
- Mudanças de arquitetura passam pelos docs primeiro; seguir o schema e as
  decisões de `docs/base-do-projeto.md`.