"""Servidor MCP (SSE) do motor de estudo para o AnythingLLM.

Expõe como ferramentas MCP o motor de estudo (`vestibular.estudo.motiva`) e o
acervo (SQLite `data/vestibular.db`). Roda no docker-compose como
`vestibular-mcp` na rede interna `web`; o AnythingLLM conecta por
`http://vestibular-mcp:8891/sse`.

A app FastAPI envolve o transport SSE do FastMCP (`/sse`, `/messages/`) e
acrescenta documentação automática (`/openapi.json`, `/docs`) e a rota REST
convencional `POST /api/consultar` para clientes que não falam MCP nativo
(ex.: Gemini Web), com CORS liberado para o navegador.

Toda tool devolve uma STRING JSON (documento único) — o FastMCP entregaria
listas como blocos separados, o que quebra o parse no cliente.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from vestibular.estudo import frequencia, motiva
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
def responder(
    usuario: str,
    questao_id: int,
    alternativa: str,
    grau_certeza: str | None = None,
    causa_erro: str | None = None,
    sintese_ativa: str | None = None,
) -> str:
    """Registra a alternativa do aluno e recalibra FSRS/θ/nível por tema/b.
    Retorna JSON com o veredito (correta), o gabarito oficial, os novos níveis
    por tema e o id da tentativa. `alternativa` é a letra escolhida (a..e).
    Opcionais do caderno de erros: `grau_certeza` ('conviccao'|'duvida'|'chute'),
    `causa_erro` ('teoria'|'pegadinha'|'atencao') e `sintese_ativa` (1-2 frases)."""
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
        return _json(
            motiva.responder(
                con,
                usuario,
                questao_id,
                alt,
                grau_certeza=grau_certeza,
                causa_erro=causa_erro,
                sintese_ativa=sintese_ativa,
            )
        )


@mcp.tool()
def anotar_erro(
    tentativa_id: int,
    causa_erro: str | None = None,
    sintese_ativa: str | None = None,
) -> str:
    """Preenche o caderno de erros de uma tentativa já respondida (útil quando o
    aluno responde primeiro e anota depois): `causa_erro`
    ('teoria'|'pegadinha'|'atencao') e `sintese_ativa` (1-2 frases). Retorna
    JSON com 'atualizado': true/false (false = tentativa inexistente)."""
    with connect() as con:
        atualizado = motiva.anotar_erro(con, tentativa_id, causa_erro, sintese_ativa)
    return _json({"tentativa_id": tentativa_id, "atualizado": atualizado})


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
def questoes_respondidas(usuario: str = "eu") -> str:
    """Histórico de respostas do usuário: cada tentativa com exame, número,
    resposta dada, se o gabarito é null (anulada), o acerto (0/1 | null se
    anulada), data, detalhe (JSON) e o caderno de erros (grau_certeza,
    causa_erro, sintese_ativa — null se não preenchidos). Retorna JSON (array
    por tentativa)."""
    with connect() as con:
        rows = con.execute(
            """SELECT t.id, t.questao_id, q.exame_label, q.numero, q.enunciado,
                      t.resposta, q.gabarito, q.anulada, t.correta, t.data, t.detalhe,
                      t.grau_certeza, t.causa_erro, t.sintese_ativa
               FROM tentativas t
               JOIN questoes q ON q.id = t.questao_id
               WHERE t.usuario = ?
               ORDER BY t.data, t.id""",
            (usuario,),
        ).fetchall()
    return _json([dict(r) for r in rows])


@mcp.tool()
def relatorio_provas(
    vestibular: str = "univesp", nivel: str = "todos", limite: int = 0
) -> str:
    """Relatório de frequência das provas (padrão UNIVESP): contagens reais de
    questões por área, tema e exame, com a proporção do total de classificações.
    Escopo em `vestibular`: 'univesp' | 'fuvest' | 'todos'. Granularidade em
    `nivel`: 'todos' (resumo + área + tema + por exame), 'area', 'tema' ou
    'exame'; `limite` > 0 corta os rankings área/tema nos top-N (0 = todos).
    Inclui redação (área 'Redação'); uma questão pode contar em mais de um
    tema. Útil para escolher as áreas mais cobradas. Retorna JSON (documento
    único)."""
    limite = max(0, min(int(limite or 0), frequencia.LIMITE_MAX))
    with connect() as con:
        return _json(frequencia.relatorio(con, vestibular, nivel, limite))


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


class ConsultarRequest(BaseModel):
    """Corpo do POST /api/consultar: seleciona uma tool pelo nome."""

    tool: str = Field(description="Nome da tool a executar (ver GET /api/tools)")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Argumentos da tool, ex.: {'usuario': 'eu'}"
    )


TOOLS: dict[str, Callable[..., str]] = {
    "proxima_questao": proxima_questao,
    "responder": responder,
    "anotar_erro": anotar_erro,
    "progresso": progresso,
    "niveis_por_tema": niveis_por_tema,
    "questoes_respondidas": questoes_respondidas,
    "relatorio_provas": relatorio_provas,
    "listar_exames": listar_exames,
    "buscar_questoes": buscar_questoes,
    "gabarito_exame": gabarito_exame,
}


def main() -> None:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware

    mcp_app = mcp.sse_app()

    app = FastAPI(
        title="vestibular-mcp",
        description=(
            "Motor de estudo (ZPD) e acervo de vestibulares. "
            "Clientes MCP nativos usam /sse (SSE); REST convencional em "
            "POST /api/consultar. Documentação: /docs e /openapi.json."
        ),
        version="1.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        allow_credentials=False,
    )

    @app.get("/.well-known/mcp.json")
    def well_known() -> dict:
        """Descoberta do servidor MCP (SSE) para clientes que seguem a spec."""
        return {
            "name": "vestibular",
            "version": "1.0.0",
            "endpoints": [{"name": "sse", "url": "/sse", "transport": "sse"}],
        }

    @app.get("/api/tools")
    def listar_tools() -> list[dict[str, str]]:
        """Lista as tools disponíveis para POST /api/consultar."""
        return [
            {"nome": nome, "descricao": (func.__doc__ or "").strip()}
            for nome, func in TOOLS.items()
        ]

    @app.post("/api/consultar", response_model=None)
    def consultar(req: ConsultarRequest):
        """Executa uma tool (mesma regra de negócio do MCP) e devolve JSON."""
        func = TOOLS.get(req.tool)
        if func is None:
            raise HTTPException(
                status_code=404, detail=f"ferramenta desconhecida: {req.tool}"
            )
        try:
            resultado = func(**req.params)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"parâmetros inválidos: {exc}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return json.loads(resultado)

    app.mount("/", mcp_app)

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8891)


if __name__ == "__main__":
    main()
