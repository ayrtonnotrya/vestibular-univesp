"""Rasch 1PL: habilidade θ por área e dificuldade b por item (calibração).

- `b` (item): herdado da IA (difficultade = −logit do score low-thinking),
  ou 0 neutro; suavizado com respostas reais:
  b = (κ·b0 + n_obs·logit(1−p_emp))/(κ + n_obs)  (p_emp = taxa de acerto).
- `theta` (área): estimativa MAP com prior gaussiana N(0, 2). Sem solução
  fechada para 1PL -> Newton-Raphson sobre a verossimilhança + prior.
"""
import math
import sqlite3

PRIOR_VAR = 2.0
KAPPA = 4.0


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


def estima_theta(respostas: list[tuple[float, int]]) -> tuple[float, float, int]:
    """MAP de θ para uma lista de (b, correto) com prior N(0, PRIOR_VAR).

    Retorna (theta, var_theta, n_obs). Redações/sem item param são filtradas antes.
    """
    if not respostas:
        return 0.0, PRIOR_VAR, 0
    theta = 0.0
    for _ in range(30):
        g = -theta / PRIOR_VAR
        h = -1.0 / PRIOR_VAR
        for b, y in respostas:
            p = _sigmoid(theta - b)
            g += y - p
            h -= p * (1 - p)
        if h == 0:
            break
        passo = -g / h
        theta += passo
        if abs(passo) < 1e-6:
            break
    var = 1.0 / (-h) if h < 0 else PRIOR_VAR
    return theta, var, len(respostas)


def _respostas_area(con: sqlite3.Connection, usuario: str, area_id: int) -> list[tuple[float, int]]:
    rows = con.execute(
        """SELECT ip.b AS b, t.correta AS correta
           FROM tentativas t
           JOIN classificacoes c ON c.questao_id = t.questao_id
           JOIN item_params ip ON ip.questao_id = t.questao_id
           WHERE t.usuario = ? AND c.area_id = ? AND t.correta IS NOT NULL""",
        (usuario, area_id),
    ).fetchall()
    return [(r["b"], int(r["correta"])) for r in rows]


def atualiza_habilidades(con: sqlite3.Connection, usuario: str, area_id: int) -> dict:
    """Reestima θ da área após uma resposta e grava em `habilidades`."""
    respostas = _respostas_area(con, usuario, area_id)
    theta, var, n = estima_theta(respostas)
    con.execute(
        """INSERT INTO habilidades(usuario, area_id, theta, var_theta, n_obs)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(usuario, area_id) DO UPDATE SET
             theta = excluded.theta,
             var_theta = excluded.var_theta,
             n_obs = excluded.n_obs""",
        (usuario, area_id, theta, var, n),
    )
    con.commit()
    return {"area_id": area_id, "theta": theta, "var": var, "n_obs": n}


def atualiza_item_b(con: sqlite3.Connection, questao_id: int) -> dict:
    """Suaviza o b do item com as tentativas reais da questão."""
    b0 = con.execute(
        "SELECT b FROM item_params WHERE questao_id = ?", (questao_id,)
    ).fetchone()
    if b0 is None:
        return {"questao_id": questao_id, "b": 0.0}
    b0 = b0["b"]
    row = con.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(correta), 0) AS ac FROM tentativas WHERE questao_id = ?",
        (questao_id,),
    ).fetchone()
    n = row["n"]
    p_emp = (row["ac"] + 0.5) / (n + 1) if n else None
    if n == 0:
        b = b0
    else:
        b = (KAPPA * b0 + n * (-_logit(p_emp))) / (KAPPA + n)
    con.execute(
        "UPDATE item_params SET b = ?, n_obs = ? WHERE questao_id = ?",
        (b, n, questao_id),
    )
    con.commit()
    return {"questao_id": questao_id, "b": b, "n_obs": n}


def theta_area(con: sqlite3.Connection, usuario: str, area_id: int) -> float:
    r = con.execute(
        "SELECT theta FROM habilidades WHERE usuario = ? AND area_id = ?",
        (usuario, area_id),
    ).fetchone()
    return r["theta"] if r else 0.0