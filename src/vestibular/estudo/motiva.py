"""Orquestração do estudo: próxima questão + registro da resposta.

Modo Estudar (`proxima_questao`):
1. pool = catálogo inteiro (temas com questão disponível), sem portão FSRS;
2. tema é sorteado com peso = prioridade do tema:
   0,4·frequência real do tema nas provas UNIVESP + 0,4·fraqueza
   (1 − score por tema quando contagem >= `MIN_TENTATIVAS_REVISAO`; senão
   1 − sigmoid(θ da área)) + 0,2·exploração (inverso das observações);
3. questão do tema sorteado é sorteada uniformemente, preferindo inéditas.

Modo Revisão (`proxima_revisao`): fila dedicada dos temas **vencidos** pelo
FSRS (portão de contagem, cap por sessão), escolhendo questões já vistas —
pendências (erro/dúvida/chute) primeiro, depois acertos antigos.

Resposta (`responder`) grava tentativa, atualiza FSRS do(s) tema(s), θ da(s)
área(s), nível por tema (score/racha/contagem) e o `b` da questão.
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
from .fsrs_config import MIN_TENTATIVAS_REVISAO
from .rasch import _sigmoid

# Pesos da prioridade do tema no sorteio da próxima questão: frequência real
# do tema nas provas UNIVESP, fraqueza do usuário (1 - score por tema) e
# exploração (inverso do nº de observações).
PESO_FREQ = 0.4
PESO_FRAQUEZA = 0.4
PESO_EXPLORACAO = 0.2

# Score neutro (1 - score = 0.5) para temas sem tentativas do usuário.
SCORE_NEUTRO = 0.5

# Caderno de erros: valores possíveis e rótulos de exibição.
GRAUS_CERTEZA = ("conviccao", "duvida", "chute")
CAUSAS_ERRO = ("teoria", "pegadinha", "atencao")
GRAU_CERTEZA_LABEL = {
    "conviccao": "🟢 Convicção",
    "duvida": "🟡 Dúvida",
    "chute": "🔴 Chute",
}
CAUSA_ERRO_LABEL = {
    "teoria": "🧠 Lacuna Teórica",
    "pegadinha": "🎯 Pegadinha de Banca",
    "atencao": "🔍 Atenção/Cálculo",
}


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


def _forma_questao(t: dict, q: dict, theta: float, nivel: dict) -> dict:
    """Dict de retorno comum de `proxima_questao`/`proxima_revisao`."""
    return {
        "questao_id": q["id"],
        "exame_label": q["exame_label"],
        "numero": q["numero"],
        "enunciado": q["enunciado"],
        "textos_de_apoio": _json_lista(q["textos_de_apoio"]),
        "midia": _json_lista(q["midia"]),
        "alternativas": json.loads(q["alternativas"]) if q["alternativas"] else None,
        "gabarito": q["gabarito"],
        "tema_id": t["tema_id"],
        "tema_nome": t["nome"],
        "area_id": t["area_id"],
        "theta": theta,
        "nivel_base": nivel["base"],
        "nivel_tema": nivel["score"],
        "nivel_contagem": nivel["contagem"],
    }


def _temas_pool(
    con: sqlite3.Connection,
    area_id: int | None = None,
    tema_id: int | None = None,
    fase: int | None = None,
) -> list[dict]:
    """Todos os temas do catálogo (restritos por área/tema/fase), sem portão
    FSRS: no modo Estudar o pool é o catálogo inteiro e a prioridade
    (frequência + fraqueza + exploração) decide o sorteio."""
    conds, params = [], []
    if area_id is not None:
        conds.append("t.area_id = ?")
        params.append(area_id)
    if tema_id is not None:
        conds.append("t.id = ?")
        params.append(tema_id)
    if fase is not None:
        conds.append("t.fase = ?")
        params.append(fase)
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = con.execute(
        f"SELECT t.id AS tema_id, t.area_id, t.nome FROM temas t {where} ORDER BY t.id",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def proxima_questao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    seed: int | None = None,
    excluir_ids: set[int] | None = None,
    area_id: int | None = None,
    tema_id: int | None = None,
    fase: int | None = None,
) -> dict | None:
    """Devolve a próxima questão (objetiva) ou None se não há nada para estudar.

    `area_id`/`tema_id`/`fase` (opcionais) restringem os temas considerados;
    None significa "qualquer área/tema/fase". `fase` é a ordem da fase/módulo
    do catálogo (assuntos.json) dentro da área — a questão sai de um dos temas
    da fase, mantendo as regras de sorteio (prioridade, Rasch).

    O pool é o catálogo inteiro: cada tema com questão disponível entra no
    sorteio com peso = prioridade (0,4·frequência real nas provas UNIVESP +
    0,4·fraqueza + 0,2·exploração). Temas com contagem >=
    `MIN_TENTATIVAS_REVISAO` usam fraqueza = 1 − score por tema; abaixo do
    portão, 1 − sigmoid(θ da área) (estimativa estável, sem oscilar a cada
    resposta). `excluir_ids` remove questões específicas.

    Retorna dict com chaves: questao_id, exame_label, numero, enunciado,
    textos_de_apoio, midia, alternativas (dict), gabarito, tema_id, tema_nome,
    area_id, theta, nivel_base ("tema"|"area"), nivel_tema (score por tema ou
    None), nivel_contagem.
    """
    agora = agora or dt.datetime.now(dt.UTC)
    rng = random.Random(seed)
    temas = _temas_pool(con, area_id, tema_id, fase)
    if not temas:
        return None

    # prioridade do tema: frequência real nas provas UNIVESP (o que mais cai),
    # fraqueza do usuário (1 - score por tema quando há evidência; senão θ da
    # área) e exploração (inverso das observações); o sorteio ponderado evita
    # temas que insistem em aparecer por uma única resposta.
    prior = frequencia_mod.prior_por_tema(con)
    candidatos = []
    thetas: dict[int, float] = {}
    for t in temas:
        aid = t["area_id"]
        if aid not in thetas:
            thetas[aid] = rasch_mod.theta_area(con, usuario, aid)
        theta = thetas[aid]
        nivel = niveis_mod.habilidade_tema(con, usuario, t["tema_id"])
        q = seletor_mod.escolher_aleatoria(con, usuario, t["tema_id"], rng, excluir_ids)
        if q:
            freq = prior.get(t["tema_id"], frequencia_mod.PRIOR_FLOOR)
            score = nivel["score"] if nivel["score"] is not None else SCORE_NEUTRO
            if nivel["contagem"] >= MIN_TENTATIVAS_REVISAO:
                fraqueza = 1.0 - score
            else:
                fraqueza = 1.0 - _sigmoid(theta)
            exploracao = 1.0 / (1.0 + nivel["contagem"])
            prioridade = (
                PESO_FREQ * freq
                + PESO_FRAQUEZA * fraqueza
                + PESO_EXPLORACAO * exploracao
            )
            candidatos.append((t, q, theta, nivel, prioridade))
    if not candidatos:
        return None

    t, q, theta, nivel, _ = rng.choices(
        candidatos, weights=[c[4] for c in candidatos], k=1
    )[0]
    return _forma_questao(t, q, theta, nivel)


def proxima_revisao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    seed: int | None = None,
    area_id: int | None = None,
    tema_id: int | None = None,
    fase: int | None = None,
) -> dict | None:
    """Próxima questão da fila de revisão: um tema **vencido** pelo FSRS do
    usuário (portão de contagem, sem cap) com questão **já vista** — pendências
    (erro/dúvida/chute) primeiro, depois acertos antigos. Nunca inéditas.

    Mesmo shape de retorno de `proxima_questao`; None quando não há tema
    vencido no escopo (o aviso padrão do app cobre).

    `area_id`/`tema_id`/`fase` restringem o escopo como em `proxima_questao`.
    """
    agora = agora or dt.datetime.now(dt.UTC)
    rng = random.Random(seed)
    vencidos = fsrs_mod.vencidos(con, usuario, agora, area_id, tema_id, fase)
    due = [t for t in vencidos if t["vencimento"] is not None]
    if not due:
        return None
    # urgência determinística: vencimento mais antigo → mais lapses → pior score
    marcas = ",".join("?" * len(due))
    lapses = {
        r["tema_id"]: r["lapses"]
        for r in con.execute(
            f"""SELECT tema_id, lapses FROM fsrs_estados
                WHERE usuario = ? AND tema_id IN ({marcas})""",
            [usuario, *(t["tema_id"] for t in due)],
        )
    }
    scores: dict[int, float] = {}
    for t in due:
        nivel = niveis_mod.nivel_tema(con, usuario, t["tema_id"])
        scores[t["tema_id"]] = (
            nivel["score"] if nivel and nivel["score"] is not None else SCORE_NEUTRO
        )

    def _chave(t: dict) -> tuple:
        return (t["vencimento"], -lapses.get(t["tema_id"], 0), scores[t["tema_id"]])

    due.sort(key=_chave)
    for t in due:
        theta = rasch_mod.theta_area(con, usuario, t["area_id"])
        nivel = niveis_mod.habilidade_tema(con, usuario, t["tema_id"])
        q = seletor_mod.escolher_revisao(con, usuario, t["tema_id"], rng)
        if q:
            return _forma_questao(t, q, theta, nivel)
    return None


def resumo_revisao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    area_id: int | None = None,
    tema_id: int | None = None,
    fase: int | None = None,
) -> dict:
    """Contadores do modo Revisão no escopo (área/tema/fase): temas com
    revisão vencida no FSRS (todos, sem o cap da sessão) e nº de pendências
    distintas (última tentativa errada ou com dúvida/chute)."""
    agora = agora or dt.datetime.now(dt.UTC)
    cond, params = (
        "f.usuario = ? AND f.vencimento IS NOT NULL AND f.vencimento <= ?",
        [usuario, agora.isoformat()],
    )
    if area_id is not None:
        cond += " AND t.area_id = ?"
        params.append(area_id)
    if tema_id is not None:
        cond += " AND t.id = ?"
        params.append(tema_id)
    if fase is not None:
        cond += " AND t.fase = ?"
        params.append(fase)
    vencidos = con.execute(
        f"""SELECT COUNT(*) FROM fsrs_estados f
            JOIN temas t ON t.id = f.tema_id
            JOIN niveis_usuarios n ON n.tema_id = f.tema_id AND n.usuario = f.usuario
            WHERE {cond} AND n.contagem >= ?""",
        [*params, MIN_TENTATIVAS_REVISAO],
    ).fetchone()[0]

    pcond, pparams = "t.usuario = ?", [usuario]
    if area_id is not None:
        pcond += " AND tm.area_id = ?"
        pparams.append(area_id)
    if tema_id is not None:
        pcond += " AND tm.id = ?"
        pparams.append(tema_id)
    if fase is not None:
        pcond += " AND tm.fase = ?"
        pparams.append(fase)
    pendencias = con.execute(
        f"""SELECT COUNT(DISTINCT questao_id) FROM (
              SELECT t.questao_id,
                     ROW_NUMBER() OVER (
                       PARTITION BY t.questao_id ORDER BY t.data DESC, t.id DESC
                     ) AS rn,
                     t.correta, t.grau_certeza
              FROM tentativas t
              JOIN classificacoes c ON c.questao_id = t.questao_id
              JOIN temas tm ON tm.id = c.tema_id
              WHERE {pcond}
            )
            WHERE rn = 1 AND (correta = 0 OR grau_certeza IN ('duvida', 'chute'))""",
        pparams,
    ).fetchone()[0]
    return {"vencidos": vencidos or 0, "pendencias": pendencias or 0}


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
    grau_certeza: str | None = None,
    causa_erro: str | None = None,
    sintese_ativa: str | None = None,
) -> dict:
    """Registra a resposta e atualiza o estado (FSRS, Rasch, item).

    `grau_certeza`/`causa_erro`/`sintese_ativa` (caderno de erros) são
    opcionais: acertos convictos e tentativas antigas ficam com NULL."""
    agora = agora or dt.datetime.now(dt.UTC)
    if grau_certeza is not None and grau_certeza not in GRAUS_CERTEZA:
        raise ValueError(
            f"grau_certeza inválido: {grau_certeza!r} (use {', '.join(GRAUS_CERTEZA)})"
        )
    if causa_erro is not None and causa_erro not in CAUSAS_ERRO:
        raise ValueError(
            f"causa_erro inválido: {causa_erro!r} (use {', '.join(CAUSAS_ERRO)})"
        )
    q = con.execute(
        "SELECT gabarito, anulada FROM questoes WHERE id = ?", (questao_id,)
    ).fetchone()
    if q is None:
        raise ValueError(f"questao {questao_id} inexistente")

    if q["anulada"] or not q["gabarito"]:
        correta = None
    else:
        correta = (resposta or "").strip().lower() == q["gabarito"].strip().lower()

    cur = con.execute(
        """INSERT INTO tentativas
           (usuario, questao_id, resposta, correta, data, detalhe,
            grau_certeza, causa_erro, sintese_ativa)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            usuario,
            questao_id,
            resposta,
            correta,
            agora.isoformat(),
            detalhe,
            grau_certeza,
            causa_erro,
            sintese_ativa,
        ),
    )
    con.commit()
    tentativa_id = cur.lastrowid

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
        "tentativa_id": tentativa_id,
        "questao_id": questao_id,
        "resposta": resposta,
        "gabarito": q["gabarito"],
        "correta": correta,
        "grau_certeza": grau_certeza,
        "causa_erro": causa_erro,
        "sintese_ativa": sintese_ativa,
        "temas": atualizacoes_fsrs,
        "niveis": atuais_niveis,
    }


def anotar_erro(
    con: sqlite3.Connection,
    tentativa_id: int,
    causa_erro: str | None = None,
    sintese_ativa: str | None = None,
) -> bool:
    """Preenche o caderno de erros de uma tentativa já registrada (causa +
    síntese ativa, capturadas após a conferência do gabarito).

    Retorna True se a tentativa existe e foi atualizada; False se o id não
    existe (ex.: questão nunca importada no banco)."""
    if causa_erro is not None and causa_erro not in CAUSAS_ERRO:
        raise ValueError(
            f"causa_erro inválido: {causa_erro!r} (use {', '.join(CAUSAS_ERRO)})"
        )
    cur = con.execute(
        "UPDATE tentativas SET causa_erro = ?, sintese_ativa = ? WHERE id = ?",
        (
            causa_erro,
            (sintese_ativa or "").strip() or None,
            tentativa_id,
        ),
    )
    con.commit()
    return cur.rowcount > 0


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
