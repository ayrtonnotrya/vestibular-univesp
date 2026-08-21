# Base do Projeto — Mineração de Questões de Vestibular

Projeto pessoal para mineração, classificação, ranqueamento e estudo
adaptativo de questões dos vestibulares **USP (FUVEST)**, **UNESP**,
**UNICAMP** e **UNIVESP** (alvo principal).

---

## 1. Objetivo

Construir um pipeline que:

1. Baixa os PDFs oficiais das provas.
2. Extrai o texto e as imagens importantes de cada questão (OCR quando
   necessário).
3. Quebra cada prova em questões individuais e as armazena em um banco de
   dados.
4. Classifica cada questão em **área** e **tema** usando IA (function calling),
   a partir de uma taxonomia fechada.
5. Atribui uma **dificuldade empírica** a cada questão, usando IA "low
   thinking": o modelo tenta resolver várias vezes e o percentual de acerto
   vira o score da questão.
6. Alimenta um **app de estudo** que, dado o tema e o nível do usuário naquele
   tema, oferece a questão certa, dá feedback ao errar e registra tentativas
   para calcular o score do usuário por assunto.

---

## 2. Decisões de arquitetura (resumo do planejamento)

- **Pipeline em Python puro** (CLI + SQLite), independente de qualquer
  framework web.
- **Interface de estudo em Streamlit** (Python puro, renderiza imagens com
  alta qualidade no navegador). Migrar para Django só se virar multi-usuário /
  produto web.
- **OCR**: PyMuPDF para PDFs textuais; PaddleOCR/EasyOCR para PDFs escaneados.
- **Classificação e score via IA com function calling** (ver §6 e §7).
- **SQLite** como banco inicial.

---

## 3. Escopo e vestibular de partida

- **Piloto (fase de prova de conceito):** UNIVESP + 1 prova da FUVEST.
  Validar acurácia de parse/OCR/classificação antes de escalar.
- **Meta final:** as 4 instituições, todas as edições.

---

## 4. Estrutura de pastas

### 4.1. PDFs brutos — tudo em `data/`, separação no nome do arquivo

As provas **não vêm separadas por matéria** (1–3 PDFs por prova: caderno de
questões, redação, gabarito). A separação por matéria também **não acontece no
filesystem** — acontece no nível do banco de dados (tabela `questoes` e
`classificacoes`), após o parse (§6).

Para manter simples, **não usamos subpastas granulares**. Todo o conteúdo bruto
fica direto em `data/` (que é **não versionada**), e a identificação vai embutida
no **nome do arquivo**:

```
data/
  <vestibular>_<ano>_<caderno>.pdf
```

Exemplos:

```
data/univesp_2026_questoes.pdf
data/univesp_2026_gabarito.pdf
data/fuvest_2026_questoes.pdf
data/fuvest_2026_redacao.pdf
```

Padrão de nome: `<vestibular>_<ano>_<caderno>.pdf` onde `<caderno>` é
`questoes`, `redacao`, `gabarito` etc. As **imagens extraídas** e os **JSONs do
parser** também ficam em `data/` (subpastas simples, não versionadas).

### 4.2. Estrutura geral do repositório

```
vestibular-univesp/
  docs/                    # documentação (este arquivo)
  src/
    downloader/            # baixa PDFs (scraper determinístico + fallback IA)
    extractor/             # PDF -> texto + imagens (PyMuPDF / OCR)
    parser/                # texto -> questões individuais (JSON estruturado)
    db/                    # schema, conexão, queries (SQLite)
    ia/
      classificar/         # function calling: area/tema
      dificuldade/         # score empírico via low-thinking
      feedback/            # explicação ao errar
    estudo/                # seleção adaptativa + progresso do usuário
  app/                     # Streamlit (interface de estudo)
  data/                    # NÃO VERSIONADA: conteúdo bruto e intermediário
    *.pdf                  # PDFs brutos, nome <vestibular>_<ano>_<caderno>.pdf
    json/                  # saída intermediária do parser
    imagens/               # figuras extraídas por questão
    vestibular.db          # banco SQLite
  scripts/                 # CLI (click): ingere, classifica, pontua
```

---

## 5. Modelo de dados (SQLite)

```sql
vestibulares(id, nome)                    -- univesp, fuvest, unesp, unicamp

questoes(
  id PK,
  vestibular_id FK,
  ano,
  materia,                -- derivada da classificação (área)
  enunciado,              -- texto (após OCR)
  alternativas,           -- JSON: {a,b,c,d,e}
  gabarito,               -- letra | null (se não houver oficial)
  fonte_pdf,              -- nome do PDF bruto em data/ (ex.: univesp_2026_questoes.pdf)
  pagina,
  imagens,                -- JSON: caminhos das figuras extraídas
  criado_em
)

classificacoes(
  id PK,
  questao_id FK,
  area,                   -- ex.: matematica
  tema,                   -- ex.: progressao geometrica
  confianca,              -- 0..1 retornado pela IA
  modelo,                 -- modelo que classificou
  UNIQUE(questao_id)
)

dificuldades(
  id PK,
  questao_id FK,
  tentativas_realizadas,  -- nº de execuções low-thinking
  acertos,                -- nº de acertos
  score,                  -- acertos / tentativas (0..1)
  modelo
)

niveis_usuarios(
  id PK,
  usuario,                -- nome/identificador
  tema,                   -- ex.: progressao geometrica
  score,                  -- média ponderada de acertos
  racha,                  -- recente acertos / tentativas
  qtd_tentativas
)

tentativas(
  id PK,
  usuario,
  questao_id FK,
  resposta,               -- letra escolhida
  correta,                -- bool
  data,
  detalhe                 -- JSON do feedback da IA
)
```

---

## 6. Pipeline de ingesta

Sequência por PDF:

1. **download**: salvar em `data/` com nome `<vestibular>_<ano>_<caderno>.pdf`. `data/` não é versionada.
2. **extract**: PyMuPDF extrai texto + imagens embutidas. Se sem texto
   (escaneado), rodar PaddleOCR. Figuras importantes são salvas e vinculadas
   à questão (link pelo número da página/posição).
3. **parse**: quebrar o texto em questões (detectar numeração/enunciado,
   alternativas a–e, gabarito quando presente). Saída JSON intermediária em
   `data/json/` para inspeção manual.
4. **import**: gravar no SQLite (`questoes`).
5. **classificar**: para cada questão, IA via function calling retorna
   `{area, tema, confianca}` a partir de uma **taxonomia fechada** de temas.
6. **pontuar**: IA low-thinking tenta resolver a questão N vezes (N≈3–5);
   `score = acertos / tentativas`. Marca questões com gabarito ambíguo.

---

## 7. Uso de IA (function calling)

Todos os passos de IA usam **function calling** (chamadas estruturadas), e não
texto livre, para produzir dados consistentes e graváveis.

- **classificar**: entrada = enunciado + alternativas; saída JSON
  `{area, tema, confianca}`. Taxonomia fechada evita temas inconsistentes.
- **dificuldade**: entrada = questão completa; o modelo resolve "low
  thinking" e retorna a alternativa escolhida; comparada ao gabarito.
  Repetido N vezes para robustez.
- **feedback**: ao usuário errar, entrada = enunciado + resposta do usuário +
  gabarito; retorna explicação didática (texto).

> **Cuidado:** o score da IA é uma *estimativa* de dificuldade, não verdade
> absoluta. Deve ser calibrado com tentativas reais do usuário ao longo do
> tempo (níveis por tema em `niveis_usuarios`).

---

## 8. Interface de estudo (Streamlit)

- Selecionar **tema/área**.
- O sistema pega questão com dificuldade próxima ao **nível do usuário** no
  tema (via `score` de `dificuldades` + `niveis_usuarios`).
- Renderiza **enunciado + imagens** (`st.image`) + alternativas clicáveis.
- Usuário responde → registra em `tentativas`.
- Se errou → **feedback** da IA e atualização do `niveis_usuarios`.

---

## 9. Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| OCR de gráficos/imagens degrada a questão | Guardar e exibir sempre a figura junto ao texto; validar piloto |
| Gabarito ausente/inesperado (ex.: UNIVESP) | Fonte extra ou validação cruzada da IA |
| Score low-thinking ≠ dificuldade humana | Usar como estimativa; calibrar com tentativas reais |
| Custo/rate-limit de IA em larga escala | Rodar piloto pequeno antes de escalar |
| Sites mudam estrutura | Scraper determinístico + agente IA como fallback |

---

## 10. Fases

- **Fase 1 — Prova de conceito:** pipeline Python + SQLite, ingesta de 1 prova
  real (UNIVESP) + 1 FUVEST, validar parse/OCR/classificação.
- **Fase 2 — Estudo:** app Streamlit lendo o SQLite (visualizar/responder/
  feedback).
- **Fase 3 — Escala:** 4 vestibulares, todas as edições, classificação e score
  em lote.
- **Fase 4 (opcional):** multi-usuário / Django somente se virar produto.
