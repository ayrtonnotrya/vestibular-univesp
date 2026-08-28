"""Agendamento FSRS por tema (py-fsrs, padrão FSRS-5/6).

Cada `(usuario, tema)` é um "card" do FSRS: o estado completo (estabilidade,
dificuldade, due) é persistido serializado em `fsrs_estados.card_json`.
Acertou a questão -> rating Good; errou -> rating Again.

Política (ver `fsrs_config`): temas com menos de `MIN_TENTATIVAS_REVISAO`
respostas são **exploráveis** — não ganham card e entram em `vencidos()` com
`vencimento=None` (nunca contam como "atrasados"). A partir do portão, o tema
recebe card FSRS com passo de aprendizagem em dias e entra na fila de vencidos,
limitada por sessão (`CAP_REVISOES_SESSAO`). O modo Estudar sorteia sobre o
catálogo inteiro (`motiva._temas_pool`); este módulo alimenta a fila de Revisão
e as estatísticas.
"""
import datetime as dt
import sqlite3

from fsrs import Card, Rating

from .fsrs_config import CAP_REVISOES_SESSAO, MIN_TENTATIVAS_REVISAO, make_scheduler

_scheduler = make_scheduler()

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


def _contagem(con: sqlite3.Connection, usuario: str, tema_id: int) -> int:
    row = con.execute(
        "SELECT contagem FROM niveis_usuarios WHERE usuario = ? AND tema_id = ?",
        (usuario, tema_id),
    ).fetchone()
    return row["contagem"] if row else 0


def revisar(
    con: sqlite3.Connection,
    usuario: str,
    tema_id: int,
    correta: bool,
    agora: dt.datetime | None = None,
) -> dict:
    """Registra uma resposta no FSRS do tema e devolve o novo vencimento.

    Temas abaixo do portão de evidência (`contagem + 1 <
    MIN_TENTATIVAS_REVISAO`) não ganham card: retornam
    `{"vencimento": None, "estado": "exploracao"}` sem tocar em `fsrs_estados`.
    """
    agora = agora or dt.datetime.now(dt.UTC)
    if _contagem(con, usuario, tema_id) + 1 < MIN_TENTATIVAS_REVISAO:
        return {"tema_id": tema_id, "vencimento": None, "estado": "exploracao"}
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
        """SELECT f.card_json, n.contagem AS contagem
           FROM fsrs_estados f
           LEFT JOIN niveis_usuarios n ON n.tema_id = f.tema_id AND n.usuario = f.usuario
           WHERE f.usuario = ? AND f.tema_id = ?""",
        (usuario, tema_id),
    ).fetchone()
    if not row or not row["card_json"]:
        return None
    if (row["contagem"] or 0) < MIN_TENTATIVAS_REVISAO:
        return None
    card = Card.from_json(row["card_json"])
    try:
        return _scheduler.get_card_retrievability(card, agora)
    except (ValueError, TypeError):
        return 1.0


def vencidos(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    area_id: int | None = None,
    tema_id: int | None = None,
    fase: int | None = None,
) -> list[dict]:
    """Temas do agendamento FSRS do usuário, na ordem da fila de revisão.

    Separa o catálogo em **exploráveis** (sem card OU contagem <
    `MIN_TENTATIVAS_REVISAO`; `vencimento=None`/`r=None`) e **vencidos** (card
    existe, contagem >= portão, `vencimento <= agora`). O subgrupo vencido é
    limitado por urgência (dias de atraso + 2·lapses) a
    `CAP_REVISOES_SESSAO`; os exploráveis vão ao fim (nunca contam como
    vencidos, mas seguem no retorno para quem quiser o catálogo inteiro).

    `area_id`/`tema_id`/`fase` (opcionais) restringem o escopo do agendamento;
    `fase` filtra pela ordem da fase/módulo do catálogo dentro da área.

    Retorna: [{tema_id, area_id, nome, vencimento, r}].
    """
    agora = agora or dt.datetime.now(dt.UTC)
    conds = "(f.vencimento IS NULL OR f.vencimento <= ?)"
    params: list = [usuario, usuario, _iso(agora)]
    if area_id is not None:
        conds += " AND t.area_id = ?"
        params.append(area_id)
    if tema_id is not None:
        conds += " AND t.id = ?"
        params.append(tema_id)
    if fase is not None:
        conds += " AND t.fase = ?"
        params.append(fase)
    rows = con.execute(
        f"""SELECT t.id AS tema_id, t.area_id, t.nome,
                  f.vencimento AS venc, f.lapses AS lapses, f.card_json,
                  n.contagem AS contagem
           FROM temas t
           LEFT JOIN fsrs_estados f
             ON f.tema_id = t.id AND f.usuario = ?
           LEFT JOIN niveis_usuarios n
             ON n.tema_id = t.id AND n.usuario = ?
           WHERE {conds}
           ORDER BY t.nome""",
        params,
    ).fetchall()

    exploraveis: list[dict] = []
    due: list[dict] = []
    for r in rows:
        contagem = r["contagem"] or 0
        if r["venc"] is None or not r["card_json"] or contagem < MIN_TENTATIVAS_REVISAO:
            exploraveis.append(
                {
                    "tema_id": r["tema_id"],
                    "area_id": r["area_id"],
                    "nome": r["nome"],
                    "vencimento": None,
                    "r": None,
                }
            )
            continue
        try:
            r_ = _scheduler.get_card_retrievability(
                Card.from_json(r["card_json"]), agora
            )
        except (ValueError, TypeError):
            r_ = None
        due.append(
            {
                "tema_id": r["tema_id"],
                "area_id": r["area_id"],
                "nome": r["nome"],
                "vencimento": r["venc"],
                "r": r_,
                "_urg": (agora - dt.datetime.fromisoformat(r["venc"])).days
                + 2 * (r["lapses"] or 0),
            }
        )
    # vencidos: urgência desc (mais atrasado/esquecido primeiro), cap por sessão
    due.sort(key=lambda x: x["_urg"], reverse=True)
    due = due[:CAP_REVISOES_SESSAO]
    for d in due:
        d.pop("_urg")
    # exploráveis vão ao fim (R None), como na ordenação anterior
    return due + exploraveis