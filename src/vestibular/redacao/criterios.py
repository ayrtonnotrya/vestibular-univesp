"""Carregamento e validação dos critérios oficiais de redação (JSON curado).

A fonte de verdade em runtime é `data/criterios/redacao_univesp_2026.json`
(curado a partir do Manual do Candidato UNIVESP 2026 — ver Tarefa 0 do plano);
o PDF não é lido pela aplicação. Caminho configurável por `CRITERIOS_PATH`
(arquivo) ou `DATA_DIR` (raiz dos dados; default `data`).
"""

import json
import os
from pathlib import Path

ARQUIVO_PADRAO = Path("criterios") / "redacao_univesp_2026.json"


def caminho_criterios() -> Path:
    direto = os.environ.get("CRITERIOS_PATH")
    if direto:
        return Path(direto)
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    return data_dir / ARQUIVO_PADRAO


def _validar(c: object) -> dict:
    if not isinstance(c, dict):
        raise RuntimeError("critérios: JSON raiz não é objeto")
    for campo in ("nota_maxima", "competencias", "regras_anulacao", "extrato_manual"):
        if campo not in c:
            raise RuntimeError(f"critérios: campo ausente: {campo}")
    comps = c["competencias"]
    if not isinstance(comps, list) or len(comps) < 2:
        raise RuntimeError("critérios: 'competencias' deve ser lista com 2+ itens")
    for i, comp in enumerate(comps):
        if not isinstance(comp, dict):
            raise RuntimeError(f"critérios: competência {i} não é objeto")
        for campo in ("id", "nome", "maximo", "níveis"):
            if campo not in comp:
                raise RuntimeError(f"critérios: competência {i} sem campo '{campo}'")
        niveis = comp["níveis"]
        if not isinstance(niveis, list) or not niveis:
            raise RuntimeError(f"critérios: competência {comp['id']} sem níveis")
        for nivel in niveis:
            if not isinstance(nivel, dict) or "pontos" not in nivel:
                raise RuntimeError(f"critérios: nível inválido na competência {comp['id']}")
    if not isinstance(c["regras_anulacao"], list) or not c["regras_anulacao"]:
        raise RuntimeError("critérios: 'regras_anulacao' deve ser lista não vazia")
    if not str(c["extrato_manual"]).strip():
        raise RuntimeError("critérios: 'extrato_manual' vazio")
    return c


def carregar() -> dict:
    """Lê e valida o JSON de critérios; falha com erro claro (nunca silencia)."""
    path = caminho_criterios()
    if not path.exists():
        raise RuntimeError(
            f"Arquivo de critérios não encontrado: {path} — cure o manual "
            "(ver AGENTS.md, módulo de redação) ou ajuste CRITERIOS_PATH/DATA_DIR."
        )
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"critérios inválidos em {path}: {e}") from e
    return _validar(dados)


def rubrica_prompt(criterio: dict, resumida: bool = False) -> str:
    """Formata a rubrica para injetar no prompt da correção (ou versão resumida
    para o tutor)."""
    linhas = [
        f"Tipo de texto exigido: {criterio.get('tipo_texto', 'dissertativo-argumentativo')}. "
        f"Nota máxima total: {criterio['nota_maxima']}.",
        "",
        "CRITÉRIOS (avalie cada um separadamente):",
    ]
    for comp in criterio["competencias"]:
        linhas.append(f"- Critério {comp['id']}: {comp['nome']} (máximo {comp['maximo']} pontos)")
        if not resumida:
            for nivel in sorted(comp["níveis"], key=lambda n: -n["pontos"]):
                linhas.append(f"    {nivel['pontos']} pontos: {nivel['descricao']}")
    linhas.append("")
    linhas.append("REGRAS DE ANULAÇÃO (nota 0 total):")
    for regra in criterio["regras_anulacao"]:
        linhas.append(f"- {regra}")
    if not resumida:
        for pen in criterio.get("penalidades_importantes", []):
            linhas.append(f"- [penalidade] {pen}")
    return "\n".join(linhas)
