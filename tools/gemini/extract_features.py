"""Extração de features de complexidade cognitiva das questões via router
OpenCode Go (modelo `hy3`, OpenAI-compatível) — mesmo plumbing do
score_dificuldade.py.

Envia as questões em LOTES (default 10 por mensagem) de texto puro; o modelo
devolve um JSON CHAVEADO pelo número da questão, garantindo o mapeamento:

  {"questoes": {"<numero>": {bloom_level, logic_steps, interdisciplinary,
                             distractor_plausibility, inversion_command,
                             reading_load, requires_formula_recall,
                             prior_knowledge_dependency}}}

Features persistidas em `data/json/<label>_questoes.json` na chave
`features_ia` (com `modelo` e `data`); lote que falhar após N tentativas fica
sem gravar (o `status` mostra a cobertura). Questões com `midia` são ignoradas
por padrão (`--com-midia` as inclui usando a descrição textual da figura).

Uso (imagem vestibular-app, com $PWD montado em /work e --network host):
  python extract_features.py status [labels...]
  python extract_features.py extract [labels...] [--force] [--com-midia] \
      [--batch 10] [--limite N] [--modelo hy3]

Env: OPENCODE_BASE_URL, OPENCODE_API_KEY, MAX_TENTATIVAS, CONCURRENCY,
MODEL_FEATURES, TEMPERATURE, FEAT_BATCH, MAX_APOIO.
"""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from score_dificuldade import (
    JK,
    MAX_TENT,
    CONCURRENCY,
    _ago,
    _extract_json,
    gpt_call,
    has_midia,
    labels_all,
    load_pair,
    obj_questions,
    save_pair,
)

MODEL = os.environ.get("MODEL_FEATURES", "hy3")
BATCH = int(os.environ.get("FEAT_BATCH", "10"))
MAX_APOIO = int(os.environ.get("MAX_APOIO", "1500"))
CHECKPOINT = int(os.environ.get("CHECKPOINT", "3"))

SCHEMA = {
    "bloom_level": {"tipo": "int", "min": 1, "max": 6},
    "logic_steps": {"tipo": "int", "min": 1, "max": 5},
    "interdisciplinary": {"tipo": "bool"},
    "distractor_plausibility": {"tipo": "int", "min": 1, "max": 3},
    "inversion_command": {"tipo": "bool"},
    "reading_load": {"tipo": "int", "min": 1, "max": 3},
    "requires_formula_recall": {"tipo": "bool"},
    "prior_knowledge_dependency": {"tipo": "int", "min": 1, "max": 3},
}

RUBRIC = """{
  "bloom_level": <int>, // 1: Lembrar (decoreba), 2: Entender, 3: Aplicar (fórmula direta), 4: Analisar (inferência estrutural), 5: Avaliar, 6: Criar/Sintetizar (múltiplos modelos abstratos).
  "logic_steps": <int>, // Número estimado de operações lógicas ou cálculos sequenciais necessários para resolver (1 a 5).
  "interdisciplinary": <boolean>, // true se exigir integração explícita de mais de uma subárea ou disciplina; false caso contrário.
  "distractor_plausibility": <int>, // 1: Absurdos fáceis de eliminar; 2: Erros comuns de cálculo/sinal; 3: Distratores altamente sofisticados, baseados em falsas premissas teóricas.
  "inversion_command": <boolean>, // true se o enunciado usar "EXCETO", "INCORRETA", "NÃO é", etc.
  "reading_load": <int>, // 1: Comando direto (basta ler a última linha); 2: Leitura integral necessária mas fluida; 3: Alta carga cognitiva (textos densos, múltiplas restrições a reter na memória).
  "requires_formula_recall": <boolean>, // true se o aluno precisar buscar uma equação matemática/química na memória (não fornecida no texto).
  "prior_knowledge_dependency": <int> // 1: Pura interpretação de texto; 2: Domínio de conceitos base; 3: Exige teoria profunda e específica sem apoio no texto.
}"""

SYSTEM = (
    "Você é um especialista em psicometria e elaboração de itens para exames "
    "de alto nível (FUVEST, UNICAMP, ENEM). Sua tarefa é analisar questões de "
    "múltipla escolha e extrair features de complexidade cognitiva. "
    "Responda ÚNICA e EXCLUSIVAMENTE com um objeto JSON válido, sem nenhum "
    "texto adicional, saudações ou formatação markdown fora do bloco JSON. "
    "Siga rigorosamente o schema e a rubrica fornecidos."
)


def _trunc(t, n=MAX_APOIO):
    t = (t or "").strip()
    return t[:n] + "…" if len(t) > n else t


def _render(q, com_midia):
    partes = [f"Q{q['numero']}"]
    partes.append(f"GABARITO: {q['gabarito']}")
    areas = q.get("areas") or []
    if areas:
        partes.append("AREAS: " + ", ".join(str(a) for a in areas))
    partes.append(f"ENUNCIADO: {_trunc(q.get('enunciado'))}")
    tex = q.get("textos_de_apoio") or []
    if tex:
        apoio = "\n".join(_trunc(t) for t in tex if isinstance(t, str))
        partes.append(f"TEXTOS DE APOIO: {apoio}")
    if com_midia and q.get("midia"):
        mid = "\n".join(_trunc(m) for m in q["midia"] if isinstance(m, str))
        partes.append(f"FIGURA (descrição): {mid}")
    alt = q.get("alternativas") or {}
    alt_txt = "\n".join(f"{k}) {v}" for k, v in sorted(alt.items()))
    partes.append(f"ALTERNATIVAS:\n{alt_txt}")
    return "\n".join(partes)


def batch_prompt(label, questoes, com_midia):
    corpo = "\n\n---\n\n".join(_render(q, com_midia) for q in questoes)
    nums = ", ".join(str(q["numero"]) for q in questoes)
    return (
        f"Exame: {label}. Analise as {len(questoes)} questões abaixo "
        f"(Q+numero identifica cada uma). Para CADA questão, preencha TODOS os "
        "campos do schema seguindo a rubrica.\n"
        f"Esquema da resposta (chaves = numeros das questões):\n"
        '{"questoes": {"<numero>": ' + RUBRIC.replace("\n", " ") + "}}\n\n"
        f"QUESTÕES ({nums}):\n\n{corpo}"
    )


def normalize(raw):
    """Valida/ajusta um dict de features contra o SCHEMA; None se inválido."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for campo, regra in SCHEMA.items():
        v = raw.get(campo)
        if v is None:
            return None
        if regra["tipo"] == "bool":
            if isinstance(v, bool):
                out[campo] = v
            elif isinstance(v, str) and v.lower() in ("true", "false"):
                out[campo] = v.lower() == "true"
            elif v in (0, 1):
                out[campo] = bool(v)
            else:
                return None
        else:
            try:
                n = int(round(float(v)))
            except (TypeError, ValueError):
                return None
            if not (regra["min"] <= n <= regra["max"]):
                return None
            out[campo] = n
    return out


def parse_responses(text, esperados):
    data = _extract_json(text)
    feats = data.get("questoes")
    if isinstance(feats, list):
        feats = {
            str(r.get("numero")): {k: v for k, v in r.items() if k != "numero"}
            for r in feats
            if isinstance(r, dict) and r.get("numero") is not None
        }
    if not isinstance(feats, dict):
        raise ValueError("chave 'questoes' ausente ou não é objeto")
    resp, falhas = {}, []
    for num_str in esperados:
        raw = feats.get(str(num_str))
        norm = normalize(raw)
        if norm is None:
            falhas.append(num_str)
        else:
            resp[num_str] = norm
    if falhas:
        raise ValueError(f"features inválidas/ausentes para: {falhas}")
    return resp


def solve_batch(label, lote, com_midia, modelo):
    esperados = [str(q["numero"]) for q in lote]
    prompt = batch_prompt(label, lote, com_midia)
    last = None
    for i in range(MAX_TENT):
        try:
            text, thinking = gpt_call(
                modelo,
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": prompt}],
            )
            resp = parse_responses(text, esperados)
            return resp, (thinking and "thinking-ligado"), None
        except Exception as e:
            last = str(e)[:160]
            time.sleep(2 + 2 * i)
    return None, f"ERRO {last}", esperados


def cmd_status(labels):
    for label in labels:
        questoes, _ = load_pair(label)
        qs = obj_questions(questoes)
        mid = [q for q in qs if has_midia(q)]
        ft = [q for q in qs if q.get("features_ia")]
        print(
            f"{label:16} obj={len(qs):4} midia={len(mid):4} "
            f"com_features={len(ft):4} ({len(ft) / len(qs):.0%})" if qs else label
        )


def cmd_extract(labels, force=False, com_midia=False, batch=BATCH, limite=0):
    for label in labels:
        questoes, imagens = load_pair(label)
        fila = [
            q for q in obj_questions(questoes)
            if force or not q.get("features_ia")
        ]
        if not com_midia:
            fila = [q for q in fila if not has_midia(q)]
        if limite:
            fila = fila[:limite]
        if not fila:
            print(f"[{label}] nada a extrair (use --force p/ refazer)", flush=True)
            continue
        lotes = [fila[i:i + batch] for i in range(0, len(fila), batch)]
        print(f"[{label}] {len(fila)} questões em {len(lotes)} lote(s) de ≤{batch}"
              f" | modelo={MODEL} | midia={'incluída (descrição)' if com_midia else 'ignorada'}",
              flush=True)

        ok = falhas = 0
        try:
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
                fut = {ex.submit(solve_batch, label, lote, com_midia, MODEL): lote
                       for lote in lotes}
                for i, f in enumerate(as_completed(fut), 1):
                    resp, aviso, pendente = f.result()
                    if resp:
                        for q in fut[f]:
                            q["features_ia"] = dict(resp[str(q["numero"])])
                            q["features_ia"]["modelo"] = MODEL
                            q["features_ia"]["data"] = _ago()
                        ok += len(resp)
                    else:
                        falhas += len(pendente)
                        print(f"      lote {fut[f][0]['numero']}..{fut[f][-1]['numero']}: "
                              f"{aviso} — questões {pendente} ficaram sem features",
                              flush=True)
                    if i % CHECKPOINT == 0:
                        save_pair(label, questoes, imagens)
        finally:
            save_pair(label, questoes, imagens)
        print(f"[{label}] features gravadas={ok} falhas={falhas} | "
              f"{sum(1 for q in obj_questions(questoes) if q.get('features_ia'))}"
              f"/{len(obj_questions(questoes))} no total", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["status", "extract"])
    ap.add_argument("labels", nargs="*", default=None)
    ap.add_argument("--force", action="store_true",
                    help="reprocessa questões já com features")
    ap.add_argument("--com-midia", action="store_true",
                    help="inclui questões com figura (usa a descrição textual)")
    ap.add_argument("--batch", type=int, default=BATCH,
                    help="questões por mensagem (default 10)")
    ap.add_argument("--limite", type=int, default=0,
                    help="máx de questões por label (teste)")
    ap.add_argument("--modelo", default=MODEL,
                    help="modelo do router (default hy3)")
    args, extra = ap.parse_known_args()

    labels = (list(args.labels or []) + extra) or labels_all()
    labels = [l.removesuffix("_questoes") for l in labels]

    if args.cmd == "status":
        cmd_status(labels)
    else:
        cmd_extract(labels, args.force, args.com_midia, args.batch, args.limite)


if __name__ == "__main__":
    main()