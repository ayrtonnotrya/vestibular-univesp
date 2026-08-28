# Base do Projeto — Mineração de Questões de Vestibular

Projeto pessoal para mineração, classificação, ranqueamento e estudo
adaptativo de questões dos vestibulares **USP (FUVEST)**, **UNESP**,
**UNICAMP** e **UNIVESP** (alvo principal).

---

## 1. Objetivo

Construir um pipeline que:

1. Baixa os PDFs oficiais das provas.
2. Extrai o texto e as imagens importantes de cada questão (IA quando
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
- **Extração (VALIDADO):** IA multimodal (Gemini) lê os PDFs nativamente e
  retorna o JSON das questões estruturado (substitui OCR/PyMuPDF-parser na
  prática atual — ver §6). `PyMuPDF`/`PaddleOCR` ficam como plano B para
  PDFs que a IA não consiga ler.
- **Classificação e score via IA com function calling** (ver §6.4 e §7).
- **SQLite** como banco inicial (ainda não implementado).

---

## 3. Escopo e vestibular de partida

- **Piloto (fase de prova de conceito):** UNIVESP + 1 prova da FUVEST.
  Validar acurácia de parse/OCR/classificação antes de escalar.
- **Estado atual:** extração **UNIVESP 2017–2024 completa e validada**
  (9 exames, 525 questões: 516 objetivas + 9 redações).
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
data/univesp_<label>_questoes.pdf
data/univesp_<label>_gabarito.pdf
```

Exemplos reais de `label` no acervo UNIVESP: `2017_2s`, `2018_1s`, `2018_2s`,
`2019_2`, `2020`, `2021`, `2022`, `2023`, `2024`.

### 4.2. Estrutura geral do repositório

```
vestibular-univesp/
  docs/                    # documentação (este arquivo)
  src/
    downloader/            # baixa PDFs (scraper determinístico + fallback IA)
    extractor/             # PDF -> texto + imagens (PyMuPDF / OCR) — plano B
    parser/                # texto -> questões individuais (JSON estruturado)
    db/                    # schema, conexão, queries (SQLite)
    ia/
      classificar/         # function calling: area/tema
      dificuldade/         # score empírico via low-thinking
      feedback/            # explicação ao errar
    estudo/                # seleção adaptativa + progresso do usuário
  tools/
    gemini/                # VALIDADO: extração via Gemini (Dockerfile, extract/run_all/validate/repair)
  app/                     # Streamlit (interface de estudo)
  data/                    # NÃO VERSIONADA: conteúdo bruto e intermediário
    *.pdf                  # PDFs brutos, nome univesp_<label>_(questoes|gabarito).pdf
    json/                  # SAÍDA da extração (questoes + imagens por exame)
    imagens/               # (a gerar) figuras recortadas por questão
    vestibular.db          # banco SQLite (a criar)
  scripts/                 # CLI (click): ingere, classifica, pontua (esqueleto)
  tmp/                     # NÃO VERSIONADA: scratch, txt de conferência
```

---

## 5. Modelo de dados (SQLite — planejado)

```sql
vestibulares(id, nome)                    -- univesp, fuvest, unesp, unicamp

questoes(
  id PK,
  vestibular_id FK,
  ano,
  materia,                -- derivada da classificação (área)
  enunciado,              -- texto (após extração)
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
  tema_id FK temas,
  score,                  -- média ponderada de acertos (0..1)
  racha,                  -- sequência atual de acertos
  contagem,               -- qtd_tentativas
  ultima_data
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

Implementação parcial — o acervo vive nos JSONs de `data/json/` (importado para
SQLite) e o motor de estudo em `src/vestibular/estudo/` já cria/usa
`vestibulares`, `questoes`, `classificacoes`, `niveis_usuarios`, `tentativas`,
`habilidades`, `item_params` e `fsrs_estados`. Pendente: `ia/classificar`,
`ia/dificuldade` (score) e `ia/feedback` (Fase 1).

---

## 6. Pipeline de ingesta

### 6.1. O QUE FUNCIONA HOJE (validado — UNIVESP 2017–2024)

A extração é feita por **IA multimodal (Gemini)** lendo os PDFs nativamente.
O modelo recebe o caderno de questões + o gabarito oficial (upload via
`client.files.upload`) e transcreve/estrutura tudo em uma chamada por exame,
com saída JSON forçada (`responseMimeType: application/json`).

Fluxo (scripts em `tools/gemini/`, executados via Docker):

1. **extract** (`extract.py <label>`): prompt com regras de transcrição +
   catálogo (`data/assuntos.json`) + schema; uma chamada
   `models/<modelo>:generateContent` por exame (~1–2,5 min), com
   retry/backoff em 429/500/503.
2. **saídas:**
   - `data/json/univesp_<label>_questoes.json` (schema §6.2);
   - `data/json/univesp_<label>_imagens.json` (coordenadas §6.3).
3. **validate** (`validate.py`): confere gabarito vs PDF oficial, cobertura
   sequencial, schema e strings de área/assunto vs catálogo.
4. **repair** (`repair.py`): casa assuntos divergentes com a string EXATA do
   catálogo (necessário porque o modelo abrevia strings).

**Resultado:** 9 exames, 525 questões, gabaritos 100% conferidos.

### 6.2. Schema do JSON de questões (`data/json/univesp_<label>_questoes.json`)

```json
{
  "exame": "univesp_2021_questoes",
  "ano": 2021,
  "semestre": 2,
  "fonte_questoes": "data/univesp_2021_questoes.pdf",
  "fonte_gabarito": "data/univesp_2021_gabarito.pdf",
  "total_questoes": 57,
  "questoes": [
    {
      "numero": 1,
      "tipo": "objetiva",
      "enunciado": "Transcrição integral do enunciado, fórmulas em unicode.",
      "textos_de_apoio": ["Texto de apoio/motivador, se houver."],
      "midia": ["Página N: descrição objetiva de figura/gráfico/tabela, se houver."],
      "alternativas": {"a": "...", "b": "...", "c": "...", "d": "...", "e": "..."},
      "gabarito": "c",
      "areas": [
        {"area": "Física", "assuntos": ["...", "..."]},
        {"area": "Matemática", "assuntos": ["..."]}
      ],
      "extraida_parcialmente": false,
      "anulada": false
    }
  ]
}
```

Regras de transcrição (rigorosas):
- Enunciado fiel, sem resumir/corrigir. Fórmulas em unicode (`x²`, `√2`, `π`,
  `Δ`, `10⁻³`, frações `a/b`).
- Textos de apoio/citações/coletâneas **íntegros** em `textos_de_apoio`.
- Figuras/gráficos/tabelas/cartuns descritos objetivamente em `midia`
  (precedidos de "Página N:").
- Alternativas na ordem com a letra; letra ilegível → `[ilegivel]` em
  `extraida_parcialmente: true`.
- Redação → `tipo: "redacao"`, `gabarito: null`, `alternativas: null`.
- Anulação oficial → `anulada: true` com `gabarito: null` (ex.: 2019_2 Q26);
  questão retificada mantém o gabarito final (ex.: 2024 Q4 "D - Retificada").

### 6.3. Coordenadas de imagens (`data/json/univesp_<label>_imagens.json`)

O modelo também informa onde cada figura/gráfico/tabela/cartum aparece, para
recorte/exibição e o app de estudo (ver §6.5):

```json
{
  "exame": "univesp_2021",
  "figuras_coordenadas": {
    "4": [
      {"pagina": 6, "tipo": "grafico", "elemento": "descrição curta",
       "bbox": [210, 260, 440, 780]}
    ]
  }
}
```

> **Atenção (descoberta nesta sessão):** o `bbox` **não** é `[x0,y0,x1,y1]`
> em percentual 0–100 como o prompt original pediu. O modelo gravou na prática
> `[y0, x0, y1, x1]` em **escala 0–1000** (permil da dimensão da página).
> Ou seja:
>   `x0 = bbox[1]/1000 * W ; y0 = bbox[0]/1000 * H ; x1 = bbox[3]/1000 * W ; y1 = bbox[2]/1000 * H`
>
> Origem no canto superior esquerdo. Ex.: `bbox=[210,260,440,780]` em página
> 581×751pt → região x≈151–453, y≈158–330 (o gráfico Q1 da 2024).
>
> Esse formato é o consumido por `app/panzoom.py` e
> `tools/gemini/extract_images.py`. Não converter como 0–100.

Acervo atual: 159 figuras mapeadas nos 9 exames (~1 a 20 por exame). A página
de cada questão (com ou sem mídia) é gravada no campo `pagina` de
`*_questoes.json` por `tools/gemini/fix_paginas.py` (fonte principal; o app não
lê PDF em runtime).

### 6.4. Próximos passos (a implementar)

1. **import**: gravar os JSONs no SQLite (`questoes`).
2. **classificar**: IA via function calling retorna `{area, tema, confianca}`
   a partir da taxonomia fechada (a classificação já vem prévia nos JSONs via
   `areas`/`assuntos` — reaproveitar/validar).
3. **pontuar**: IA low-thinking tenta resolver a questão N vezes (N≈3–5);
   `score = acertos / tentativas`. Marca questões com gabarito ambíguo.

### 6.5. App de estudo e viewer pan/zoom (implementado)

**Decisão desta sessão:** trocar o auto-recorte das figuras por **exibição da
página inteira em pan/zoom**, com a figura já enquadrada quando há `bbox`. É
mais robusto (não depende de recorte preciso) e permite ao usuário enquadrar/se
aproximar como quiser.

- `app/panzoom.py` — viewer pan/zoom da página:
  - Exibe a página a partir do **JPEG pré-renderizado**
    (`data/paginas/<label>/p<NNN>.jpg`), com o resultado em base64 embutido num
    `<div>` HTML (`st.iframe`), usando `transform: translate+scale`.
  - **Arrastar** para mover (listeners de mouse no `window`; `<img draggable="false">`
    impede o drag nativo que antes travava).
  - **Zoom** pela roda do mouse, duplo-clique e botões `+`/`−`.
  - Botões "**Página inteira**" (fit) e "**Enquadrar questão**" (volta ao bbox).
- `app/study.py` — interface de estudo, em um **layout único** para todas as
  questões:
  1. **Questão** (em cima): enunciado, textos de apoio, alternativas
     (`st.radio` + botão Responder) com gabarito.
  2. **Página** (embaixo): viewer pan/zoom; enquadrado no `bbox` se houver,
     página inteira caso contrário.
  - Página resolvida por: campo `pagina` do `_questoes.json` (gravado por
    `tools/gemini/fix_paginas.py`) → página da figura (`bbox`) → `"Página N:"`
    na `midia` → interpolação pelas páginas conhecidas. **O app não lê PDF em
    runtime.**

**Como rodar:** `docker compose up vestibular-app` (porta 8501; já na rede
`web` do nginx-proxy-manager). O serviço usa `app/study.py` como comando.

### 6.6. Auto-recorte (utilitário opcional)

`tools/gemini/extract_images.py` recorta as figuras (raster via
`get_image_info`, vetorial via cluster de `get_drawings`, expandindo com texto
vizinho para não cortar títulos/rótulos). Saída em `data/imagens/`. **Não é
usado pelo app** (que usa pan/zoom), mas serve para gerar os PNGs recortados em
lote quando necessário.

---

## 7. Uso de IA

- **Extração (validado):** Gemini multimodal lê PDFs e devolve JSON estruturado
  (`responseMimeType=application/json`). Modelo usado: `gemini-3.5-flash-lite`
  (`gemini-3.7-flash` caiu em rate-limit ~15 RPM/250 TPM nesta conta).
- **classificar**: entrada = enunciado + alternativas; saída JSON
  `{area, tema, confianca}`. Taxonomia fechada evita temas inconsistentes.
- **dificuldade**: o modelo resolve "low thinking" e retorna a alternativa
  escolhida; comparada ao gabarito. Repetido N vezes para robustez.
- **feedback**: ao usuário errar, gera explicação didática (texto).

> **Cuidado:** o score da IA é uma *estimativa* de dificuldade, não verdade
> absoluta. Deve ser calibrado com tentativas reais do usuário ao longo do
> tempo (níveis por tema em `niveis_usuarios`).

---

## 8. Interface de estudo (Streamlit)

- **Estado atual (implementado):** três modos no app (Streamlit):
  - *Explorar*: visualização a partir dos JSONs, com página em viewer pan/zoom.
  - *Estudar* (adaptativo via `src/vestibular/estudo/` no SQLite): o pool de
    candidatos é o **catálogo inteiro** de temas (sem portão FSRS) e o sorteio é
    ponderado por prioridade = 0,4·frequência real nas provas UNIVESP +
    0,4·fraqueza + 0,2·exploração (inverso das observações). A fraqueza usa
    `1 − score` por tema quando `contagem >= MIN_TENTATIVAS_REVISAO` (3); abaixo
    do portão usa `1 − sigmoid(θ da área)` (estimativa estável, sem oscilar a
    cada resposta). O seletor Rasch escolhe a questão pelo nível do usuário — por
    tema quando há dados, senão por área — e a resposta recalibra FSRS/θ/b/nível
    por tema.
  - *Revisão*: fila dedicada dos temas **vencidos pelo FSRS** (portão de
    contagem + cap de `CAP_REVISOES_SESSAO` por sessão) com questão **já vista**
    — pendências do caderno de erros (última resposta errada ou dúvida/chute)
    primeiro, depois acertos antigos; **nunca** questões inéditas.
- **Política do FSRS por tema** (não é flashcards): tema só ganha card com
  `MIN_TENTATIVAS_REVISAO` (3) respostas (antes permanece "explorável", com
  `vencimento=None` e fora das filas de vencidos das Estatísticas); o passo de
  aprendizagem é de **1 dia** (`learning_steps`/`relearning_steps`), não
  minutos; `desired_retention=0.87`; parâmetros FSRS-6 padrão (sem calibrar).
  Config central em `src/vestibular/estudo/fsrs_config.py` (um único
  `Scheduler` compartilhado por `fsrs.py` e `app/estatisticas.py`).
- **Próximo passo:**
  - `ia/dificuldade` (score low-thinking) para semear `item_params.b` dos itens
    ainda sem `b` da IA.
  - **feedback** da IA ao errar, gravado em `tentativas.detalhe`.
  - Expor o motor via **MCP** para tutoria em assistente (AnythingLLM)
    (`proxima_revisao` na família tutor).

---

## 9. Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| OCR/extração de gráficos degrada a questão | IA multimodal lê PDF nativamente; guardar `bbox` das figuras (`imagens.json`) para recorte/exibição |
| `pdftotext` quebra o layout dos cadernos | Não usar; extrair via Gemini. `pdftotext -layout` só nos gabaritos (conferência) |
| Modelo de CLI/agente não aceita PDF | Usar Gemini via API (upload de arquivo); jamais depender do anexo nativo do agente |
| Rate-limit/cota de IA (ex.: ~15 RPM / 250 TPM) | Retry/backoff; modelo leve `gemini-3.5-flash-lite`; 1 chamada por exame |
| Modelo abrevia/adapta strings do catálogo | `repair.py` (fuzzy) + `validate.py` contra `data/assuntos.json` |
| Gabarito ausente/inesperado (ex.: ANULADA/Retificada) | Ler gabarito oficial junto na chamada; tratar `anulada`/retificação |
| Score low-thinking ≠ dificuldade humana | Usar como estimativa; calibrar com tentativas reais |
| Custo de IA em larga escala | Rodar piloto pequeno antes de escalar |
| Sites mudam estrutura | Scraper determinístico + agente IA como fallback |

---

## 10. Fases e status

- **Fase 1 — Prova de conceito:**
  - [x] Extração IA UNIVESP 2017–2024 (9 exames, 525 questões; gabaritos
        100% conferidos; validação e reparo de catálogo automatizados).
  - [ ] Import para SQLite; classificações/score (function calling).
- **Fase 2 — Estudo (parcial):**
  - [x] App Streamlit que mostra a questão + página em **pan/zoom** (ver §6.5).
  - [ ] Seleção adaptativa por tema/nível + feedback da IA + `tentativas`.
- **Fase 3 — Escala:** 4 vestibulares, todas as edições, classificação e score
  em lote.
- **Fase 4 (opcional):** multi-usuário / Django somente se virar produto.
