"""Agendamento FSRS por tema (py-fsrs, padrão FSRS-5/6).

Cada `(usuario, tema)` é um "card" do FSRS: o estado completo (estabilidade,
dificuldade, due) é persistido serializado em `fsrs_estados.card_json`.
Acertou a questão -> rating Good; errou -> rating Again.
"""
import datetime as dt
import sqlite3

from fsrs import Card, Rating, Scheduler

_scheduler = Scheduler()

_ESTADO_MAPA = {0: "new", 1: "learning", 2: "review", 3: "relearning"}


def _iso(d: dt.datetime) -> str:
    return d.isoformat()


def _card(con: sqlite3.Connection, usuario: str, tema_id: int) -> Card:
    row = con.execute(
        "SELECT card_json FROM fsrs_estados WHERE usuario = ? AND tema_id = ?",
        (usuario, tema_id),
    ).fetchone()
    if row and row["card_json"]:
        return Card.from_json(row["card_json"])
    return Card()  # novo: estado Learning/step 0


def revisar(
    con: sqlite3.Connection,
    usuario: str,
    tema_id: int,
    correta: bool,
    agora: dt.datetime | None = None,
) -> dict:
    """Registra uma resposta no FSRS do tema e devolve o novo vencimento."""
    agora = agora or dt.datetime.now(dt.UTC)
    card = _card(con, usuario, tema_id)
    rating = Rating.Good if correta else Rating.Again
    novo, _log = _scheduler.review_card(card, rating, agora)
    estado = _ESTADO_MAPA.get(int(novo.state.value), "learning")
    repos, lapses = 1, 0
    if not correta:
        lapses = 1
    con.execute(
        """INSERT INTO fsrs_estados
             (usuario, tema_id, card_json, estado, vencimento, ultima_revisao, repos, lapses)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(usuario, tema_id) DO UPDATE SET
             card_json = excluded.card_json,
             estado = excluded.estado,
             vencimento = excluded.vencimento,
             ultima_revisao = excluded.ultima_revisao,
             repos = fsrs_estados.repos + excluded.repos,
             lapses = fsrs_estados.lapses + excluded.lapses""",
        (
            usuario,
            tema_id,
            novo.to_json(),
            estado,
            _iso(novo.due),
            _iso(agora),
            repos,
            lapses,
        ),
    )
    con.commit()
    return {"tema_id": tema_id, "vencimento": novo.due, "estado": estado}


def _retrievability(con: sqlite3.Connection, usuario: str, tema_id: int, agora) -> float | None:
    row = con.execute(
        "SELECT card_json FROM fsrs_estados WHERE usuario = ? AND tema_id = ?",
        (usuario, tema_id),
    ).fetchone()
    if not row:
        return None
    card = Card.from_json(row["card_json"])
    try:
        return _scheduler.get_card_retrievability(card, agora)
    except (ValueError, TypeError):
        return 1.0


def vencidos(con: sqlite3.Connection, usuario: str, agora: dt.datetime | None = None) -> list[dict]:
    """Temas com revisão vencida (ou nunca iniciados), ordenados por R crescente.

    Retorna: [{tema_id, area_id, nome, vencimento, r}].
    """
    agora = agora or dt.datetime.now(dt.UTC)
    rows = con.execute(
        """SELECT t.id AS tema_id, t.area_id, t.nome,
                  f.vencimento AS venc, f.card_json
           FROM temas t
           LEFT JOIN fsrs_estados f
             ON f.tema_id = t.id AND f.usuario = ?
           WHERE f.vencimento IS NULL OR f.vencimento <= ?
           ORDER BY t.nome""",
        (usuario, _iso(agora)),
    ).fetchall()

    out = []
    for r in rows:
        r_ = None
        if r["card_json"]:
            card = Card.from_json(r["card_json"])
            try:
                r_ = _scheduler.get_card_retrievability(card, agora)
            except (ValueError, TypeError):
                r_ = None
        out.append(
            {
                "tema_id": r["tema_id"],
                "area_id": r["area_id"],
                "nome": r["nome"],
                "vencimento": r["venc"],
                "r": r_,
            }
        )
    # R None (nunca visto) vai para o fim; o resto por R crescente (mais esquecido primeiro)
    out.sort(key=lambda x: (1.0 if x["r"] is None else x["r"]))
    return out