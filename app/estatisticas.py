"""Consultas de estatísticas de desempenho para a página "Estatísticas".

Tudo lido de `data/vestibular.db` (SQL direto, sem dependência além de
sqlite3). As funções devolvem listas de dicts prontas para virar DataFrame
na camada Streamlit (`app/study.py`).
"""

import datetime as dt
import sqlite3
from collections import defaultdict

from fsrs import Card, Scheduler

from vestibular.estudo.motiva import CAUSA_ERRO_LABEL, GRAU_CERTEZA_LABEL

_scheduler = Scheduler()


def _de_faixa(dia_iso: str) -> str:
    """Dia (data[:10]) a partir de um timestamp ISO, exibido como dd/mm."""
    try:
        d = dt.datetime.fromisoformat(dia_iso)
    except ValueError:
        return dia_iso
    return d.strftime("%d/%m")


def _temas_areas(con: sqlite3.Connection) -> dict[int, dict]:
    rows = con.execute(
        """SELECT t.id, t.nome AS tema, t.area_id, a.nome AS area
           FROM temas t JOIN areas a ON a.id = t.area_id"""
    ).fetchall()
    return {
        r["id"]: {"tema": r["tema"], "area": r["area"], "area_id": r["area_id"]}
        for r in rows
    }


def resumo(
    con: sqlite3.Connection, usuario: str, agora: dt.datetime | None = None
) -> dict:
    """Métricas gerais do usuário: totais, aproveitamento, período e revisões."""
    agora = agora or dt.datetime.now(dt.UTC)
    row = con.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN correta = 1 THEN 1 ELSE 0 END) AS acertos,
                  SUM(CASE WHEN correta = 0 THEN 1 ELSE 0 END) AS erros,
                  SUM(CASE WHEN correta IS NULL THEN 1 ELSE 0 END) AS anuladas,
                  COUNT(DISTINCT questao_id) AS distintas,
                  MIN(data) AS primeira,
                  MAX(data) AS ultima
           FROM tentativas WHERE usuario = ?""",
        (usuario,),
    ).fetchone()
    total = row["total"] or 0
    acertos = row["acertos"] or 0
    deltas = []
    for r in con.execute(
        """SELECT t.data, t.correta, ip.b AS b FROM tentativas t
           LEFT JOIN item_params ip ON ip.questao_id = t.questao_id
           WHERE t.usuario = ? AND t.correta IS NOT NULL""",
        (usuario,),
    ):
        if r["b"] is not None:
            deltas.append((r["b"], int(r["correta"])))
    b_medio = sum(b for b, _ in deltas) / len(deltas) if deltas else None
    vencidas = con.execute(
        """SELECT COUNT(*) FROM fsrs_estados f JOIN temas t ON t.id = f.tema_id
           WHERE f.usuario = ? AND f.vencimento IS NOT NULL
             AND f.vencimento <= ?""",
        (usuario, agora.isoformat()),
    ).fetchone()[0]
    return {
        "total": total,
        "acertos": acertos,
        "erros": row["erros"] or 0,
        "anuladas": row["anuladas"] or 0,
        "distintas": row["distintas"] or 0,
        "pct": round(100 * acertos / total, 1) if total else 0.0,
        "b_medio": round(b_medio, 2) if b_medio is not None else None,
        "temas_vencidos": vencidas or 0,
        "primeira": (row["primeira"] or "")[:10],
        "ultima": (row["ultima"] or "")[:10],
    }


def por_dia(con: sqlite3.Connection, usuario: str, area_id: int | None = None) -> list[dict]:
    """Aproveitamento por dia: dia, tentativas, acertos, % (de uma área se dada)."""
    cond, params = "", [usuario]
    if area_id is not None:
        cond = (
            " AND EXISTS (SELECT 1 FROM classificacoes c "
            "WHERE c.questao_id = t.questao_id AND c.area_id = ?)"
        )
        params.append(area_id)
    por: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        f"SELECT data, correta FROM tentativas t WHERE t.usuario = ?{cond}", params
    ):
        dia = (r["data"] or "")[:10]
        por[dia][1] += 1
        if r["correta"] == 1:
            por[dia][0] += 1
    out = []
    for dia in sorted(por):
        ac, tot = por[dia]
        out.append(
            {
                "dia": _de_faixa(dia),
                "tentativas": tot,
                "acertos": ac,
                "pct": round(100 * ac / tot, 1) if tot else 0.0,
            }
        )
    return out


def por_area(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Por área: habilidade θ (Rasch), nº de observações e acurácia real
    (contando a questão uma vez para cada área em que foi classificada)."""
    try:
        rows = con.execute(
            """SELECT a.id AS area_id, a.nome AS area,
                      h.theta, h.n_obs
               FROM areas a
               LEFT JOIN habilidades h ON h.area_id = a.id AND h.usuario = ?
               ORDER BY h.theta DESC NULLS LAST""",
            (usuario,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = con.execute(
            """SELECT a.id AS area_id, a.nome AS area,
                      h.theta, h.n_obs
               FROM areas a
               LEFT JOIN habilidades h ON h.area_id = a.id AND h.usuario = ?
               ORDER BY a.nome""",
            (usuario,),
        ).fetchall()
    acur: dict[int, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        """SELECT c.area_id AS area_id,
                  COUNT(DISTINCT t.id) AS tot,
                  COUNT(DISTINCT CASE WHEN t.correta = 1 THEN t.id END) AS ac
           FROM tentativas t
           JOIN classificacoes c ON c.questao_id = t.questao_id
           WHERE t.usuario = ? AND t.correta IS NOT NULL
           GROUP BY c.area_id""",
        (usuario,),
    ):
        acur[r["area_id"]] = [r["ac"], r["tot"]]
    out = []
    for r in rows:
        ac, tot = acur.get(r["area_id"], [0, 0])
        out.append(
            {
                "area_id": r["area_id"],
                "area": r["area"],
                "theta": round(r["theta"], 2) if r["theta"] is not None else None,
                "n_obs": r["n_obs"] or 0,
                "acertos": ac,
                "tentativas": tot,
                "pct": round(100 * ac / tot, 1) if tot else None,
            }
        )
    return out


def _status_vencimento(venc_iso: str | None, agora: dt.datetime) -> str:
    if not venc_iso:
        return "sem revisão"
    try:
        venc = dt.datetime.fromisoformat(venc_iso)
        dias = (agora - venc).days
    except ValueError:
        return "sem revisão"
    if dias > 0:
        return "atrasada"
    if dias == 0:
        return "hoje"
    return "próxima"


def por_tema(
    con: sqlite3.Connection, usuario: str, agora: dt.datetime | None = None
) -> list[dict]:
    """Por tema com tentativas: score, racha, contagem e estado FSRS."""
    agora = agora or dt.datetime.now(dt.UTC)
    fsrs = {
        r["tema_id"]: r
        for r in con.execute(
            """SELECT tema_id, estado, repos, lapses, vencimento
               FROM fsrs_estados WHERE usuario = ?""",
            (usuario,),
        )
    }
    out = []
    for r in con.execute(
        """SELECT n.tema_id, n.score, n.racha, n.contagem, n.ultima_data,
                  t.nome AS tema, t.fase AS fase, a.nome AS area
           FROM niveis_usuarios n
           JOIN temas t ON t.id = n.tema_id
           JOIN areas a ON a.id = t.area_id
           WHERE n.usuario = ? AND n.contagem > 0
           ORDER BY n.score, n.contagem DESC""",
        (usuario,),
    ):
        f = fsrs.get(r["tema_id"])
        out.append(
            {
                "area": r["area"],
                "tema": r["tema"],
                "fase": r["fase"],
                "score": round(r["score"], 2),
                "racha": r["racha"],
                "tentativas": r["contagem"],
                "estado": (f["estado"] if f else None) or "—",
                "lapses": (f["lapses"] if f else 0) or 0,
                "vencimento": _status_vencimento(f["vencimento"] if f else None, agora),
            }
        )
    return out


def por_fase(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Desempenho agregado por fase (ordem do catálogo) a partir dos níveis por
    tema: score médio, nº de temas com dados e aproveitamento por área+fase."""
    conds = ["n.usuario = ?", "n.contagem > 0", "t.fase IS NOT NULL"]
    params: list = [usuario]
    if area_id is not None:
        conds.append("t.area_id = ?")
        params.append(area_id)
    rows = con.execute(
        f"""SELECT a.id AS area_id, a.nome AS area, t.fase AS fase,
                   COUNT(*) AS n_temas, AVG(n.score) AS score_medio,
                   SUM(n.contagem) AS tentativas,
                   SUM(n.contagem * n.score) AS acertos_est
            FROM niveis_usuarios n
            JOIN temas t ON t.id = n.tema_id
            JOIN areas a ON a.id = t.area_id
            WHERE {" AND ".join(conds)}
            GROUP BY t.area_id, t.fase
            ORDER BY a.nome, t.fase""",
        params,
    ).fetchall()
    out = []
    for r in rows:
        tot = r["tentativas"] or 0
        pct = 100.0 * (r["acertos_est"] or 0.0) / tot if tot else None
        out.append(
            {
                "area_id": r["area_id"],
                "area": r["area"],
                "fase": r["fase"],
                "n_temas": r["n_temas"],
                "score_medio": round(r["score_medio"], 2)
                if r["score_medio"] is not None
                else None,
                "tentativas": tot,
                "pct": round(pct, 1) if pct is not None else None,
            }
        )
    return out


def evolucao_por_fase(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Aproveitamento por dia por fase (tentativas reais, em ordem cronológica)."""
    conds = ["t.usuario = ?", "t.correta IS NOT NULL", "tm.fase IS NOT NULL"]
    params: list = [usuario]
    if area_id is not None:
        conds.append("tm.area_id = ?")
        params.append(area_id)
    por: dict[tuple, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        f"""SELECT a.nome AS area, tm.fase AS fase, t.data AS data, t.correta AS correta
            FROM tentativas t
            JOIN classificacoes c ON c.questao_id = t.questao_id
            JOIN temas tm ON tm.id = c.tema_id
            JOIN areas a ON a.id = tm.area_id
            WHERE {" AND ".join(conds)}""",
        params,
    ):
        chave = (r["area"], r["fase"], (r["data"] or "")[:10])
        por[chave][1] += 1
        if r["correta"] == 1:
            por[chave][0] += 1
    out = []
    for (area, fase, dia), (ac, tot) in por.items():
        out.append(
            {
                "area": area,
                "fase": fase,
                "data": dia,
                "dia": _de_faixa(dia),
                "acertos": ac,
                "tentativas": tot,
                "pct": round(100 * ac / tot, 1) if tot else 0.0,
            }
        )
    out.sort(key=lambda x: (x["data"], x["fase"]))
    return out


def por_exame(con: sqlite3.Connection, usuario: str, area_id: int | None = None) -> list[dict]:
    """Aproveitamento por exame (label), de uma área se dada."""
    cond, params = "", [usuario]
    if area_id is not None:
        cond = (
            " AND EXISTS (SELECT 1 FROM classificacoes c "
            "WHERE c.questao_id = q.id AND c.area_id = ?)"
        )
        params.append(area_id)
    por: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        f"""SELECT q.exame_label AS exame, t.correta AS correta
            FROM tentativas t JOIN questoes q ON q.id = t.questao_id
            WHERE t.usuario = ?{cond}""",
        params,
    ):
        por[r["exame"]][1] += 1
        if r["correta"] == 1:
            por[r["exame"]][0] += 1
    return [
        {
            "exame": exame,
            "tentativas": tot,
            "acertos": ac,
            "pct": round(100 * ac / tot, 1) if tot else 0.0,
        }
        for exame, (ac, tot) in sorted(por.items())
    ]


def revisoes(
    con: sqlite3.Connection, usuario: str, agora: dt.datetime | None = None,
    area_id: int | None = None,
) -> list[dict]:
    """Fila de revisão FSRS: temas com estado, ordenados por status e data."""
    agora = agora or dt.datetime.now(dt.UTC)
    cond, params = "", [usuario]
    if area_id is not None:
        cond = " AND t.area_id = ?"
        params.append(area_id)
    out = []
    for r in con.execute(
        f"""SELECT f.tema_id, f.estado, f.repos, f.lapses, f.vencimento,
                  t.nome AS tema, a.nome AS area
           FROM fsrs_estados f
           JOIN temas t ON t.id = f.tema_id
           JOIN areas a ON a.id = t.area_id
           WHERE f.usuario = ? AND f.vencimento IS NOT NULL{cond}""",
        params,
    ):
        out.append(
            {
                "area": r["area"],
                "tema": r["tema"],
                "estado": r["estado"],
                "repos": r["repos"],
                "lapses": r["lapses"],
                "vencimento": r["vencimento"],
                "status": _status_vencimento(r["vencimento"], agora),
            }
        )
    ordena = {"atrasada": 0, "hoje": 1, "próxima": 2}
    out.sort(key=lambda x: (ordena.get(x["status"], 3), x["vencimento"] or ""))
    return out


def historico(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Cada tentativa com exame, questão, resposta, gabarito e área/tema.

    Com `area_id`, só tentativas da área, listando apenas os temas dela (sem
    referências a outras áreas)."""
    temas_areas = _temas_areas(con)
    klass: dict[int, list[int]] = defaultdict(list)
    for r in con.execute("SELECT questao_id, tema_id FROM classificacoes"):
        info = temas_areas.get(r["tema_id"])
        if area_id is None or (info and info["area_id"] == area_id):
            klass[r["questao_id"]].append(r["tema_id"])
    cond, params = "", [usuario]
    if area_id is not None:
        cond = (
            " AND EXISTS (SELECT 1 FROM classificacoes c "
            "WHERE c.questao_id = q.id AND c.area_id = ?)"
        )
        params.append(area_id)
    out = []
    for r in con.execute(
        f"""SELECT t.id, t.data, t.questao_id, q.exame_label, q.numero, t.resposta,
                  q.gabarito, q.anulada, t.correta, t.grau_certeza, t.causa_erro,
                  t.sintese_ativa
           FROM tentativas t JOIN questoes q ON q.id = t.questao_id
           WHERE t.usuario = ?{cond} ORDER BY t.data, t.id""",
        params,
    ):
        areas: set[str] = set()
        temas: set[str] = set()
        for tid in klass.get(r["questao_id"], []):
            info = temas_areas.get(tid)
            if not info:
                continue
            areas.add(info["area"])
            temas.add(info["tema"])
        out.append(
            {
                "data": r["data"][:10],
                "exame": r["exame_label"],
                "questao": r["numero"],
                "resposta": (r["resposta"] or "").upper(),
                "gabarito": (r["gabarito"] or "").upper() if r["gabarito"] else "—",
                "resultado": {1: "✅", 0: "❌", None: "—"}.get(r["correta"]),
                "anulada": bool(r["anulada"]),
                "certeza": GRAU_CERTEZA_LABEL.get(r["grau_certeza"], "—"),
                "causa_erro": CAUSA_ERRO_LABEL.get(r["causa_erro"], "—"),
                "sintese_ativa": r["sintese_ativa"] or "—",
                "areas": ", ".join(sorted(areas)),
                "temas": ", ".join(sorted(temas)),
            }
        )
    return out


def por_vestibular(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Aproveitamento por vestibular (FUVEST, UNIVESP, UNESP, ENEM, FATEC),
    dentro de uma área se dada."""
    cond, params = "", [usuario]
    if area_id is not None:
        cond = (
            " AND EXISTS (SELECT 1 FROM classificacoes c "
            "WHERE c.questao_id = q.id AND c.area_id = ?)"
        )
        params.append(area_id)
    por: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        f"""SELECT v.nome AS vestibular, t.correta AS correta
            FROM tentativas t
            JOIN questoes q ON q.id = t.questao_id
            JOIN vestibulares v ON v.id = q.vestibular_id
            WHERE t.usuario = ?{cond}""",
        params,
    ):
        por[r["vestibular"]][1] += 1
        if r["correta"] == 1:
            por[r["vestibular"]][0] += 1
    return [
        {
            "vestibular": v.upper(),
            "tentativas": tot,
            "acertos": ac,
            "pct": round(100 * ac / tot, 1) if tot else 0.0,
        }
        for v, (ac, tot) in sorted(por.items())
    ]


def cobertura_fase(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Cobertura por fase: questões UNIVESP classificadas nos temas da fase
    contra questões distintas já tentadas pelo usuário."""
    cond, p_banco, p_user = "", [], [usuario]
    if area_id is not None:
        cond = " AND tm.area_id = ?"
        p_banco.append(area_id)
        p_user.append(area_id)
    banco = {
        r["tema_id"]: (r["area"], r["fase"], r["n"])
        for r in con.execute(
            f"""SELECT tm.id AS tema_id, a.nome AS area, tm.fase AS fase, COUNT(*) AS n
                FROM classificacoes c
                JOIN temas tm ON tm.id = c.tema_id
                JOIN areas a ON a.id = tm.area_id
                JOIN questoes q ON q.id = c.questao_id
                JOIN vestibulares v ON v.id = q.vestibular_id
                WHERE v.nome = 'univesp'{cond}
                GROUP BY tm.id""",
            p_banco,
        ).fetchall()
    }
    tentadas: dict[int, int] = defaultdict(int)
    for r in con.execute(
        f"""SELECT c.tema_id AS tema_id, COUNT(DISTINCT t.questao_id) AS n
            FROM tentativas t
            JOIN classificacoes c ON c.questao_id = t.questao_id
            JOIN temas tm ON tm.id = c.tema_id
            WHERE t.usuario = ? AND t.correta IS NOT NULL{cond}
            GROUP BY c.tema_id""",
        p_user,
    ).fetchall():
        tentadas[r["tema_id"]] = r["n"]
    por: dict[tuple, list[int, int, int]] = defaultdict(lambda: [0, 0, 0])
    for tid, (area, fase, n) in banco.items():
        chave = (area, fase)
        por[chave][0] += 1
        por[chave][1] += n
        por[chave][2] += tentadas.get(tid, 0)
    out = []
    for (area, fase), (n_temas, n_banco, n_tent) in sorted(por.items()):
        out.append(
            {
                "area": area,
                "fase": fase,
                "n_temas": n_temas,
                "questoes_banca": n_banco,
                "tentadas": n_tent,
                "cobertura": round(100 * n_tent / n_banco, 1) if n_banco else None,
            }
        )
    return out


def gaps(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Temas cobrados nas provas UNIVESP que o usuário ainda não iniciou,
    ordenados pela recorrência real (mais cobrados primeiro)."""
    cond, params = "", ["univesp", usuario]
    if area_id is not None:
        cond = " AND a.id = ?"
        params.append(area_id)
    rows = con.execute(
        f"""SELECT a.id AS area_id, a.nome AS area, t.id AS tema_id, t.nome AS tema,
                   t.fase AS fase, COUNT(*) AS n
            FROM classificacoes c
            JOIN temas t ON t.id = c.tema_id
            JOIN areas a ON a.id = t.area_id
            JOIN questoes q ON q.id = c.questao_id
            JOIN vestibulares v ON v.id = q.vestibular_id
            WHERE v.nome = ?
              AND NOT EXISTS (SELECT 1 FROM niveis_usuarios nu
                              WHERE nu.tema_id = t.id AND nu.usuario = ?
                                AND nu.contagem > 0){cond}
            GROUP BY t.id
            ORDER BY n DESC""",
        params,
    ).fetchall()
    return [
        {
            "area": r["area"],
            "tema": r["tema"],
            "fase": r["fase"],
            "questoes_banca": r["n"],
        }
        for r in rows
    ]


def retencao(
    con: sqlite3.Connection,
    usuario: str,
    agora: dt.datetime | None = None,
    area_id: int | None = None,
) -> list[dict]:
    """Retrievability (R) atual de cada tema pelo FSRS, do mais esquecido ao
    mais fresco."""
    agora = agora or dt.datetime.now(dt.UTC)
    cond, params = "", [usuario]
    if area_id is not None:
        cond = " AND t.area_id = ?"
        params.append(area_id)
    out = []
    for r in con.execute(
        f"""SELECT f.tema_id, f.estado, f.repos, f.lapses, f.vencimento, f.card_json,
                  t.nome AS tema, t.fase AS fase, a.nome AS area
           FROM fsrs_estados f
           JOIN temas t ON t.id = f.tema_id
           JOIN areas a ON a.id = t.area_id
           WHERE f.usuario = ? AND f.card_json IS NOT NULL{cond}""",
        params,
    ).fetchall():
        try:
            r_ = _scheduler.get_card_retrievability(
                Card.from_json(r["card_json"]), agora
            )
        except (ValueError, TypeError):
            r_ = None
        out.append(
            {
                "area": r["area"],
                "tema": r["tema"],
                "fase": r["fase"],
                "estado": r["estado"],
                "repos": r["repos"],
                "lapses": r["lapses"],
                "vencimento": _status_vencimento(r["vencimento"], agora),
                "r": round(r_, 2) if r_ is not None else None,
            }
        )
    out.sort(key=lambda x: (x["r"] is None, x["r"] or 1.0))
    return out


def b_vs_theta(
    con: sqlite3.Connection, usuario: str, area_id: int | None = None
) -> list[dict]:
    """Dificuldade média (b, Rasch) das questões tentadas vs habilidade θ da
    área. `delta` = θ − b: positivo → questões abaixo do seu nível."""
    cond, params = "", [usuario]
    if area_id is not None:
        cond = " AND a.id = ?"
        params.append(area_id)
    out = []
    for r in con.execute(
        f"""SELECT a.id AS area_id, a.nome AS area, AVG(ip.b) AS b_medio,
                   COUNT(*) AS n, h.theta AS theta
            FROM tentativas t
            JOIN questoes q ON q.id = t.questao_id
            JOIN classificacoes c ON c.questao_id = q.id
            JOIN areas a ON a.id = c.area_id
            JOIN item_params ip ON ip.questao_id = q.id
            JOIN habilidades h ON h.area_id = a.id AND h.usuario = t.usuario
            WHERE t.usuario = ? AND t.correta IS NOT NULL AND h.n_obs > 0{cond}
            GROUP BY a.id
            ORDER BY a.nome""",
        params,
    ).fetchall():
        bm, th = r["b_medio"], r["theta"]
        out.append(
            {
                "area": r["area"],
                "b_medio": round(bm, 2) if bm is not None else None,
                "theta": round(th, 2) if th is not None else None,
                "delta": round(th - bm, 2) if bm is not None and th is not None else None,
                "n": r["n"],
            }
        )
    return out
