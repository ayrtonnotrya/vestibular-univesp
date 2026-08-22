"""Nível do usuário por tema (`niveis_usuarios`): score, racha, contagem.

Complemento pragmático ao Rasch por área: enquanto `habilidades.theta` é o nível
MAP por área, `niveis_usuarios` guarda, para cada `(usuario, tema)`, a proporção
empírica de acertos (`score`, média móvel), a sequência atual de acertos
(`racha`) e o número de tentativas (`contagem`). Serve para seleção por tema,
progresso e feedback ("você está fraco em Funções").
"""

import datetime as dt
import sqlite3

from .rasch import _logit

MIN_TENTATIVAS_TEMA = 2  # mínimo de tentativas para o nível por tema guiar a seleção


def _temas_da_questao(con: sqlite3.Connection, questao_id: int) -> list[int]:
    rows = con.execute(
        """SELECT DISTINCT t.id AS tema_id
           FROM classificacoes c JOIN temas t ON t.id = c.tema_id
           WHERE c.questao_id = ?""",
        (questao_id,),
    ).fetchall()
    return [r["tema_id"] for r in rows]


def nivel_tema(con: sqlite3.Connection, usuario: str, tema_id: int) -> dict | None:
    r = con.execute(
        "SELECT score, racha, contagem, ultima_data FROM niveis_usuarios WHERE usuario = ? AND tema_id = ?",
        (usuario, tema_id),
    ).fetchone()
    return dict(r) if r else None


def atualiza(
    con: sqlite3.Connection,
    usuario: str,
    questao_id: int,
    correta: bool | None,
    agora: dt.datetime | None = None,
) -> list[dict]:
    """Atualiza score/racha/contagem por tema da questão. Retorna os níveis novos."""
    if correta is None:
        return []
    agora = agora or dt.datetime.now(dt.UTC)
    resultado = []
    for tema_id in _temas_da_questao(con, questao_id):
        nivel = nivel_tema(con, usuario, tema_id)
        score, contagem = (nivel["score"], nivel["contagem"]) if nivel else (0.5, 0)
        novo_n = contagem + 1
        y = 1.0 if correta else 0.0
        score = score + (y - score) / novo_n
        racha = ((nivel["racha"] if nivel else 0) + 1) if correta else 0
        con.execute(
            """INSERT INTO niveis_usuarios(usuario, tema_id, score, racha, contagem, ultima_data)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(usuario, tema_id) DO UPDATE SET
                 score = excluded.score,
                 racha = excluded.racha,
                 contagem = excluded.contagem,
                 ultima_data = excluded.ultima_data""",
            (usuario, tema_id, score, racha, novo_n, agora.isoformat()),
        )
        resultado.append(
            {"tema_id": tema_id, "score": score, "racha": racha, "contagem": novo_n}
        )
    con.commit()
    return resultado


def habilidade_tema(
    con: sqlite3.Connection,
    usuario: str,
    tema_id: int,
    min_tentativas: int = MIN_TENTATIVAS_TEMA,
) -> dict:
    """Devolve a habilidade a usar na seleção do tema.

    Com dados suficientes no tema, usa o nível por tema (`base="tema"`, logit do
    score); senão `base="area"` (valor None) e o chamador usa o θ da área."""
    nivel = nivel_tema(con, usuario, tema_id)
    if nivel and nivel["contagem"] >= min_tentativas:
        return {
            "base": "tema",
            "valor": _logit(nivel["score"]),
            "score": nivel["score"],
            "contagem": nivel["contagem"],
        }
    return {
        "base": "area",
        "valor": None,
        "score": nivel["score"] if nivel else None,
        "contagem": nivel["contagem"] if nivel else 0,
    }


def niveis_usuario(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Todos os níveis por tema do usuário, com nomes de tema/área."""
    rows = con.execute(
        """SELECT n.tema_id, t.nome AS tema, a.id AS area_id, a.nome AS area,
                  n.score, n.racha, n.contagem, n.ultima_data
           FROM niveis_usuarios n
           JOIN temas t ON t.id = n.tema_id
           JOIN areas a ON a.id = t.area_id
           WHERE n.usuario = ?
           ORDER BY a.nome, t.nome""",
        (usuario,),
    ).fetchall()
    return [dict(r) for r in rows]
