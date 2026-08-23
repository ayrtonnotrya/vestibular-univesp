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