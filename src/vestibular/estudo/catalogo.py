"""Seed do catálogo (áreas/temas) a partir de `data/assuntos.json`.

A taxonomia é fechada: `areas` e `temas` são inseridos com INSERT OR IGNORE
(UNIQUE), então o seed é idempotente.
"""
import json
import sqlite3
from pathlib import Path

DEFAULT_ASSUNTOS = Path("data/assuntos.json")


def seed_catalogo(con: sqlite3.Connection, assuntos_path: str | Path | None = None) -> dict:
    path = Path(assuntos_path) if assuntos_path else DEFAULT_ASSUNTOS
    with open(path, encoding="utf-8") as f:
        cat = json.load(f)["plano_de_estudos_vestibular"]

    got_areas, got_temas = {}, {}
    for disc in cat["disciplinas"]:
        area = disc["area"]
        con.execute("INSERT OR IGNORE INTO areas(nome) VALUES (?)", (area,))
        row = con.execute("SELECT id FROM areas WHERE nome = ?", (area,)).fetchone()
        got_areas[area] = row["id"]
        for mod in disc.get("modulos", []):
            for assunto in mod["assuntos"]:
                con.execute(
                    "INSERT OR IGNORE INTO temas(area_id, nome) VALUES (?, ?)",
                    (got_areas[area], assunto),
                )
                t = con.execute(
                    "SELECT id FROM temas WHERE area_id = ? AND nome = ?",
                    (got_areas[area], assunto),
                ).fetchone()
                got_temas[assunto] = t["id"]
    con.commit()
    return {"areas": got_areas, "temas": got_temas}


def mapa_ids(con: sqlite3.Connection) -> dict:
    """Retorna {area: {id, temas: {nome: id}}} para consulta na importação."""
    areas = con.execute("SELECT id, nome FROM areas").fetchall()
    out = {}
    for a in areas:
        ts = con.execute(
            "SELECT id, nome FROM temas WHERE area_id = ?", (a["id"],)
        ).fetchall()
        out[a["nome"]] = {"id": a["id"], "temas": {t["nome"]: t["id"] for t in ts}}
    return out