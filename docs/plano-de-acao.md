# Plano de Ação — Projeto de Mineração de Questões de Vestibular

Plano executável, faseado, para sair do zero até o sistema de estudo
funcionando. Partimos do piloto (**UNIVESP + 1 FUVEST**) para validar o
pipeline antes de escalar para os 4 vestibulares.

**Princípios**
- Pipelines Python puro + SQLite. Nada de framework web na ingesta.
- `data/` e `tmp/` não versionadas; código, docs e `tools/` versionados.
- IA via **function calling** para classificar / pontuar / dar feedback — e
  via **Gemini multimodal** para a extração (validado).
- Validar cada fase com dados reais antes de avançar.

---

## Fase 1 — Prova de Conceito (pipeline de ingesta)

**Meta:** extrair e ingerir provas reais (UNIVESP primeiro) e validar
acurácia de extração/classificação.

### 1.1 Esqueleto do projeto
- [x] `pyproject.toml` + `src/` + `scripts/` (click) + `app/` (esqueleto)
- [x] `tools/gemini/`: toolkit de extração via IA (Dockerfile, scripts)
- [x] `.env.example` documentando `API_KEY`; `.env` local não versionado

### 1.2 downloader
- [ ] Scraper determinístico das páginas oficiais (UNIVESP primeiro; hoje os
      PDFs são colocados manualmente em `data/`)
- [ ] Fallback: agente de IA para descobrir URL quando o padrão mudar
- [ ] Salvar em `data/univesp_<label>_<caderno>.pdf`

### 1.3 extração (PDF → questões) — ✅ VALIDADO via Gemini
- [x] Leitura nativa dos PDFs pelo modelo (`client.files.upload` +
      `generateContent`, `responseMimeType=application/json`)
- [x] Transcrição integral: enunciado, textos de apoio, alternativas a–e,
      gabarito (do PDF oficial), redação como `tipo: "redacao"`
- [x] Classificação prévia (áreas canônicas + assuntos do catálogo)
- [x] Coordenadas (`bbox`) de figuras/gráficos/tabelas → `*_imagens.json`
- [x] Saída: `data/json/univesp_<label>_questoes.json`
- [x] **Resultado:** UNIVESP 2017–2024 — 9 exames, 525 questões (516
      objetivas + 9 redações)

### 1.4 validação e reparo — ✅ VALIDADO
- [x] `validate.py`: gabarito vs PDF oficial (100% conferido), cobertura
      sequencial, schema, strings de área/assunto vs `data/assuntos.json`
- [x] `repair.py`: casamento fuzzy de assuntos divergentes com a string
      EXATA do catálogo
- [x] Tratamento de anulações/retificações (2019_2 Q26 `ANULADA`; 2024 Q4
      `Retificada`)

### 1.5 db (SQLite)
- [ ] Aplicar schema da seção 5 do doc de base (tabelas: vestibulares,
      questoes, classificacoes, dificuldades, niveis_usuarios, tentativas)
- [ ] `import` das questões no banco a partir dos JSONs em `data/json/`
- [ ] Seed da taxonomia de temas/áreas (fechada)
- [x] Motor de estudo (`src/vestibular/estudo/`): schema aplicado (à exceção de
      `dificuldades`/score IA), `import_questoes.py`, tabelas `habilidades`,
      `item_params`, `fsrs_estados`, `niveis_usuarios` e `tentativas`

### 1.6 ia/classificar (function calling)
- [ ] Chamada estruturada: enunciado+alternativas → `{area, tema, confianca}`
- [ ] Validação contra taxonomia fechada (reaproveitar `areas/assuntos` já
      presentes nos JSONs)
- [ ] Gravar em `classificacoes`
- [ ] **Critério de aceite:** acurácia de área ≥90%; tema revisado manualmente

### 1.7 ia/dificuldade (low-thinking)
- [ ] Modelo resolve a questão N=3–5 vezes, retorna alternativa escolhida
- [ ] Compara com gabarito → `score = acertos / tentativas`
- [ ] Registrar em `dificuldades`; marcar questões ambíguas/contestadas

### 1.8 Validação da Fase 1
- [ ] Rodar pipeline de ponta a ponta em 1 prova UNIVESP + 1 FUVEST
- [ ] Relatório: nº questões ingeridas, acurácia de extração, acurácia de
      classificação, distribuição de scores
- [ ] **Decisão de go/no-go** para escalar

---

## Fase 2 — Interface de Estudo (Streamlit)

**Meta:** visualizar, responder e receber feedback de questões via navegador.

### 2.1 App mínimo — ✅ PARCIAL
- [x] `app/study.py` (Streamlit): questão em cima + página (pan/zoom) embaixo
- [x] Seleção por exame e questão
- [x] Mostrar enunciado, textos de apoio, alternativas + gabarito
- [x] Viewer **pan/zoom** da página original (enquadrada no `bbox` quando há)
- [ ] Registrar resposta em `tentativas` (SQLite)

### 2.2 Seleção adaptativa
- [x] Pegar questão com dificuldade (b em `item_params`, logit do score IA
      suavizado com tentativas reais) próxima ao nível do usuário no tema:
      nível por tema (`niveis_usuarios`) quando há ≥2 tentativas, senão θ da
      área (Rasch MAP)
- [x] Evitar questões já respondidas (ou circular)

### 2.3 Feedback ao errar
- [ ] Chamada IA (function calling/texto) gerando explicação didática
- [ ] Registrar detalhe em `tentativas.detalhe`

### 2.4 Progresso do usuário
- [x] Atualizar `niveis_usuarios` (score, racha, contagem) por tema
- [x] Tela de progresso por área/tema (θ por área + nível por tema)

### 2.5 Validação da Fase 2
- [ ] Usar de ponta a ponta com as questões da Fase 1
- [ ] Ajustar seleção/feedback conforme uso real

---

## Fase 3 — Escala (4 vestibulares, todas as edições)

**Meta:** popular o banco com o acervo completo.

### 3.1 downloader em escala
- [ ] Scrapers para FUVEST, UNESP, UNICAMP, UNIVESP
- [ ] Histórico de edições (todas as provas disponíveis publicamente)
- [ ] Controle de idempotência (não re-baixar o que já existe em `data/`)

### 3.2 Ingesta em lote
- [ ] Script de varredura: para cada PDF em `data/`, extract→validate→import
- [ ] Rastreabilidade: rodar classificação e score em lote com
      retries/rate-limit (respeitar cotas, ex.: ~15 RPM / 250 TPM)
- [ ] Tratar redação separadamente (não é questão objetiva)

### 3.3 Qualidade
- [ ] Auditoria de gabaritos (fonte oficial + validação cruzada da IA)
- [ ] Reconciliar pontuação ambígua/duplicada
- [ ] Backup do banco

---

## Fase 4 (Opcional) — Multi-usuário / Produto Web

**Meta:** somente se quiser compartilhar/publicar.

- [ ] Avaliar Django vs FastAPI+frontend (auth, painel admin)
- [ ] Migrar SQLite → Postgres se necessário
- [ ] Pipeline de ingesta permanece intacto (independência confirmada)

---

## Critérios de prioridade e go/no-go

| Fase | Go/no-go | Critério |
| --- | --- | --- |
| 1 | Go (extração) | extração UNIVESP validada: gabaritos 100%, cobertura completa |
| 1 | Go (restante) | SQLite + classificação área ≥90% no piloto |
| 2 | Go | uso funciona de ponta a ponta com questões da F1 |
| 3 | Go | piloto validado; custo/rate-limit de IA aceitável |
| 4 | Go | desejo real de multi-usuário/product |

---

## Riscos a vigiar (detalhados na seção 9 do doc de base)

1. OCR/degradar figuras → IA multimodal lê PDF; `bbox` guardado p/ recorte.
2. `pdftotext` quebra layout dos cadernos → extração via Gemini; poppler só
   para gabaritos.
3. Modelo de CLI não aceita anexo PDF → Gemini via API (upload), nunca
   depender do anexo nativo do agente.
4. Rate-limit de IA (3.7-flash bateu em ~15 RPM/250 TPM) → usar
   `gemini-3.5-flash-lite`, 1 chamada por exame, retry/backoff.
5. Modelo abrevia strings do catálogo → `repair.py` + `validate.py`.
6. Gabarito anulado/retificado → tratar `anulada` e gabarito final.
7. Sites mudam estrutura → scraper determinístico + agente fallback.

---

## Próximos passos imediatos

1. Importar os JSONs de `data/json/` para o SQLite (seção 1.5).
2. Adicionar FUVEST ao acervo e rodar a extração (1.3–1.4).
3. Classificação final via function calling (1.6) reaproveitando
   `areas/assuntos` dos JSONs.
4. Evoluir o app (2.1 já funcional) para seleção adaptativa (2.2) e
   feedback da IA (2.3).

> **Nota (sessão atual):** a exibição de figuras foi trocada de auto-recorte
> para **viewer pan/zoom da página inteira** (ver `docs/base-do-projeto.md`
> §6.5). O app mostra a página enquadrada no `bbox` da figura; questões sem
> mídia mostram a página localizada por `question_page()`.
