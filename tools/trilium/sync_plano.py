"""Publica o plano de estudos (`data/assuntos.json`) como uma árvore de notas
no Trilium do usuário, via MCP (Streamable HTTP).

NÃO confundir com o vestibular-mcp do repositório: aquele é um SERVIDOR
FastMCP/SSE (motor de estudo p/ AnythingLLM). Este script é um CLIENTE
JSON-RPC (httpx) que fala com o MCP embutido do Trilium em `TRILIUM_URL`,
autenticado com um token ETAPI (`Authorization: Bearer <token>`).

Árvore gerada (1 raiz + 10 áreas + 41 módulos = 52 notas):

    Vestibular UNIVESP · Plano de estudos      #vestibularPlano #vestibularSync
    ├─ 1. Matemática                           #vestibularPlano #vestibularSync
    │                                          #vestibularArea=Matemática
    │  └─ Módulo 0: Nivelamento (Base ...)     #vestibularPlano #vestibularSync
    │                                          #vestibularArea=Matemática
    │                                          #vestibularModulo=1

Os `assuntos` viram itens de checklist (`- [ ] ...`) na nota do módulo; o
progresso marcado pelo usuário (`- [x]`) é preservado entre sincronizações.

Idempotência (re-executável):
- nota com `#vestibularPlano` casada por título:
  - com `#vestibularSync` → `set_note_content` só se o conteúdo difere
    (comparação tolerante a whitespace/fim-de-linha e ao estado dos checkboxes);
  - sem `#vestibularSync` → nota manual do usuário: NUNCA é tocada;
- não existente → `create_note` + `set_attribute` (labels);
- órfãs (com `#vestibularSync` fora do catálogo) → reportadas, sem excluir;
- `--force` reescreve o conteúdo de todas as notas gerenciadas (preservando
  os checkboxes marcados).

As tools são descobertas via `tools/list` no início; se alguma obrigatória
estiver ausente na versão instalada do Trilium, o script aborta antecipadamente
em vez de quebrar no meio do sync.

Configuração (`.env`, não versionado):
    TRILIUM_URL=http://trilium.dev.ime
    TRILIUM_TOKEN=<token ETAPI — Opções > ETAPI no Trilium>
    TRILIUM_PARENT_NOTE=root

Uso (host sem python; imagem com httpx/click):
    docker run --rm --network host --env-file .env \
      -v "$PWD":/work -w /work vestibular-app:latest \
      python tools/trilium/sync_plano.py --dry-run
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

LABEL_PLANO = "vestibularPlano"
LABEL_SYNC = "vestibularSync"
LABEL_AREA = "vestibularArea"
LABEL_MODULO = "vestibularModulo"

# Tools sem as quais dá pra saber que a versão instalada diverge do esperado.
OBRIGATORIAS = (
    "create_note",
    "set_note_content",
    "set_attribute",
    "search_notes",
    "get_child_notes",
    "get_note_content",
    "get_subtree",
)

# Protocolos de initialize em ordem de preferência; o servidor responde 400
# com o header `Mcp-Protocol-Version` quando não aceita um deles.
PROTOCOLOS = ("2025-06-18", "2025-03-26", "2024-11-05")

# Aliases de parâmetros por versão do Trilium (nome canônico -> alternativas).
ALIASES = {
    "noteId": ("note_id",),
    "parentNoteId": ("parentId", "parent_note_id"),
    "newTitle": ("title",),
}


class ErroMCP(Exception):
    """Erro de transporte/protocolo MCP."""


class ErroTool(Exception):
    """A tool executou mas devolveu `{"error": ...}`."""


class VersaoNaoSuportada(Exception):
    def __init__(self, versao):
        super().__init__(f"servidor MCP não aceita protocolVersion (usa {versao})")
        self.versao = versao


# ---------------------------------------------------------------- Builder


@dataclass
class Nota:
    titulo: str
    conteudo: str
    labels: list[str]
    filhos: list["Nota"] = field(default_factory=list)


def conteudo_raiz(plano):
    n_areas = len(plano["disciplinas"])
    n_modulos = sum(len(d["modulos"]) for d in plano["disciplinas"])
    return "\n".join(
        [
            "Plano de estudos gerado a partir de `data/assuntos.json` "
            + "(repositório vestibular-univesp).",
            "",
            f"- Público-alvo: {plano['publico_alvo']}",
            f"- Banca: {plano['foco_banca']}",
            f"- Estrutura: {n_areas} áreas, {n_modulos} módulos "
            + "(checklists de assuntos).",
            "",
            "Como usar: estude as áreas na ordem e marque `- [x]` nos assuntos "
            + "dominados. Notas com `#vestibularSync` são sobrescritas na próxima "
            + "sincronização; edições manuais em notas sem essa label são "
            + "preservadas.",
        ]
    )


def conteudo_area(area):
    return f"> {area['orientacao_pedagogica']}\n"


def conteudo_modulo(mod):
    return "\n".join(f"- [ ] {a}" for a in mod["assuntos"]) + "\n"


def construir_plano(dados):
    plano = dados["plano_de_estudos_vestibular"]
    raiz = Nota(
        titulo="Vestibular UNIVESP · Plano de estudos",
        conteudo=conteudo_raiz(plano),
        labels=[LABEL_PLANO, LABEL_SYNC],
    )
    for i, area in enumerate(plano["disciplinas"], 1):
        area_nota = Nota(
            titulo=f"{i}. {area['area']}",
            conteudo=conteudo_area(area),
            labels=[LABEL_PLANO, LABEL_SYNC, f"{LABEL_AREA}={area['area']}"],
        )
        for mod in area["modulos"]:
            area_nota.filhos.append(
                Nota(
                    titulo=mod["fase"],
                    conteudo=conteudo_modulo(mod),
                    labels=[
                        LABEL_PLANO,
                        LABEL_SYNC,
                        f"{LABEL_AREA}={area['area']}",
                        f"{LABEL_MODULO}={mod['ordem']}",
                    ],
                )
            )
        raiz.filhos.append(area_nota)
    return raiz


def contagem(raiz):
    areas = len(raiz.filhos)
    modulos = sum(len(f.filhos) for f in raiz.filhos)
    return 1 + areas + modulos, areas, modulos


def imprimir_arvore(raiz, destino):
    def walk(nota, nivel):
        destino.write(f"{'  ' * nivel}{nota.titulo}\n")
        for filho in nota.filhos:
            walk(filho, nivel + 1)

    walk(raiz, 0)


# ---------------------------------------------------- Comparação de conteúdo

_CHECKBOX = re.compile(r"^[-*]\s*\[([ xX])\]\s*(.*)$")
_BULLET = re.compile(r"^[-*+]\s+")
_ESCAPES = ("\\*", "\\_", "\\-", "\\#", "\\(", "\\)", "\\[", "\\]", "\\{", "\\}")


def _desescapar(texto):
    for esc in _ESCAPES:
        texto = texto.replace(esc, esc[1:])
    return texto.replace("&amp;", "&")


def _parte_linha(linha):
    s = linha.strip()
    m = _CHECKBOX.match(s)
    if m:
        return m.group(1).lower() == "x", _desescapar(m.group(2).strip())
    return None, _desescapar(_BULLET.sub("", s, count=1))


def linhas_significativas(md):
    """Linhas não vazias, sem borda de whitespace e sem o token do checkbox.

    O estado marcado/desmarcado (`- [x]` vs `- [ ]`) NUNCA gera diff: é o
    progresso do usuário, que vive no Trilium e não no catálogo.
    """
    out = []
    for linha in md.splitlines():
        if not linha.strip():
            continue
        _, texto = _parte_linha(linha)
        out.append(texto)
    return out


def conteudos_iguais(a, b):
    return linhas_significativas(a) == linhas_significativas(b)


def itens_marcados(md):
    marcados = set()
    for linha in md.splitlines():
        marcado, texto = _parte_linha(linha)
        if marcado:
            marcados.add(texto)
    return marcados


def aplicar_marcados(novo, atual):
    """Replica os checkboxes `- [x]` de `atual` sobre o conteúdo novo."""
    marcados = itens_marcados(atual)
    if not marcados:
        return novo
    out = []
    for linha in novo.splitlines():
        marcado, texto = _parte_linha(linha)
        if marcado is not None and texto in marcados:
            out.append(f"- [x] {texto}")
        else:
            out.append(linha)
    return "\n".join(out)


# --------------------------------------------------------------- Cliente MCP


class ClienteMCPTrilium:
    def __init__(self, url, token, timeout=30.0):
        self.url = url.rstrip("/") + "/mcp"
        self.token = token
        self.timeout = timeout
        self._id = 0
        self._tools: dict[str, dict] | None = None

    def _proximo_id(self):
        self._id += 1
        return self._id

    def _post(self, payload, metodo):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            resp = httpx.post(
                self.url, json=payload, headers=headers, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise ErroMCP(
                f"falha de rede ao falar com {self.url}: {exc}. "
                "DNS privado pode não entrar no contêiner — rode com "
                "--network host (ou --add-host trilium.dev.ime:<ip>)."
            ) from exc
        if resp.status_code == 401:
            raise ErroMCP(
                "401: token ETAPI rejeitado pelo Trilium. Crie/renove um token "
                "em Opções > ETAPI e atualize TRILIUM_TOKEN no .env."
            )
        if resp.status_code == 403:
            try:
                detalhe = resp.json().get("error")
            except ValueError:
                detalhe = resp.text[:200]
            raise ErroMCP(
                f"403: {detalhe or 'acesso negado'} — verifique se o MCP está "
                "habilitado nas opções do Trilium (AI / LLM)."
            )
        if resp.status_code == 400 and metodo == "initialize":
            versao = resp.headers.get("Mcp-Protocol-Version")
            if versao:
                raise VersaoNaoSuportada(versao)
        if resp.status_code >= 400:
            raise ErroMCP(f"HTTP {resp.status_code} de {self.url}: {resp.text[:300]}")
        return _ler_corpo(resp)

    def initialize(self):
        tentadas = set()
        versao = PROTOCOLOS[0]
        while True:
            try:
                self._chamar(
                    "initialize",
                    {
                        "protocolVersion": versao,
                        "capabilities": {},
                        "clientInfo": {"name": "sync-plano", "version": "1.0.0"},
                    },
                    metodo_rede="initialize",
                )
                return
            except VersaoNaoSuportada as exc:
                versao = exc.versao
                if versao in tentadas:
                    raise ErroMCP(
                        f"servidor MCP não negocia protocolVersion '{versao}'"
                    ) from exc
                tentadas.add(versao)

    def notificar(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload, method)

    def _chamar(self, method, params=None, metodo_rede=None):
        payload = {"jsonrpc": "2.0", "id": self._proximo_id(), "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._post(payload, metodo_rede or method)
        if "error" in msg:
            err = msg["error"]
            raise ErroMCP(
                f"JSON-RPC error {err.get('code')}: {err.get('message')} "
                f"(requestId={payload['id']}, método={method})"
            )
        return msg.get("result", {})

    def listar_tools(self):
        if self._tools is None:
            result = self._chamar("tools/list", {})
            self._tools = {
                t["name"]: t.get("inputSchema", {}) for t in result.get("tools", [])
            }
        return self._tools

    def validar_tools(self):
        presentes = self.listar_tools()
        ausentes = [t for t in OBRIGATORIAS if t not in presentes]
        if ausentes:
            raise ErroMCP(
                "tools obrigatórias ausentes no MCP do Trilium instalado: "
                + ", ".join(ausentes)
                + "\ndisponíveis: "
                + ", ".join(sorted(presentes))
            )

    def _adaptar(self, nome, args):
        if self._tools is None:
            self.listar_tools()
        props = (self._tools.get(nome) or {}).get("properties") or {}
        out = {}
        for chave, valor in args.items():
            if valor is None:
                continue
            if chave in props:
                out[chave] = valor
                continue
            for alias in ALIASES.get(chave, ()):
                if alias in props:
                    out[alias] = valor
                    break
            else:
                raise ErroMCP(
                    f"tool '{nome}' da versão instalada não aceita o parâmetro "
                    f"'{chave}' (schema diverge do esperado)"
                )
        return out

    def call_tool(self, nome, **args):
        args = self._adaptar(nome, args)
        result = self._chamar("tools/call", {"name": nome, "arguments": args})
        if result.get("isError"):
            raise ErroTool(f"{nome}: erro reportado pela tool")
        texto = "".join(
            c.get("text", "")
            for c in result.get("content") or []
            if c.get("type") == "text"
        )
        if not texto.strip():
            return result.get("structuredContent", {})
        try:
            valor = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErroMCP(
                f"resposta da tool '{nome}' não é JSON: {texto[:200]}"
            ) from exc
        if isinstance(valor, dict) and "error" in valor and "success" not in valor:
            raise ErroTool(f"{nome}: {valor['error']}")
        return valor


def _ler_corpo(resp):
    corpo = resp.text
    if "text/event-stream" in resp.headers.get("content-type", ""):
        msgs = []
        for linha in corpo.splitlines():
            linha = linha.strip()
            if linha.startswith("data:"):
                dado = linha[5:].strip()
                if dado and dado != "[DONE]":
                    msgs.append(json.loads(dado))
        if not msgs:
            raise ErroMCP("resposta SSE vazia do servidor MCP")
        return msgs[-1]
    if not corpo.strip():
        return {}
    msg = json.loads(corpo)
    if isinstance(msg, list):
        return msg[-1]
    return msg


# ------------------------------------------------------------- Sincronização


@dataclass
class Relatorio:
    criadas: list[str] = field(default_factory=list)
    atualizadas: list[str] = field(default_factory=list)
    inalteradas: list[str] = field(default_factory=list)
    manuais: list[str] = field(default_factory=list)
    orfas: list[str] = field(default_factory=list)

    def sujo(self):
        return bool(self.criadas or self.atualizadas or self.orfas)


def buscar_por_label(cliente, label, parent_id):
    r = cliente.call_tool(
        "search_notes",
        query=f"#{label}",
        fastSearch=True,
        ancestorNoteId=parent_id,
        limit=500,
    )
    return {x["noteId"]: x for x in r.get("results") or []}


def resolver_pai(cliente, parent):
    if parent == "root":
        return "root"
    try:
        cliente.call_tool("get_child_notes", noteId=parent)
        return parent
    except ErroTool as exc:
        r = cliente.call_tool("search_notes", query=parent, fastSearch=True, limit=1)
        res = r.get("results") or []
        if res:
            return res[0]["noteId"]
        raise ErroMCP(
            f"pai '{parent}' não encontrado no Trilium (noteId inválido?)"
        ) from exc


def reconciliar(cliente, raiz, parent_id, aplicar, force):
    """Compara a árvore-alvo com o estado do Trilium e (se `aplicar`) efetua.

    `aplicar=False` (dry-run) apenas lê e devolve as operações planejadas.
    """
    plano_ids = buscar_por_label(cliente, LABEL_PLANO, parent_id)
    sync_ids = buscar_por_label(cliente, LABEL_SYNC, parent_id)

    por_titulo = {}
    for note_id, info in plano_ids.items():
        titulo = str(info.get("title", "")).strip()
        if titulo in por_titulo:
            print(
                f"aviso: duas notas #vestibularPlano com o título '{titulo}' "
                f"({por_titulo[titulo]} e {note_id}); usando a primeira",
                file=sys.stderr,
            )
        else:
            por_titulo[titulo] = note_id

    rel = Relatorio()
    casados = set()

    def walk(nota, parent_id, nivel):
        ind = "  " * nivel
        note_id = por_titulo.get(nota.titulo)
        if note_id is None:
            rel.criadas.append(f"{ind}[criar] {nota.titulo}")
            novo_id = note_id
            if aplicar:
                resultado = cliente.call_tool(
                    "create_note",
                    parentNoteId=parent_id,
                    title=nota.titulo,
                    content=nota.conteudo,
                    type="text",
                )
                novo_id = resultado["noteId"]
                for label in nota.labels:
                    nome, sep, valor = label.partition("=")
                    kwargs = {"noteId": novo_id, "type": "label", "name": nome}
                    if sep:
                        kwargs["value"] = valor
                    cliente.call_tool("set_attribute", **kwargs)
            else:
                novo_id = f"<novo:{nota.titulo}>"
        else:
            casados.add(note_id)
            if note_id not in sync_ids:
                rel.manuais.append(
                    f"{ind}[manual] {nota.titulo} ({note_id}) — sem "
                    f"#{LABEL_SYNC}, não foi tocada"
                )
                return
            conteudo = cliente.call_tool("get_note_content", noteId=note_id)
            atual = conteudo.get("content", "") or ""
            difere = not conteudos_iguais(nota.conteudo, atual)
            if difere or force:
                alvo = aplicar_marcados(nota.conteudo, atual)
                rel.atualizadas.append(f"{ind}[atualizar] {nota.titulo} ({note_id})")
                if aplicar:
                    cliente.call_tool("set_note_content", noteId=note_id, content=alvo)
            else:
                rel.inalteradas.append(f"{ind}[ok] {nota.titulo} ({note_id})")
            novo_id = note_id

        for filho in nota.filhos:
            walk(filho, novo_id, nivel + 1)

    walk(raiz, parent_id, 0)

    for note_id, info in sync_ids.items():
        if note_id not in casados:
            rel.orfas.append(
                f"[orfao] {info.get('title', '?')} ({note_id}) — "
                f"#{LABEL_SYNC} fora do catálogo (não excluída)"
            )
    return rel


def imprimir_relatorio(rel):
    for texto in rel.criadas:
        print(texto)
    for texto in rel.atualizadas:
        print(texto)
    for texto in rel.manuais:
        print(texto)
    for texto in rel.orfas:
        print(texto)
    for texto in rel.inalteradas:
        print(texto)


# ---------------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(
        description="Sincroniza o plano de estudos (assuntos.json) com o "
        "Trilium via MCP."
    )
    ap.add_argument(
        "--assuntos",
        default=os.environ.get("ASSUNTOS_JSON", "data/assuntos.json"),
        help="caminho do catálogo (default: data/assuntos.json)",
    )
    ap.add_argument(
        "--parent",
        default=os.environ.get("TRILIUM_PARENT_NOTE", "root"),
        help="noteId do pai da árvore (default: root, ou TRILIUM_PARENT_NOTE)",
    )
    ap.add_argument(
        "--url",
        default=os.environ.get("TRILIUM_URL", "http://trilium.dev.ime"),
        help="base URL do Trilium (default: TRILIUM_URL ou http://trilium.dev.ime)",
    )
    ap.add_argument(
        "--token",
        default=os.environ.get("TRILIUM_TOKEN", ""),
        help="token ETAPI (default: TRILIUM_TOKEN no .env)",
    )
    ap.add_argument("--force", action="store_true", help="reescreve conteúdos sem diff")
    ap.add_argument("--timeout", type=float, default=30.0)
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument(
        "--dry-run",
        action="store_true",
        help="imprime a árvore e as operações, nada escreve",
    )
    grupo.add_argument(
        "--check",
        action="store_true",
        help="valida o Trilium contra o catálogo; exit 0 se consistente",
    )
    args = ap.parse_args()

    caminho = Path(args.assuntos)
    if not caminho.exists():
        print(f"erro: {caminho} não existe", file=sys.stderr)
        sys.exit(1)
    raiz = construir_plano(json.loads(caminho.read_text(encoding="utf-8")))
    total, n_areas, n_modulos = contagem(raiz)

    print(
        f"Plano de estudos — {total} notas "
        f"(1 raiz + {n_areas} áreas + {n_modulos} módulos)\n"
    )
    imprimir_arvore(raiz, sys.stdout)
    print()

    if not args.token:
        if args.dry_run:
            print(
                "aviso: TRILIUM_TOKEN ausente (sem conexão). Preencha o .env "
                "(token em Opções > ETAPI do Trilium) para ver as operações."
            )
            return 0
        print(
            "erro: TRILIUM_TOKEN não definido. Adicione ao .env "
            "(token ETAPI, ver Opções > ETAPI do Trilium); placeholders em "
            ".env.example.",
            file=sys.stderr,
        )
        sys.exit(1)

    cliente = ClienteMCPTrilium(args.url, args.token, args.timeout)
    cliente.initialize()
    cliente.notificar("notifications/initialized")
    cliente.validar_tools()
    parent_id = resolver_pai(cliente, args.parent)

    if args.check:
        rel = reconciliar(cliente, raiz, parent_id, aplicar=False, force=False)
        imprimir_relatorio(rel)
        resumo = (
            f"check: {len(rel.criadas)} criar, {len(rel.atualizadas)} atualizar, "
            f"{len(rel.inalteradas)} ok, {len(rel.manuais)} manuais, "
            f"{len(rel.orfas)} órfãs"
        )
        print(resumo)
        if rel.sujo():
            sys.exit(1)
        print("ok: Trilium consistente com o catálogo")
        return 0

    if args.dry_run:
        rel = reconciliar(cliente, raiz, parent_id, aplicar=False, force=args.force)
        print("operações planejadas (--dry-run, nada escrito):")
        if not (rel.criadas or rel.atualizadas or rel.manuais or rel.orfas):
            print("  nada a fazer — Trilium já está sincronizado")
        imprimir_relatorio(rel)
        return 0

    rel = reconciliar(cliente, raiz, parent_id, aplicar=True, force=args.force)
    imprimir_relatorio(rel)
    print(
        f"sync concluído: {len(rel.criadas)} criadas, "
        f"{len(rel.atualizadas)} atualizadas, {len(rel.inalteradas)} ok, "
        f"{len(rel.manuais)} manuais ignoradas, {len(rel.orfas)} órfãs reportadas"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
