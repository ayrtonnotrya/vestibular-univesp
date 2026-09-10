"""Orquestração do estudo: próxima questão + registro da resposta.

Modo Estudar (`proxima_questao`):
1. pool = catálogo inteiro (temas com questão disponível), sem portão FSRS;
2. sorteio em **dois estágios** por **mistura de 3 componentes**: frequência
   das provas (prior UNIVESP), fraqueza do usuário (1 − score por tema/θ da
   área) e exploração (inverso das observações). Em cada estágio, cada
   componente é NORMALIZADO como distribuição sobre os candidatos do estágio e
   então combinado nas fatias `ALVO_*` (70/15/15) — os alvos valem como fatia
   efetiva EXATA de cada origem no sorteio do estágio, independente das escalas
   díspares dos componentes crus (o modelo antigo, com multiplicadores brutos,
   fazia o peso nominal ≠ participação real).
   Estágio 1: área — f = Σ dos priors dos temas, a = 1 − sigmoid(θ da área),
   e = 1/(1 + n_obs de `habilidades`). Estágio 2: tema dentro da área — f =
   prior do tema, a = 1 − score (contagem >= `MIN_TENTATIVAS_REVISAO`; senão
   1 − sigmoid(θ da área)), e = 1/(1 + contagem). O nº de temas do catálogo
   fica neutro para a fatia da área;
3. questão do tema sorteado é sorteada uniformemente, preferindo inéditas.

Modo Revisão (`proxima_revisao`): fila dedicada dos temas **vencidos** pelo
FSRS (portão de contagem, cap por sessão) — pendências (erro/dúvida/chute)
primeiro e, se o tema não tem pendência, questão **inédita** do tema.

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
from .fuso import agora as _agora, naive_iso as _naive_iso
from .rasch import _sigmoid

# Fatia efetiva de cada origem no sorteio de cada estágio (mistura de
# componentes): frequência real do tema nas provas UNIVESP, fraqueza do usuário
# e exploração. Cada componente é normalizado como distribuição sobre os
# candidatos antes de ser ponderado (ver `_pesos_mistura`); por isso estes
# valores SOMAM 1 e são exatamente a participação de cada origem — ao
# contrário dos multiplicadores brutos do modelo anterior, cuja escala
# dependia da grandeza de cada termo (Σfreq=1, Σfraqueza≈2, Σexploração≪1).
ALVO_FREQ = 0.70
ALVO_FRAQUEZA = 0.15
ALVO_EXPLORACAO = 0.15

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
    FSRS: no modo Estudar o pool é o catálogo inteiro e a mistura de 3
    componentes (frequência + fraqueza + exploração) decide o sorteio."""
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


def _pesos_mistura(itens: list[dict]) -> list[float]:
    """Mistura dos 3 componentes (f/a/e) sobre os candidatos de um estágio.

    Cada componente é normalizado como distribuição (soma 1) sobre `itens` e
    então combinado nas fatias `ALVO_*`; o resultado também soma 1 quando os
    alvos somam 1. Assim os ALVO_* valem como participação de cada origem no
    sorteio deste estágio, independente das escalas cruas dos componentes
    (freq ~0,001–0,02 por tema, fraqueza ~0–1, exploração ~0–1)."""
    tot_f = sum(i["f"] for i in itens) or 1.0
    tot_a = sum(i["a"] for i in itens) or 1.0
    tot_e = sum(i["e"] for i in itens) or 1.0
    return [
        ALVO_FREQ * i["f"] / tot_f
        + ALVO_FRAQUEZA * i["a"] / tot_a
        + ALVO_EXPLORACAO * i["e"] / tot_e
        for i in itens
    ]


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
    da fase, mantendo as regras de sorteio (mistura, Rasch).

    O pool é o catálogo inteiro, com sorteio em **dois estágios por mistura de
    3 componentes** (ver módulo `_pesos_mistura`): cada estágio normaliza
    frequência/fraqueza/exploração como distribuições sobre os candidatos e
    combina nas fatias `ALVO_*` = 70/15/15 — a participação efetiva de cada
    origem sai EXATAMENTE como o alvo, e não como um multiplicador bruto da
    escala (diferença do modelo anterior em que "0,2 de fraqueza" valia ~37%
    do sorteio). Estágio 1 (área): f = Σ dos priors dos temas da área, a =
    1 − sigmoid(θ da área), e = 1/(1 + n_obs de `habilidades`). Estágio 2 (tema
    dentro da área): f = prior UNIVESP do tema, a = 1 − score (contagem >=
    `MIN_TENTATIVAS_REVISAO`; abaixo do portão usa 1 − sigmoid(θ da área),
    estimativa estável, sem oscilar a cada resposta), e = 1/(1 + contagem).
    Havendo uma única área entre os candidatos (ex.: `tema_id` fixo), o
    estágio 1 é pulado. `excluir_ids` remove questões específicas.

    Retorna dict com chaves: questao_id, exame_label, numero, enunciado,
    textos_de_apoio, midia, alternativas (dict), gabarito, tema_id, tema_nome,
    area_id, theta, nivel_base ("tema"|"area"), nivel_tema (score por tema ou
    None), nivel_contagem.
    """
    agora = agora or _agora()
    rng = random.Random(seed)
    temas = _temas_pool(con, area_id, tema_id, fase)
    if not temas:
        return None

    # Componentes por tema candidato: frequência real nas provas UNIVESP,
    # fraqueza do usuário (score por tema quando há evidência; senão θ da área)
    # e exploração (inverso das observações). A mistura por estágio acontece em
    # `_pesos_mistura`; o sorteio ponderado evita temas presos em uma resposta.
    prior = frequencia_mod.prior_por_tema(con)
    thetas: dict[int, float] = {}
    candidatos: dict[int, list[dict]] = {}
    for t in temas:
        aid = t["area_id"]
        if aid not in thetas:
            thetas[aid] = rasch_mod.theta_area(con, usuario, aid)
        theta = thetas[aid]
        nivel = niveis_mod.habilidade_tema(con, usuario, t["tema_id"])
        q = seletor_mod.escolher_aleatoria(con, usuario, t["tema_id"], rng, excluir_ids)
        if q:
            score = nivel["score"] if nivel["score"] is not None else SCORE_NEUTRO
            if nivel["contagem"] >= MIN_TENTATIVAS_REVISAO:
                fraqueza = 1.0 - score
            else:
                fraqueza = 1.0 - _sigmoid(theta)
            candidatos.setdefault(aid, []).append(
                {
                    "t": t,
                    "q": q,
                    "theta": theta,
                    "nivel": nivel,
                    "f": prior[t["tema_id"]],
                    "a": fraqueza,
                    "e": 1.0 / (1.0 + nivel["contagem"]),
                }
            )
    if not candidatos:
        return None

    # Com uma única área distinta (escopo restrito por tema_id/fase), o
    # estágio 1 não tem efeito e o tema é sorteado direto pela mistura.
    if len(candidatos) == 1:
        cs = next(iter(candidatos.values()))
        i = rng.choices(cs, weights=_pesos_mistura(cs), k=1)[0]
        return _forma_questao(i["t"], i["q"], i["theta"], i["nivel"])

    n_obs_area = {
        r["area_id"]: r["n_obs"]
        for r in con.execute(
            "SELECT area_id, n_obs FROM habilidades WHERE usuario = ?", (usuario,)
        )
    }
    areas = list(candidatos)
    comp_area = [
        {
            "f": sum(c["f"] for c in candidatos[aid]),
            "a": 1.0 - _sigmoid(thetas[aid]),
            "e": 1.0 / (1.0 + n_obs_area.get(aid, 0)),
        }
        for aid in areas
    ]
    area = areas[rng.choices(range(len(areas)), weights=_pesos_mistura(comp_area), k=1)[0]]
    cs = candidatos[area]
    i = rng.choices(cs, weights=_pesos_mistura(cs), k=1)[0]
    return _forma_questao(i["t"], i["q"], i["theta"], i["nivel"])


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
    usuário (portão de contagem, sem cap). Dentro do tema, a questão vem das
    **pendências** (última resposta errada/dúvida/chute) ou, na ausência,
    de uma **inédita** — nunca questões já respondidas corretamente.

    Mesmo shape de retorno de `proxima_questao`; None quando nenhum tema
    vencido do escopo tem pendência ou inédita (o aviso padrão do app cobre).

    `area_id`/`tema_id`/`fase` restringem o escopo como em `proxima_questao`.
    """
    agora = agora or _agora()
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
    agora = agora or _agora()
    cond, params = (
        "f.usuario = ? AND f.vencimento IS NOT NULL AND date(f.vencimento) <= date(?)",
        [usuario, _naive_iso(agora)],
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


def marcar_sessao(
    con: sqlite3.Connection,
    usuario: str,
    modo: str,
    questao_id: int | None = None,
    agora: dt.datetime | None = None,
) -> None:
    """Persiste o modo ativo do usuário (e, opcionalmente, a questão em aberto).

    Sobrevive a refresh/nova sessão do app: `_restaurar_do_url` reabre o MESMO
    modo + questão, independente de query params na URL. `questao_id=None`
    atualiza só o modo (a questão aberta anterior é preservada)."""
    agora_iso = _naive_iso(agora or _agora())
    if questao_id is None:
        con.execute(
            """INSERT INTO sessoes (usuario, modo, questao_id, atualizado_em)
               VALUES (?, ?, NULL, ?)
               ON CONFLICT (usuario) DO UPDATE SET
                   modo = excluded.modo,
                   atualizado_em = excluded.atualizado_em""",
            (usuario, modo, agora_iso),
        )
    else:
        con.execute(
            """INSERT INTO sessoes (usuario, modo, questao_id, atualizado_em)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (usuario) DO UPDATE SET
                   modo = excluded.modo,
                   questao_id = excluded.questao_id,
                   atualizado_em = excluded.atualizado_em""",
            (usuario, modo, questao_id, agora_iso),
        )
    con.commit()


def sessao_atual(
    con: sqlite3.Connection,
    usuario: str,
) -> tuple[str | None, int | None]:
    """Sessão persistida do usuário: (modo, questao_id); (None, None) sem registro."""
    row = con.execute(
        "SELECT modo, questao_id FROM sessoes WHERE usuario = ?", (usuario,)
    ).fetchone()
    return (row["modo"], row["questao_id"]) if row else (None, None)


def apagar_sessao(con: sqlite3.Connection, usuario: str) -> None:
    """Remove a sessão persistida (ex.: filtros mudaram → recomeçar)."""
    con.execute("DELETE FROM sessoes WHERE usuario = ?", (usuario,))
    con.commit()


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
    agora = agora or _agora()
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
                      AND (date(f.vencimento) <= date(?) OR f.vencimento IS NULL)) AS temas_vencidos
           FROM areas a
           LEFT JOIN habilidades h ON h.area_id = a.id AND h.usuario = ?
           ORDER BY a.nome""",
        (
            usuario,
            _naive_iso(_agora()),
            usuario,
        ),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["temas"] = por_area.get(item["area_id"], [])
        out.append(item)
    return out
