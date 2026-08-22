"""Orquestração do estudo: próxima questão + registro da resposta.

Fluxo:
1. FSRS responde "o que está vencido" (temas com revisão vencida ou novos);
2. seletor escolhe a questão dentro do tema vencido;
3. resposta => grava tentativa, atualiza FSRS do(s) tema(s), θ da(s) área(s)
   e o `b` da questão (calibração com respostas reais).
"""
import datetime as dt
import json
import random
import sqlite3

from . import fsrs as fsrs_mod
from . import rasch as rasch_mod
from . import seletor as seletor_mod


def _area_do_tema(con: sqlite3.Connection, tema_id: int) -> int:
    return con.execute("SELECT area_id FROM temas WHERE id = ?", (tema_id,)).fetchone()["area_id"]


def _temas_da_questao(con: sqlite3.Connection, questao_id: int) -> list[dict]:
    return con.execute(
        """SELECT t.id AS tema_id, t.area_id, t.nome
           FROM classificacoes c JOIN temas t ON t.id = c.tema_id
           WHERE c.questao_id = ?""",
        (questao_id,),
    ).fetchall()


def proxima_questao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    seed: int | None = None,
    excluir_ids: set[int] | None = None,
) -> dict | None:
    """Devolve a próxima questão (objetiva) ou None se não há nada para estudar.

    Retorna dict com chaves: questao_id, exame_label, numero, enunciado,
    alternativas (dict), gabarito, tema_id, tema_nome, area_id.
    """
    agora = agora or dt.datetime.now(dt.UTC)
    rng = random.Random(seed)
    vencidos = fsrs_mod.vencidos(con, usuario, agora)
    if not vencidos:
        return None

    # a questão precisa ter pelo menos 1 tema vencido; tenta em ordem de R
    for t in vencidos:
        theta = rasch_mod.theta_area(con, usuario, t["area_id"])
        q = seletor_mod.escolher(con, usuario, t["tema_id"], theta, rng, excluir_ids)
        if q:
            return {
                "questao_id": q["id"],
                "exame_label": q["exame_label"],
                "numero": q["numero"],
                "enunciado": q["enunciado"],
                "alternativas": json.loads(q["alternativas"]) if q["alternativas"] else None,
                "gabarito": q["gabarito"],
                "tema_id": t["tema_id"],
                "tema_nome": t["nome"],
                "area_id": t["area_id"],
            }
    return None


def responder(
    con: sqlite3.Connection,
    usuario: str,
    questao_id: int,
    resposta: str,
    agora: dt.datetime | None = None,
    detalhe: str | None = None,
) -> dict:
    """Registra a resposta e atualiza o estado (FSRS, Rasch, item)."""
    agora = agora or dt.datetime.now(dt.UTC)
    q = con.execute(
        "SELECT gabarito, anulada FROM questoes WHERE id = ?", (questao_id,)
    ).fetchone()
    if q is None:
        raise ValueError(f"questao {questao_id} inexistente")

    if q["anulada"] or not q["gabarito"]:
        correta = None
    else:
        correta = (resposta or "").strip().lower() == q["gabarito"].strip().lower()

    con.execute(
        "INSERT INTO tentativas(usuario, questao_id, resposta, correta, data, detalhe) VALUES (?, ?, ?, ?, ?, ?)",
        (usuario, questao_id, resposta, correta, agora.isoformat(), detalhe),
    )
    con.commit()

    # FSRS por tema + Rasch por área + b do item
    temas = _temas_da_questao(con, questao_id)
    areas = {t["area_id"] for t in temas}
    atualizacoes_fsrs = []
    for t in temas:
        if correta is None:
            continue
        atualizacoes_fsrs.append(
            fsrs_mod.revisar(con, usuario, t["tema_id"], bool(correta), agora)
        )
    for area_id in areas:
        if correta is None:
            continue
        rasch_mod.atualiza_habilidades(con, usuario, area_id)
    if correta is not None:
        rasch_mod.atualiza_item_b(con, questao_id)

    return {
        "questao_id": questao_id,
        "resposta": resposta,
        "gabarito": q["gabarito"],
        "correta": correta,
        "temas": atualizacoes_fsrs,
    }


def progresso(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Resumo por área: theta, variância, nº de tentativas e temas vencidos."""
    rows = con.execute(
        """SELECT a.id AS area_id, a.nome AS area,
                  h.theta, h.var_theta, h.n_obs,
                  (SELECT COUNT(*) FROM fsrs_estados f
                    JOIN temas t ON t.id = f.tema_id
                    WHERE f.usuario = ? AND t.area_id = a.id
                      AND (f.vencimento <= ? OR f.vencimento IS NULL)) AS temas_vencidos
           FROM areas a
           LEFT JOIN habilidades h ON h.area_id = a.id AND h.usuario = ?
           ORDER BY a.nome""",
        (
            usuario,
            dt.datetime.now(dt.UTC).isoformat(),
            usuario,
        ),
    ).fetchall()
    return [dict(r) for r in rows]