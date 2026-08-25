"""Consultas de estatísticas de desempenho para a página "Estatísticas".

Tudo lido de `data/vestibular.db` (SQL direto, sem dependência além de
sqlite3). As funções devolvem listas de dicts prontas para virar DataFrame
na camada Streamlit (`app/study.py`).
"""

import datetime as dt
import sqlite3
from collections import defaultdict


def _de_faixa(dia_iso: str) -> str:
    """Dia (data[:10]) a partir de um timestamp ISO, exibido como dd/mm."""
    try:
        d = dt.datetime.fromisoformat(dia_iso)
    except ValueError:
        return dia_iso
    return d.strftime("%d/%m")


def _temas_areas(con: sqlite3.Connection) -> dict[int, dict]:
    rows = con.execute(
        """SELECT t.id, t.nome AS tema, a.nome AS area
           FROM temas t JOIN areas a ON a.id = t.area_id"""
    ).fetchall()
    return {r["id"]: {"tema": r["tema"], "area": r["area"]} for r in rows}


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


def por_dia(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Aproveitamento por dia: dia, tentativas, acertos, %."""
    por: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        "SELECT data, correta FROM tentativas WHERE usuario = ?", (usuario,)
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
    acur = defaultdict(lambda: [0, 0])
    for r in con.execute(
        """SELECT c.area_id AS area_id, t.correta AS correta
           FROM tentativas t
           JOIN classificacoes c ON c.questao_id = t.questao_id
           WHERE t.usuario = ? AND t.correta IS NOT NULL""",
        (usuario,),
    ):
        acur[r["area_id"]][1] += 1
        if r["correta"]:
            acur[r["area_id"]][0] += 1
    out = []
    for r in rows:
        ac, tot = acur.get(r["area_id"], [0, 0])
        out.append(
            {
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
                  t.nome AS tema, a.nome AS area
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
                "score": round(r["score"], 2),
                "racha": r["racha"],
                "tentativas": r["contagem"],
                "estado": (f["estado"] if f else None) or "—",
                "lapses": (f["lapses"] if f else 0) or 0,
                "vencimento": _status_vencimento(f["vencimento"] if f else None, agora),
            }
        )
    return out


def por_exame(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Aproveitamento por exame (label)."""
    por: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in con.execute(
        """SELECT q.exame_label AS exame, t.correta AS correta
           FROM tentativas t JOIN questoes q ON q.id = t.questao_id
           WHERE t.usuario = ?""",
        (usuario,),
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
    con: sqlite3.Connection, usuario: str, agora: dt.datetime | None = None
) -> list[dict]:
    """Fila de revisão FSRS: temas com estado, ordenados por status e data."""
    agora = agora or dt.datetime.now(dt.UTC)
    out = []
    for r in con.execute(
        """SELECT f.tema_id, f.estado, f.repos, f.lapses, f.vencimento,
                  t.nome AS tema, a.nome AS area
           FROM fsrs_estados f
           JOIN temas t ON t.id = f.tema_id
           JOIN areas a ON a.id = t.area_id
           WHERE f.usuario = ? AND f.vencimento IS NOT NULL""",
        (usuario,),
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


def historico(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Cada tentativa com exame, questão, resposta, gabarito e área/tema."""
    temas_areas = _temas_areas(con)
    klass: dict[int, list[int]] = defaultdict(list)
    for r in con.execute("SELECT questao_id, tema_id FROM classificacoes"):
        klass[r["questao_id"]].append(r["tema_id"])
    out = []
    for r in con.execute(
        """SELECT t.id, t.data, t.questao_id, q.exame_label, q.numero, t.resposta,
                  q.gabarito, q.anulada, t.correta
           FROM tentativas t JOIN questoes q ON q.id = t.questao_id
           WHERE t.usuario = ? ORDER BY t.data, t.id""",
        (usuario,),
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
                "areas": ", ".join(sorted(areas)),
                "temas": ", ".join(sorted(temas)),
            }
        )
    return out
