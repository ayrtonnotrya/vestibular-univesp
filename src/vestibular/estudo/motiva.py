"""Orquestração do estudo: próxima questão + registro da resposta.

Fluxo:
1. FSRS responde "o que está vencido" (temas com revisão vencida ou novos);
2. tema vencido é sorteado com peso = mistura entre o prior do relatório
   (frequência real do tema nas provas UNIVESP) e a urgência FSRS; o prior
   dita no cold start e decai conforme o usuário revisa o tema (α);
3. seletor escolhe a questão dentro do tema sorteado — habilidade = nível por
   tema (`niveis_usuarios`) se houver dados, senão θ da área (Rasch);
4. resposta => grava tentativa, atualiza FSRS do(s) tema(s), θ da(s) área(s),
   nível por tema (score/racha/contagem) e o `b` da questão (calibração).
"""

import datetime as dt
import json
import random
import sqlite3

from . import frequencia as frequencia_mod
from . import fsrs as fsrs_mod
from . import niveis as niveis_mod
from . import rasch as rasch_mod
from . import seletor as seletor_mod

# Fator de decaimento do prior do relatório em favor do FSRS por tema: quanto
# maior, mais rápido a frequência real perde peso conforme o usuário revisa o
# tema (0.7 => a cada tentativa o assunto pessoal ganha ~ metade do espaço).
DECAIMENTO = 0.7

# Piso da urgência FSRS (evita peso zero para tema muito memorizado).
_URGENCIA_MIN = 0.05


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


def _urgencia_fsrs(r: float | None) -> float:
    """Urgência de revisão do tema em [0.05, 1.0]: novo ou muito esquecido => 1."""
    if r is None:
        return 1.0
    return max(_URGENCIA_MIN, min(1.0, 1.0 - r))


def proxima_questao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    seed: int | None = None,
    excluir_ids: set[int] | None = None,
    area_id: int | None = None,
    tema_id: int | None = None,
) -> dict | None:
    """Devolve a próxima questão (objetiva) ou None se não há nada para estudar.

    `area_id`/`tema_id` (opcionais) restringem os temas considerados; None
    significa "qualquer área/tema".

    Retorna dict com chaves: questao_id, exame_label, numero, enunciado,
    textos_de_apoio, midia, alternativas (dict), gabarito, tema_id, tema_nome,
    area_id, theta, nivel_base ("tema"|"area"), nivel_tema (score por tema ou
    None).
    """
    agora = agora or dt.datetime.now(dt.UTC)
    rng = random.Random(seed)
    vencidos = fsrs_mod.vencidos(con, usuario, agora, area_id, tema_id)
    if not vencidos:
        return None

    # candidatos: temas vencidos com questão disponível. O peso final combina o
    # prior do relatório (frequência real do tema nas provas UNIVESP) com a
    # urgência FSRS: no cold start (poucas revisões do tema) o relatório dita a
    # probabilidade; conforme o usuário acumula revisões no tema, o FSRS assume.
    prior = frequencia_mod.prior_por_tema(con)
    candidatos = []
    for t in vencidos:
        theta = rasch_mod.theta_area(con, usuario, t["area_id"])
        nivel = niveis_mod.habilidade_tema(con, usuario, t["tema_id"])
        habilidade = nivel["valor"] if nivel["base"] == "tema" else theta
        q = seletor_mod.escolher(
            con, usuario, t["tema_id"], habilidade, rng, excluir_ids
        )
        if q:
            freq = prior.get(t["tema_id"], frequencia_mod.PRIOR_FLOOR)
            alfa = 1.0 / (1.0 + DECAIMENTO * nivel["contagem"])
            peso = alfa * freq + (1.0 - alfa) * _urgencia_fsrs(t["r"])
            candidatos.append((t, q, theta, nivel, peso))
    if not candidatos:
        return None

    t, q, theta, nivel, _ = rng.choices(
        candidatos, weights=[c[4] for c in candidatos], k=1
    )[0]
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


def questao_por_id(
    con: sqlite3.Connection,
    usuario: str,
    questao_id: int,
) -> dict | None:
    """Reconstrói o dict (mesma forma de `proxima_questao`) de uma questão
    específica — usado para restaurar a questão após reload/refresh."""
    q = con.execute(
        """SELECT id, exame_label, numero, enunciado, textos_de_apoio, midia,
                  alternativas, gabarito
           FROM questoes
           WHERE id = ? AND tipo = 'objetiva' AND anulada = 0
                 AND gabarito IS NOT NULL""",
        (questao_id,),
    ).fetchone()
    if q is None:
        return None
    temas = _temas_da_questao(con, questao_id)
    if not temas:
        return None
    t = temas[0]
    theta = rasch_mod.theta_area(con, usuario, t["area_id"])
    nivel = niveis_mod.habilidade_tema(con, usuario, t["tema_id"])
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
