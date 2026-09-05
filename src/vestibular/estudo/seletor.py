"""Seletor de questão dentro de um tema (seleção adaptativa Rasch).

Order of preference:
1. questões nunca respondidas pelo usuário (exploração) — escolhida por
   dificuldade mais próxima de θ (Rasch): |θ - b| mínimo em top-k + sorteio;
2. questões já vistas — |θ - b| mínimo em top-k + sorteio, priorizando as
   menos recentes.

Uma resposta no tema atualiza FSRS, θ da área e o `b` da própria questão.
"""
import random
import sqlite3


def _questoes_tema(con: sqlite3.Connection, tema_id: int) -> list[dict]:
    return con.execute(
        """SELECT q.id, q.numero, q.exame_label, q.enunciado, q.textos_de_apoio,
                  q.midia, q.alternativas, q.gabarito, q.tipo
           FROM questoes q
           JOIN classificacoes c ON c.questao_id = q.id
           WHERE c.tema_id = ? AND q.tipo = 'objetiva' AND q.anulada = 0
                 AND q.gabarito IS NOT NULL""",
        (tema_id,),
    ).fetchall()


def _vistas(con: sqlite3.Connection, usuario: str, questao_ids: list[int]) -> dict[int, str]:
    if not questao_ids:
        return {}
    qs = ",".join("?" * len(questao_ids))
    rows = con.execute(
        f"""SELECT questao_id, MAX(data) AS ultima
            FROM tentativas WHERE usuario = ? AND questao_id IN ({qs})
            GROUP BY questao_id""",
        [usuario, *questao_ids],
    ).fetchall()
    return {r["questao_id"]: r["ultima"] for r in rows}


def _ultimas_tentativas(
    con: sqlite3.Connection, usuario: str, questao_ids: list[int]
) -> dict[int, dict]:
    """Última tentativa por questão vista: {questao_id: {correta, grau_certeza, data}}."""
    if not questao_ids:
        return {}
    qs = ",".join("?" * len(questao_ids))
    rows = con.execute(
        f"""SELECT questao_id, correta, grau_certeza, data FROM (
                SELECT t.questao_id, t.correta, t.grau_certeza, t.data,
                       ROW_NUMBER() OVER (
                         PARTITION BY t.questao_id ORDER BY t.data DESC, t.id DESC
                       ) AS rn
                FROM tentativas t
                WHERE t.usuario = ? AND t.questao_id IN ({qs})
              )
              WHERE rn = 1""",
        [usuario, *questao_ids],
    ).fetchall()
    return {r["questao_id"]: dict(r) for r in rows}


def escolher_aleatoria(
    con: sqlite3.Connection,
    usuario: str,
    tema_id: int,
    rng: random.Random,
    excluir_ids: set[int] | None = None,
) -> dict | None:
    """Questão aleatória do tema (sorteio uniforme), preferindo inéditas.

    Devolve None se o tema não tem questão disponível. Usado na seleção por
    prioridade do tema (evita repetição das mesmas questões de sempre)."""
    excluir = excluir_ids or set()
    questoes = [q for q in _questoes_tema(con, tema_id) if q["id"] not in excluir]
    if not questoes:
        return None
    vistos = _vistas(con, usuario, [q["id"] for q in questoes])
    ineditas = [q for q in questoes if q["id"] not in vistos]
    pool = ineditas or questoes
    return dict(rng.choice(pool))


def escolher(
    con: sqlite3.Connection,
    usuario: str,
    tema_id: int,
    theta: float,
    rng: random.Random,
    excluir_ids: set[int] | None = None,
) -> dict | None:
    excluir = excluir_ids or set()
    questoes = [q for q in _questoes_tema(con, tema_id) if q["id"] not in excluir]
    if not questoes:
        return None
    vistos = _vistas(con, usuario, [q["id"] for q in questoes])

    def chave(r: int) -> tuple:
        b = con.execute("SELECT b FROM item_params WHERE questao_id = ?", (r,)).fetchone()
        b = b["b"] if b else 0.0
        return abs(theta - b)

    novas = [q for q in questoes if q["id"] not in vistos]
    pool = novas or questoes
    if not pool:
        return None
    # top-k por proximidade a θ; entre empatados, sorteio
    pool.sort(key=lambda q: chave(q["id"]))
    top = pool[: min(8, len(pool))]
    escolhida = rng.choice(top)
    return dict(escolhida)


def escolher_revisao(
    con: sqlite3.Connection,
    usuario: str,
    tema_id: int,
    rng: random.Random,
) -> dict | None:
    """Questão do tema para a fila de revisão.

    Preferência pela última resposta: pendências (errada OU com grau_certeza
    'duvida'/'chute') — a mais antiga; se o tema venceu mas não tem pendência,
    cai em questão **inédita** (sorteada) para reforçar o tema. Não devolve
    questões já respondidas corretamente. None se o tema não tem pendência nem
    inédita.
    """
    questoes = _questoes_tema(con, tema_id)
    if not questoes:
        return None
    ids = [q["id"] for q in questoes]
    ult = _ultimas_tentativas(con, usuario, ids)
    vistas = [q for q in questoes if q["id"] in ult]
    if not vistas:
        return dict(rng.choice(questoes))

    def pendente(q: dict) -> bool:
        r = ult[q["id"]]
        return r["correta"] == 0 or r["grau_certeza"] in ("duvida", "chute")

    pendencias = [q for q in vistas if pendente(q)]
    if pendencias:
        pendencias.sort(key=lambda q: (ult[q["id"]]["data"], q["id"]))
        return dict(pendencias[0])
    ineditas = [q for q in questoes if q["id"] not in ult]
    if not ineditas:
        return None
    return dict(rng.choice(ineditas))