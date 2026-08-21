# AGENTS.md

Guia para agentes de IA trabalhando neste repositório.

## Projeto

Mineração de questões dos vestibulares USP (FUVEST), UNESP, UNICAMP e UNIVESP
(alvo principal): baixar PDFs, OCR/extrair texto e imagens, particionar em
questões, armazenar em SQLite, classificar (área/tema) e pontuar dificuldade
via IA (function calling), e disponibilizar estudo adaptativo numa interface
Streamlit.

Documentação de referência:
- `docs/base-do-projeto.md` — arquitetura, schema, pastas, uso de IA, riscos.
- `docs/plano-de-acao.md` — execução por fases, critérios de go/no-go.

## Regras do ambiente

- Rodar comandos com `uv run` (ou `python`) a partir da raiz do repo.
- Não commitar nunca segredos, PDFs ou dados (`.env`, `data/` são ignorados).
- Nada é versonado exceto código e docs; `data/` é não versionada.

## Arquitetura (resumo)

- **Pipeline em Python puro** + SQLite. Sem framework web na ingesta.
- **Interface:** Streamlit em `app/` (lê o SQLite; não é o pipeline).
- **IA via function calling** para classificar / pontuar / dar feedback.
- PDFs brutos: `data/<vestibular>_<ano>_<caderno>.pdf` (sem subpastas por matéria).

## Estrutura

```
docs/            # base do projeto + plano de ação
src/             # downloader, extractor, parser, db, ia/, estudo
app/             # Streamlit (interface de estudo)
data/            # NÃO VERSIONADA: pdfs, json/, imagens/, vestibular.db
scripts/         # CLI (click): ingest, classify, score
```

## Schema (SQLite — detalhes em docs/base-do-projeto.md)

Tabelas: `vestibulares`, `questoes`, `classificacoes`, `dificuldades`,
`niveis_usuarios`, `tentativas`.

## Como rodar / verificar

- CLI de ingesta: `scripts/` (click). Comandos previstos: `ingest`, `classify`,
  `score` (ainda a implementar — fase 1 do plano).
- App de estudo: `streamlit run app/app.py` (fase 2).
- Rodar lint/format: `ruff check .` e `ruff format .` (quando adicionado).

## Convenções

- Python 3.11+, tipagem leve quando útil, sem comentários redundantes.
- Não criar arquivos fora do plano (docs/*.md) sem necessidade.
- Seguir o schema e as decisões dos docs; mudanças de arquitetura passam pelos
  docs primeiro.
