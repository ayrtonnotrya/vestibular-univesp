"""Frequência real de ocorrência de cada tema nas provas do vestibular.

Prior empírico (o "relatório de temas") usado na seleção de tema do modo
estudar: com poucos dados do usuário, a probabilidade de cada disciplina surgir
é ditada pela frequência observada nas provas; conforme o usuário acumula
revisões, o FSRS por tema passa a assumir (ver `motiva.proxima_questao`).

A fonte é o próprio banco (contagens de `classificacoes` de questões UNIVESP),
o que garante junção exata por `tema_id` e consistência com o que o motor de
estudo lê — sem risco de casar nomes de assunto.
"""

import sqlite3

# Escopo do relatório; adicionar outros vestibulares à medida que tiverem
# relatório de frequência próprio.
VESTIBULARES = ("univesp",)

# Suavização de Laplace: garante prior > 0 a temas raríssimos/ausentes, para que
# nenhum tema do catálogo fique inalcançável por completo no frio do contágio.
ALFA_SMOOTH = 1.0

# Piso do prior para temas que não aparecem no escopo (nunca caíram no exame):
# mantém uma chance mínima, evitando zero absoluto.
PRIOR_FLOOR = 0.01


# Escopos aceitos pelo relatório; "todos" não filtra vestibular.
ESCOPOS = {"univesp": ("univesp",), "fuvest": ("fuvest",), "todos": None}

# Níveis de agregação do relatório.
NIVEIS = ("todos", "area", "tema", "exame")

# Teto do parâmetro `limite` nos rankings (0 = sem corte).
LIMITE_MAX = 500


def _ocorrencias_por_tema(
    con: sqlite3.Connection, vestibulares: tuple[str, ...]
) -> dict[int, int]:
    rows = con.execute(
        f"""SELECT c.tema_id AS tema_id, COUNT(*) AS n
            FROM classificacoes c
            JOIN questoes q ON q.id = c.questao_id
            JOIN vestibulares v ON v.id = q.vestibular_id
            WHERE v.nome IN ({",".join("?" for _ in vestibulares)})
            GROUP BY c.tema_id""",
        list(vestibulares),
    ).fetchall()
    return {r["tema_id"]: r["n"] for r in rows}


def _clausula_escopo(vestibular: str) -> tuple[str, list]:
    """Cláusula WHERE de escopo (COMO 'AND v.nome IN (...)') + parâmetros."""
    vestibulares = ESCOPOS[vestibular]
    if not vestibulares:
        return "", []
    marcas = ",".join("?" for _ in vestibulares)
    return f"AND v.nome IN ({marcas})", list(vestibulares)


def _scopo_questoes(con: sqlite3.Connection, vestibular: str) -> list[dict]:
    """Linhas {exame, area, tema_id, tema, n} das classificações do escopo."""
    filtro, params = _clausula_escopo(vestibular)
    return con.execute(
        f"""SELECT q.exame_label AS exame, a.nome AS area,
                   t.id AS tema_id, t.nome AS tema, COUNT(*) AS n
            FROM classificacoes c
            JOIN questoes q ON q.id = c.questao_id
            JOIN vestibulares v ON v.id = q.vestibular_id
            JOIN temas t ON t.id = c.tema_id
            JOIN areas a ON a.id = t.area_id
            WHERE 1 = 1 {filtro}
            GROUP BY q.exame_label, a.nome, t.id""",
        params,
    ).fetchall()


def relatorio(
    con: sqlite3.Connection,
    vestibular: str = "univesp",
    nivel: str = "todos",
    limite: int = 0,
) -> dict:
    """Relatório de frequência real das provas, com nomes.

    Contagens de `classificacoes` (uma questão pode ter mais de um tema; inclui
    redações). `vestibular`: 'univesp' | 'fuvest' | 'todos'. `nivel` controla a
    granularidade ('todos' inclui área, tema e por exame). `limite` > 0 corta
    os rankings área/tema nos top-N (0 = todos). `proporcao` é a fatia do total
    de classificações do escopo.
    """
    if vestibular not in ESCOPOS:
        raise ValueError(f"escopo inválido: {vestibular} (use {', '.join(ESCOPOS)})")
    if nivel not in NIVEIS:
        raise ValueError(f"nível inválido: {nivel} (use {', '.join(NIVEIS)})")
    limite = max(0, min(int(limite or 0), LIMITE_MAX))

    linhas = _scopo_questoes(con, vestibular)
    total = sum(r["n"] for r in linhas)

    filtro, params = _clausula_escopo(vestibular)
    exames = con.execute(
        f"""SELECT q.exame_label AS exame, COUNT(*) AS questoes
            FROM questoes q JOIN vestibulares v ON v.id = q.vestibular_id
            WHERE 1 = 1 {filtro}
            GROUP BY q.exame_label ORDER BY q.exame_label""",
        params,
    ).fetchall()

    def prop(n: int) -> float:
        return round(n / total, 4) if total else 0.0

    areas: dict[str, int] = {}
    temas: dict[tuple[str, str], int] = {}
    por_exame: dict[tuple[str, str], int] = {}
    for r in linhas:
        areas[r["area"]] = areas.get(r["area"], 0) + r["n"]
        chave_tema = (r["area"], r["tema"])
        temas[chave_tema] = temas.get(chave_tema, 0) + r["n"]
        chave_exame = (r["exame"], r["area"])
        por_exame[chave_exame] = por_exame.get(chave_exame, 0) + r["n"]

    ranking_areas = sorted(
        ({"area": a, "questoes": n, "proporcao": prop(n)} for a, n in areas.items()),
        key=lambda x: (-x["questoes"], x["area"]),
    )
    ranking_temas = sorted(
        (
            {"area": a, "tema": tm, "questoes": n, "proporcao": prop(n)}
            for (a, tm), n in temas.items()
        ),
        key=lambda x: (-x["questoes"], x["area"], x["tema"]),
    )
    matriz_exame = sorted(
        ({"exame": ex, "area": ar, "questoes": n} for (ex, ar), n in por_exame.items()),
        key=lambda x: (x["exame"], -x["questoes"]),
    )
    if limite:
        ranking_areas = ranking_areas[:limite]
        ranking_temas = ranking_temas[:limite]

    out = {
        "resumo": {
            "vestibular": vestibular,
            "n_exames": len(exames),
            "n_questoes": sum(r["questoes"] for r in exames),
            "n_classificacoes": total,
        },
        "exames": [dict(r) for r in exames],
    }
    if nivel in ("todos", "area", "tema"):
        out["areas"] = ranking_areas
    if nivel in ("todos", "tema"):
        out["temas"] = ranking_temas
    if nivel in ("todos", "exame"):
        out["por_exame"] = matriz_exame
    return out


def prior_por_tema(
    con: sqlite3.Connection, vestibulares: tuple[str, ...] = VESTIBULARES
) -> dict[int, float]:
    """Probabilidade a priori suavizada (Laplace) de cada tema cair numa prova.

    Devolve {tema_id: prob} em (0, 1]; temas ausentes do escopo ficam de fora.
    """
    ocorr = _ocorrencias_por_tema(con, vestibulares)
    total = sum(ocorr.values())
    n_temas = len(ocorr)
    if not ocorr or total == 0:
        return {}
    denom = total + ALFA_SMOOTH * n_temas
    return {tid: (n + ALFA_SMOOTH) / denom for tid, n in ocorr.items()}
