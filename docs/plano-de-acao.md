# Plano de Ação — Projeto de Mineração de Questões de Vestibular

Plano executável, faseado, para sair do zero até o sistema de estudo
funcionando. Partimos do piloto (**UNIVESP + 1 FUVEST**) para validar o
pipeline antes de escalar para os 4 vestibulares.

**Princípios**
- Pipelines Python puro + SQLite. Nada de framework web na ingesta.
- `data/` não versionada; código versionado.
- IA via **function calling** para classificar / pontuar / dar feedback.
- Validar cada fase com dados reais antes de avançar.

---

## Fase 1 — Prova de Conceito (pipeline de ingesta)

**Meta:** ingerir 1 prova real (UNIVESP) + 1 FUVEST no SQLite e validar
acurácia de parse/OCR/classificação.

### 1.1 Esqueleto do projeto
- [ ] `pyproject.toml` (Python 3.11+, deps: pymupdf, sqlite3 stdlib, click,
      httpx, bs4; futuro: paddleocr, langchain/llm client)
- [ ] Criar `src/` com módulos: `downloader`, `extractor`, `parser`, `db`,
      `ia/` (subpastas `classificar`, `dificuldade`, `feedback`), `estudo`
- [ ] CLI em `scripts/` (click) com comandos vazios: `ingest`, `classify`,
      `score`
- [ ] Docker: `Dockerfile` e `docker-compose.yml` (já criados) passam a
      buildar quando `src/` e `app/` existirem; duplicar `.env` a partir de
      `.env.example`

### 1.2 downloader
- [ ] Scraper determinístico das páginas oficiais (UNIVESP primeiro — alvo)
- [ ] Fallback: agente de IA para descobrir URL quando o padrão mudar
- [ ] Salvar em `data/<vestibular>_<ano>_<caderno>.pdf`

### 1.3 extractor (PDF → texto + imagens)
- [ ] PyMuPDF: extrair texto por página e imagens embutidas por página
- [ ] Detectar PDF escaneado (pouco/nenhum texto) → acionar OCR (PaddleOCR)
- [ ] Salvar imagens em `data/imagens/` e mapear página→imagem

### 1.4 parser (texto → questões)
- [ ] Detectar numeração/enunciado e alternativas a–e
- [ ] Vincular figuras da página à questão correspondente
- [ ] Extrair gabarito quando presente no mesmo/caderno de gabarito
- [ ] Escrever JSON intermediário em `data/json/` para inspeção manual
- [ ] **Critério de aceite:** ≥80% das questões de um caderno corretamente
      particionadas (verificação manual de amostra)

### 1.5 db (SQLite)
- [ ] Aplicar schema da seção 5 do doc de base (tabelas: vestibulares,
      questoes, classificacoes, dificuldades, niveis_usuarios, tentativas)
- [ ] `import` das questões no banco a partir dos JSONs do parser
- [ ] Seed da taxonomia de temas/áreas (fechada)

### 1.6 ia/classificar (function calling)
- [ ] Chamada estruturada: enunciado+alternativas → `{area, tema, confianca}`
- [ ] Validação contra taxonomia fechada
- [ ] Gravar em `classificacoes`
- [ ] **Critério de aceite:** acurácia de área ≥90%; tema revisado manualmente

### 1.7 ia/dificuldade (low-thinking)
- [ ] Modelo resolve a questão N=3–5 vezes, retorna alternativa escolhida
- [ ] Compara com gabarito → `score = acertos / tentativas`
- [ ] Registrar em `dificuldades`; marcar questões ambíguas/contestadas

### 1.8 Validação da Fase 1
- [ ] Rodar pipeline de ponta a ponta em 1 prova UNIVESP + 1 FUVEST
- [ ] Relatório: nº questões ingeridas, acurácia parse, OCR (se houve),
      acurácia classificação, distribuição de scores
- [ ] **Decisão de go/no-go** para escalar

---

## Fase 2 — Interface de Estudo (Streamlit)

**Meta:** visualizar, responder e receber feedback de questões via navegador.

### 2.1 App mínimo
- [ ] `app/app.py` (Streamlit)
- [ ] Seleção por área/tema
- [ ] Renderizar enunciado + imagens (`st.image`) + alternativas clicáveis
- [ ] Registrar resposta em `tentativas`

### 2.2 Seleção adaptativa
- [ ] Pegar questão com dificuldade (~score em `dificuldades`) próxima ao
      nível do usuário no tema (`niveis_usuarios`)
- [ ] Evitar questões já respondidas (ou circular)

### 2.3 Feedback ao errar
- [ ] Chamada IA (function calling/texto) gerando explicação didática
- [ ] Registrar detalhe em `tentativas.detalhe`

### 2.4 Progresso do usuário
- [ ] Atualizar `niveis_usuarios` (score, racha, contagem) por tema
- [ ] Tela de progresso por área/tema

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
- [ ] Script de varredura: para cada PDF em `data/`, extract→parse→import
- [ ] Rastreabilidade: rodar classificação e score em lote com retries/rate-limit
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
| 1 | Go | parse ≥80%, classificação área ≥90% no piloto |
| 2 | Go | uso funciona de ponta a ponta com questões da F1 |
| 3 | Go | piloto validado; custo/rate-limit de IA aceitável |
| 4 | Go | desejo real de multi-usuário/product |

---

## Riscos a vigiar (detalhados na seção 9 do doc de base)

1. OCR de gráficos degrada a questão → sempre exibir figura junto ao texto.
2. Gabarito UNIVESP pode vir ausente/inesperado → fonte extra/validação IA.
3. Score low-thinking ≠ dificuldade humana → calibrar com tentativas reais.
4. Custo/rate-limit de IA → começar pequeno, medir.
5. Sites mudam estrutura → scraper determinístico + agente fallback.

---

## Próximos passos imediatos

1. Criar `pyproject.toml` e esqueleto `src/` + `scripts/` (1.1).
2. Obter um PDF real da UNIVESP em `data/`.
3. Implementar `extract` + `parse` (1.3–1.4) e validar com esse PDF.
4. Aplicar schema e importar (1.5).
