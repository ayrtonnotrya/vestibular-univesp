"""Conexão e schema do banco do motor de estudo.

Banco único (SQLite, por padrão `data/vestibular.db`, configurável via
`DB_PATH`) com as tabelas do motor: catálogo, questões, classificações,
dificuldades, itens (parâmetros Rasch), estados FSRS por tema, habilidades
por área e tentativas.
"""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "data/vestibular.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
    id   INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS temas (
    id      INTEGER PRIMARY KEY,
    area_id INTEGER NOT NULL REFERENCES areas(id),
    nome    TEXT NOT NULL,
    UNIQUE (area_id, nome)
);

CREATE TABLE IF NOT EXISTS vestibulares (
    id   INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS questoes (
    id                   INTEGER PRIMARY KEY,
    vestibular_id        INTEGER NOT NULL REFERENCES vestibulares(id),
    exame_label          TEXT NOT NULL,
    ano                  INTEGER,
    numero               INTEGER NOT NULL,
    tipo                 TEXT NOT NULL,           -- objetiva | redacao
    enunciado            TEXT,
    textos_de_apoio      TEXT,                    -- JSON (array de parágrafos de contexto)
    midia                TEXT,                    -- JSON (array de descrições de figuras)
    alternativas         TEXT,                    -- JSON {a..e}
    gabarito             TEXT,                    -- a-e | null (redacao/anulada)
    fonte_pdf            TEXT,
    anulada              INTEGER NOT NULL DEFAULT 0,
    extraida_parcialmente INTEGER NOT NULL DEFAULT 0,
    UNIQUE (exame_label, numero)
);

CREATE TABLE IF NOT EXISTS classificacoes (
    id        INTEGER PRIMARY KEY,
    questao_id INTEGER NOT NULL REFERENCES questoes(id),
    area_id   INTEGER NOT NULL REFERENCES areas(id),
    tema_id   INTEGER NOT NULL REFERENCES temas(id),
    UNIQUE (questao_id, tema_id)
);

CREATE TABLE IF NOT EXISTS dificuldades (
    id             INTEGER PRIMARY KEY,
    questao_id     INTEGER NOT NULL UNIQUE REFERENCES questoes(id),
    score          REAL,                          -- 0..1 (IA low-thinking)
    tentativas_ia  INTEGER,
    modelo         TEXT
);

CREATE TABLE IF NOT EXISTS item_params (
    id        INTEGER PRIMARY KEY,
    questao_id INTEGER NOT NULL UNIQUE REFERENCES questoes(id),
    b         REAL NOT NULL DEFAULT 0.0,          -- dificuldade em logit
    n_obs     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fsrs_estados (
    id             INTEGER PRIMARY KEY,
    usuario        TEXT NOT NULL,
    tema_id        INTEGER NOT NULL REFERENCES temas(id),
    card_json      TEXT,                          -- py-fsrs Card
    estado         TEXT NOT NULL DEFAULT 'new',   -- new|learning|review|relearning
    vencimento     TEXT,                          -- ISO 8601
    ultima_revisao TEXT,
    repos          INTEGER NOT NULL DEFAULT 0,
    lapses         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (usuario, tema_id)
);

CREATE TABLE IF NOT EXISTS habilidades (
    id       INTEGER PRIMARY KEY,
    usuario  TEXT NOT NULL,
    area_id  INTEGER NOT NULL REFERENCES areas(id),
    theta    REAL NOT NULL DEFAULT 0.0,
    var_theta REAL NOT NULL DEFAULT 2.0,
    n_obs    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (usuario, area_id)
);

CREATE TABLE IF NOT EXISTS niveis_usuarios (
    id          INTEGER PRIMARY KEY,
    usuario     TEXT NOT NULL,
    tema_id     INTEGER NOT NULL REFERENCES temas(id),
    score       REAL NOT NULL DEFAULT 0.5,        -- média ponderada de acertos (0..1)
    racha       INTEGER NOT NULL DEFAULT 0,       -- sequência atual de acertos
    contagem    INTEGER NOT NULL DEFAULT 0,       -- qtd_tentativas
    ultima_data TEXT,
    UNIQUE (usuario, tema_id)
);

CREATE TABLE IF NOT EXISTS tentativas (
    id        INTEGER PRIMARY KEY,
    usuario   TEXT NOT NULL,
    questao_id INTEGER NOT NULL REFERENCES questoes(id),
    resposta  TEXT,
    correta   INTEGER,                            -- 0/1 | null se anulada
    data      TEXT NOT NULL,
    detalhe   TEXT                                -- JSON (feedback IA)
);
"""


def _migrar_questoes(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(questoes)")}
    for nome in ("textos_de_apoio", "midia"):
        if nome not in cols:
            con.execute(f"ALTER TABLE questoes ADD COLUMN {nome} TEXT")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrar_questoes(con)
    con.commit()
    return con
