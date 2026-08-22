"""Import dos JSONs extraídos (data/json/<label>_questoes.json) para o SQLite.

Popula: vestibulares, questoes, classificacoes, item_params (b a partir de
dificuldades se existir; senão b=0 como prior neutro).
"""
import json
import math
import sqlite3
from pathlib import Path

from .catalogo import mapa_ids, seed_catalogo

DEFAULT_JSON_DIR = Path("data/json")


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _vestibular_nome(label: str) -> str:
    # fuvest_2026 -> fuvest ; univesp_2017_2s -> univesp
    for prefixo in ("univesp", "fuvest", "unesp", "unicamp"):
        if label.startswith(prefixo):
            return prefixo
    return label.split("_")[0]


def import_questoes_json(
    con: sqlite3.Connection,
    label: str,
    questoes_json: str | Path,
    semestre: int | None = None,
) -> dict:
    with open(questoes_json, encoding="utf-8") as f:
        j = json.load(f)

    cat = mapa_ids(con)
    vest = _vestibular_nome(label)
    con.execute("INSERT OR IGNORE INTO vestibulares(nome) VALUES (?)", (vest,))
    vid = con.execute("SELECT id FROM vestibulares WHERE nome = ?", (vest,)).fetchone()["id"]

    n_questoes = n_redacao = n_objetiva = 0
    for q in j["questoes"]:
        numero = q["numero"]
        tipo = q.get("tipo", "objetiva")
        gab = q.get("gabarito")
        con.execute(
            """INSERT OR IGNORE INTO questoes
                 (vestibular_id, exame_label, ano, numero, tipo, enunciado,
                  textos_de_apoio, midia, alternativas, gabarito, fonte_pdf,
                  anulada, extraida_parcialmente)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                vid,
                label,
                j.get("ano"),
                numero,
                tipo,
                q.get("enunciado"),
                json.dumps(q.get("textos_de_apoio", []), ensure_ascii=False),
                json.dumps(q.get("midia", []), ensure_ascii=False),
                json.dumps(q.get("alternativas"), ensure_ascii=False) if q.get("alternativas") else None,
                gab if gab and not q.get("anulada") else None,
                j.get("fonte_questoes"),
                1 if q.get("anulada") else 0,
                1 if q.get("extraida_parcialmente") else 0,
            ),
        )
        qid = con.execute(
            "SELECT id FROM questoes WHERE exame_label = ? AND numero = ?",
            (label, numero),
        ).fetchone()["id"]
        con.execute(
            """UPDATE questoes SET textos_de_apoio = ?, midia = ?
               WHERE id = ?""",
            (
                json.dumps(q.get("textos_de_apoio", []), ensure_ascii=False),
                json.dumps(q.get("midia", []), ensure_ascii=False),
                qid,
            ),
        )

        # classificações (área/assunto conforme o catálogo fechado)
        for a in q.get("areas", []):
            area = cat.get(a["area"])
            if not area:
                continue
            for assunto in a.get("assuntos", []):
                tid = area["temas"].get(assunto)
                if tid is None:
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO classificacoes(questao_id, area_id, tema_id) VALUES (?, ?, ?)",
                    (qid, area["id"], tid),
                )

        # item param: b = logit(score IA) se houver; senão 0 (neutro)
        if tipo == "objetiva":
            score = None
            if q.get("score") is not None:
                score = q["score"]
            b = logit(score) if score is not None else 0.0
            con.execute(
                "INSERT OR IGNORE INTO item_params(questao_id, b, n_obs) VALUES (?, ?, 0)",
                (qid, b),
            )
            if score is not None:
                con.execute(
                    "INSERT OR IGNORE INTO dificuldades(questao_id, score, tentativas_ia, modelo) VALUES (?, ?, ?, ?)",
                    (qid, score, q.get("tentativas_ia", 1), q.get("modelo", "gemini")),
                )
            n_objetiva += 1
        else:
            n_redacao += 1
        n_questoes += 1

    con.commit()
    return {"label": label, "questoes": n_questoes, "objetivas": n_objetiva, "redacoes": n_redacao}


def import_todos(con: sqlite3.Connection, json_dir: str | Path | None = None) -> list[dict]:
    seed_catalogo(con)
    d = Path(json_dir) if json_dir else DEFAULT_JSON_DIR
    labels = sorted(p.name.removesuffix("_questoes.json") for p in d.glob("*_questoes.json"))
    out = []
    for label in labels:
        path = d / f"{label}_questoes.json"
        out.append(import_questoes_json(con, label, path))
    return out