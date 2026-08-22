"""Orquestração do estudo: próxima questão + registro da resposta.

Fluxo:
1. FSRS responde "o que está vencido" (temas com revisão vencida ou novos);
2. seletor escolhe a questão dentro do tema vencido — habilidade = nível por
   tema (`niveis_usuarios`) se houver dados, senão θ da área (Rasch);
3. resposta => grava tentativa, atualiza FSRS do(s) tema(s), θ da(s) área(s),
   nível por tema (score/racha/contagem) e o `b` da questão (calibração).
"""

import datetime as dt
import json
import random
import sqlite3

from . import fsrs as fsrs_mod
from . import niveis as niveis_mod
from . import rasch as rasch_mod
from . import seletor as seletor_mod


def _area_do_tema(con: sqlite3.Connection, tema_id: int) -> int:
    return con.execute("SELECT area_id FROM temas WHERE id = ?", (tema_id,)).fetchone()[
        "area_id"
    ]


def _temas_da_questao(con: sqlite3.Connection, questao_id: int) -> list[dict]:
    return con.execute(
        """SELECT t.id AS tema_id, t.area_id, t.nome
           FROM classificacoes c JOIN temas t ON t.id = c.tema_id
           WHERE c.questao_id = ?""",
        (questao_id,),
    ).fetchall()


def _json_lista(texto: str | None) -> list:
    if not texto:
        return []
    try:
        lst = json.loads(texto)
        return lst if isinstance(lst, list) else []
    except json.JSONDecodeError:
        return []


def proxima_questao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    seed: int | None = None,
    excluir_ids: set[int] | None = None,
) -> dict | None:
    """Devolve a próxima questão (objetiva) ou None se não há nada para estudar.

    Retorna dict com chaves: questao_id, exame_label, numero, enunciado,
    textos_de_apoio, midia, alternativas (dict), gabarito, tema_id, tema_nome,
    area_id, theta, nivel_base ("tema"|"area"), nivel_tema (score por tema ou
    None).
    """
    agora = agora or dt.datetime.now(dt.UTC)
    rng = random.Random(seed)
    vencidos = fsrs_mod.vencidos(con, usuario, agora)
    if not vencidos:
        return None

    # candidatos: temas vencidos com questão disponível. Revisões atrasadas
    # (r não-None) têm prioridade, mas o desempate aleatório entre os mais
    # urgentes + amostra de temas novos faz o estudo alternar de assunto em vez
    # de prender sempre no mesmo tema (desempate alfabético do vencidos).
    candidatos = []
    for t in vencidos:
        theta = rasch_mod.theta_area(con, usuario, t["area_id"])
        nivel = niveis_mod.habilidade_tema(con, usuario, t["tema_id"])
        habilidade = nivel["valor"] if nivel["base"] == "tema" else theta
        q = seletor_mod.escolher(
            con, usuario, t["tema_id"], habilidade, rng, excluir_ids
        )
        if q:
            candidatos.append((t, q, theta, nivel))
    if not candidatos:
        return None

    def chave(c):
        t = c[0]
        return (
            0 if t["r"] is not None else 1,
            t["r"] if t["r"] is not None else 0.0,
            rng.random(),
        )

    candidatos.sort(key=chave)
    t, q, theta, nivel = rng.choice(candidatos[: min(6, len(candidatos))])
    return {
        "questao_id": q["id"],
        "exame_label": q["exame_label"],
        "numero": q["numero"],
        "enunciado": q["enunciado"],
        "textos_de_apoio": _json_lista(q["textos_de_apoio"]),
        "midia": _json_lista(q["midia"]),
        "alternativas": json.loads(q["alternativas"])
        if q["alternativas"]
        else None,
        "gabarito": q["gabarito"],
        "tema_id": t["tema_id"],
        "tema_nome": t["nome"],
        "area_id": t["area_id"],
        "theta": theta,
        "nivel_base": nivel["base"],
        "nivel_tema": nivel["score"],
        "nivel_contagem": nivel["contagem"],
    }


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

    # FSRS por tema + nível por tema + Rasch por área + b do item
    temas = _temas_da_questao(con, questao_id)
    areas = {t["area_id"] for t in temas}
    atualizacoes_fsrs = []
    for t in temas:
        if correta is None:
            continue
        atualizacoes_fsrs.append(
            fsrs_mod.revisar(con, usuario, t["tema_id"], bool(correta), agora)
        )
    atuais_niveis = (
        niveis_mod.atualiza(con, usuario, questao_id, correta, agora)
        if correta is not None
        else []
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
        "niveis": atuais_niveis,
    }


def niveis_por_tema(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Níveis por tema do usuário (score, racha, contagem) com nomes de área/tema."""
    return niveis_mod.niveis_usuario(con, usuario)


def progresso(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Resumo por área: theta, variância, nº de tentativas, temas vencidos e os
    níveis por tema da área."""
    niveis = niveis_mod.niveis_usuario(con, usuario)
    por_area: dict[int, list[dict]] = {}
    for n in niveis:
        por_area.setdefault(n["area_id"], []).append(n)
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
    out = []
    for r in rows:
        item = dict(r)
        item["temas"] = por_area.get(item["area_id"], [])
        out.append(item)
    return out
