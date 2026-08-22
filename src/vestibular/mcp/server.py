"""Servidor MCP (SSE) do motor de estudo para o AnythingLLM.

Expõe como ferramentas MCP o motor de estudo (`vestibular.estudo.motiva`) e o
acervo (SQLite `data/vestibular.db`). Roda no docker-compose como
`vestibular-mcp` na rede interna `web`; o AnythingLLM conecta por
`http://vestibular-mcp:8891/sse`.

Toda tool devolve uma STRING JSON (documento único) — o FastMCP entregaria
listas como blocos separados, o que quebra o parse no cliente.
"""

import datetime as dt
import json

from mcp.server.fastmcp import FastMCP

from vestibular.estudo import motiva
from vestibular.estudo.db import connect

mcp = FastMCP("vestibular", host="0.0.0.0", port=8891)


def _default(obj):
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    return str(obj)


def _json(result) -> str:
    return json.dumps(result, ensure_ascii=False, default=_default)


def _alternativas(texto: str | None) -> dict | None:
    if not texto:
        return None
    return json.loads(texto)


def _json_lista(texto: str | None) -> list:
    if not texto:
        return []
    try:
        lst = json.loads(texto)
        return lst if isinstance(lst, list) else []
    except json.JSONDecodeError:
        return []


@mcp.tool()
def proxima_questao(usuario: str = "eu") -> str:
    """Próxima questão do ZPD (FSRS + Rasch + nível por tema). Retorna JSON com
    enunciado, textos_de_apoio e midia (texto da questão COMPLETO — o enunciado
    curto é complementado por esses campos), alternativas, tema, área, nível
    base e gabarito.

    IMPORTANTE: não revelar o gabarito ao aluno antes de ele responder; use o
    campo só para conferir a resposta em `responder`."""
    with connect() as con:
        return _json(motiva.proxima_questao(con, usuario))


@mcp.tool()
def responder(usuario: str, questao_id: int, alternativa: str) -> str:
    """Registra a alternativa do aluno e recalibra FSRS/θ/nível por tema/b.
    Retorna JSON com o veredito (correta), o gabarito oficial e os novos níveis
    por tema. `alternativa` é a letra escolhida (a, b, c, d, e)."""
    alt = (alternativa or "").strip().lower()
    if not alt:
        raise ValueError("informe a alternativa respondida (letra, ex.: 'a')")
    with connect() as con:
        row = con.execute(
            "SELECT alternativas FROM questoes WHERE id = ?", (questao_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"questão {questao_id} inexistente no acervo")
        opcoes = _alternativas(row["alternativas"])
        if opcoes and alt not in opcoes:
            raise ValueError(
                f"alternativa '{alt}' inválida; opções: {', '.join(sorted(opcoes))}"
            )
        return _json(motiva.responder(con, usuario, questao_id, alt))


@mcp.tool()
def progresso(usuario: str = "eu") -> str:
    """Resumo por área: θ (habilidade), nº de observações, temas vencidos
    (FSRS) e os níveis por tema do usuário. Retorna JSON (array por área)."""
    with connect() as con:
        return _json(motiva.progresso(con, usuario))


@mcp.tool()
def niveis_por_tema(usuario: str = "eu") -> str:
    """Nível por tema do usuário: score (0..1), racha e contagem de tentativas,
    com nomes de área/tema. Retorna JSON (array por tema)."""
    with connect() as con:
        return _json(motiva.niveis_por_tema(con, usuario))


@mcp.tool()
def listar_exames() -> str:
    """Lista os exames no acervo: label, total de questões, objetivas e
    redações. Retorna JSON (array por exame)."""
    with connect() as con:
        rows = con.execute(
            """SELECT exame_label, COUNT(*) AS total,
                      SUM(CASE WHEN tipo = 'objetiva' THEN 1 ELSE 0 END) AS objetivas,
                      SUM(CASE WHEN tipo = 'redacao' THEN 1 ELSE 0 END) AS redacoes
               FROM questoes GROUP BY exame_label ORDER BY exame_label"""
        ).fetchall()
    return _json([dict(r) for r in rows])


@mcp.tool()
def buscar_questoes(
    exame: str | None = None,
    numero: int | None = None,
    area: str | None = None,
    assunto: str | None = None,
    limite: int = 20,
) -> str:
    """Busca questões no acervo por exame (ex.: 'fuvest_2024'), número, área ou
    assunto (tema do catálogo). Retorna JSON (array) com enunciado, textos_de_apoio
    e midia (texto da questão COMPLETO — o enunciado curto é complementado por
    esses campos), alternativas, gabarito, temas e áreas; `limite` vai até 100."""
    limite = max(1, min(int(limite), 100))
    condicoes = []
    params: list = []
    if exame:
        condicoes.append("q.exame_label = ?")
        params.append(exame)
    if numero is not None:
        condicoes.append("q.numero = ?")
        params.append(numero)
    if area:
        condicoes.append(
            """EXISTS (SELECT 1 FROM classificacoes c
                        JOIN temas t ON t.id = c.tema_id
                        JOIN areas a ON a.id = t.area_id
                        WHERE c.questao_id = q.id AND a.nome = ?)"""
        )
        params.append(area)
    if assunto:
        condicoes.append(
            """EXISTS (SELECT 1 FROM classificacoes c
                        JOIN temas t ON t.id = c.tema_id
                        WHERE c.questao_id = q.id AND t.nome = ?)"""
        )
        params.append(assunto)
    where = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    with connect() as con:
        rows = con.execute(
            f"""SELECT q.id, q.exame_label, q.ano, q.numero, q.tipo, q.enunciado,
                       q.textos_de_apoio, q.midia, q.alternativas, q.gabarito, q.anulada,
                       (SELECT GROUP_CONCAT(DISTINCT a.nome) FROM classificacoes c
                          JOIN temas t ON t.id = c.tema_id
                          JOIN areas a ON a.id = t.area_id
                          WHERE c.questao_id = q.id) AS areas,
                       (SELECT GROUP_CONCAT(DISTINCT t.nome) FROM classificacoes c
                          JOIN temas t ON t.id = c.tema_id
                          WHERE c.questao_id = q.id) AS temas
                FROM questoes q
                {where}
                ORDER BY q.exame_label, q.numero
                LIMIT ?""",
            [*params, limite],
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["textos_de_apoio"] = _json_lista(d.pop("textos_de_apoio"))
        d["midia"] = _json_lista(d.pop("midia"))
        d["alternativas"] = _alternativas(d.pop("alternativas"))
        out.append(d)
    return _json(out)


@mcp.tool()
def gabarito_exame(exame: str) -> str:
    """Gabarito oficial de um exame: número × letra (null = redação/anulada).
    Retorna JSON (array por questão)."""
    with connect() as con:
        rows = con.execute(
            """SELECT numero, gabarito, anulada FROM questoes
               WHERE exame_label = ? AND tipo = 'objetiva'
               ORDER BY numero""",
            (exame,),
        ).fetchall()
    if not rows:
        raise ValueError(f"exame '{exame}' sem questões objetivas no acervo")
    return _json([dict(r) for r in rows])


def main() -> None:
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()