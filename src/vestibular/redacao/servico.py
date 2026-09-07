"""Fila de envios de redação — camada sobre SQLite (sem thread e sem chamada de IA).

O botão "Enviar" do app chama apenas `enqueue()` (insere `status='fila'`);
quem executa as rodadas de IA é o `worker` (ou o smoke, via `rodar_uma`), que
chama `processar_job()` — núcleo testável das duas rodadas, com entrega
progressiva (a correção grava antes da aula) e retomada da fase que falhou.
"""

import json
import sqlite3

from ..estudo.fuso import naive_iso
from . import correcao as correcao_mod
from . import criterios as criterios_mod
from . import router
from . import tutor as tutor_mod

_RANK_EXAMES = {"fuvest": 1, "univesp": 2, "enem": 3, "fatec": 4, "unesp": 5}

STATUS_ATIVOS = ("fila", "corrigindo", "aulando")


def _ordem_exame(label: str) -> tuple:
    """Chave de ordenação de exames (UNIVESP primeiro; ano mais novo primeiro)
    — espelho de `app/study.py` (sem importar o app)."""
    partes = label.split("_")
    ano = next((int(p) for p in partes[1:] if p.isdigit()), 0)
    return (_RANK_EXAMES.get(partes[0], 9), -ano, label)


def _json_lista(texto: str | None) -> list:
    if not texto:
        return []
    try:
        v = json.loads(texto)
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def listar_temas(con: sqlite3.Connection) -> list[dict]:
    """Todas as questões `tipo='redacao'` (UNIVESP primeiro, ano mais novo
    primeiro), com a área/tema da classificação quando existir."""
    rows = con.execute(
        """SELECT q.id, q.exame_label, q.numero, q.ano, q.enunciado, v.nome AS vestibular
           FROM questoes q
           JOIN vestibulares v ON v.id = q.vestibular_id
           WHERE q.tipo = 'redacao'"""
    ).fetchall()
    classificadas = {}
    for r in con.execute(
        """SELECT c.questao_id, a.nome AS area, t.nome AS tema
           FROM classificacoes c
           JOIN areas a ON a.id = c.area_id
           JOIN temas t ON t.id = c.tema_id
           JOIN questoes q ON q.id = c.questao_id
           WHERE q.tipo = 'redacao'
           ORDER BY a.nome, t.nome"""
    ):
        classificadas.setdefault(r["questao_id"], []).append(
            {"area": r["area"], "tema": r["tema"]}
        )
    out = []
    for r in rows:
        d = dict(r)
        d["temas"] = classificadas.get(d["id"], [])
        out.append(d)
    out.sort(key=lambda d: _ordem_exame(d["exame_label"]))
    return out


def montar_tema(con: sqlite3.Connection, questao_id: int) -> dict | None:
    """Ficha do tema (input dos prompts das duas rodadas) a partir da linha de
    `questoes` + classificação da área Redação quando existir."""
    row = con.execute(
        """SELECT q.id, q.exame_label, q.numero, q.ano, q.tipo, q.enunciado,
                  q.textos_de_apoio, q.midia, v.nome AS vestibular
           FROM questoes q
           JOIN vestibulares v ON v.id = q.vestibular_id
           WHERE q.id = ?""",
        (questao_id,),
    ).fetchone()
    if row is None:
        return None
    temas = [
        {"area": r["area"], "tema": r["tema"]}
        for r in con.execute(
            """SELECT a.nome AS area, t.nome AS tema
               FROM classificacoes c
               JOIN areas a ON a.id = c.area_id
               JOIN temas t ON t.id = c.tema_id
               WHERE c.questao_id = ? AND a.nome = 'Redação'
               ORDER BY t.nome""",
            (questao_id,),
        )
    ]
    return {
        "questao_id": row["id"],
        "exame_label": row["exame_label"],
        "numero": row["numero"],
        "ano": row["ano"],
        "vestibular": row["vestibular"],
        "enunciado": row["enunciado"],
        "textos_de_apoio": _json_lista(row["textos_de_apoio"]),
        "midia": _json_lista(row["midia"]),
        "temas": temas,
    }


def enqueue(con: sqlite3.Connection, usuario: str, questao_id: int, texto: str) -> int:
    """Insere o envio na fila e devolve o id — É TUDO o que o botão Enviar faz."""
    agora = naive_iso()
    cur = con.execute(
        """INSERT INTO redacao_envios
           (usuario, questao_id, texto, palavras, status, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, 'fila', ?, ?)""",
        (usuario, questao_id, texto, len(texto.split()), agora, agora),
    )
    con.commit()
    return cur.lastrowid


def cancelar(con: sqlite3.Connection, envio_id: int) -> bool:
    """Cancela APENAS enquanto `fila` (rowcount 0 = o worker já reclamou)."""
    cur = con.execute(
        "UPDATE redacao_envios SET status='cancelado', atualizado_em=? WHERE id=? AND status='fila'",
        (naive_iso(), envio_id),
    )
    con.commit()
    return cur.rowcount > 0


def tentar(con: sqlite3.Connection, envio_id: int) -> bool:
    """Erro → fila (com `tentativas` zerada; a retomada é econômica: só roda a
    rodada que falta, ver `processar_job`)."""
    cur = con.execute(
        """UPDATE redacao_envios
           SET status='fila', tentativas=0, fase_erro=NULL, erro=NULL, atualizado_em=?
           WHERE id=? AND status='erro'""",
        (naive_iso(), envio_id),
    )
    con.commit()
    return cur.rowcount > 0


def _linha(con: sqlite3.Connection, envio_id: int) -> dict | None:
    row = con.execute("SELECT * FROM redacao_envios WHERE id=?", (envio_id,)).fetchone()
    return dict(row) if row else None


def _status(
    con: sqlite3.Connection,
    envio_id: int,
    novo: str,
    **campos: object,
) -> None:
    sets, vals = ["status=?", "atualizado_em=?"], [novo, naive_iso()]
    for chave, valor in campos.items():
        sets.append(f"{chave}=?")
        vals.append(valor)

    # Cancelamento concorrente (dois containers/CLI): nunca escrever por cima
    # de um `cancelado`.
    vals.append(envio_id)
    cur = con.execute(
        f"UPDATE redacao_envios SET {', '.join(sets)} WHERE id=? AND status!='cancelado'",
        vals,
    )

    con.commit()
    if cur.rowcount == 0:
        raise RuntimeError(f"envio {envio_id} cancelado; abortando processamento")

def _erro(con: sqlite3.Connection, envio_id: int, fase: str, exc: Exception) -> None:
    atual = con.execute(
        "SELECT status, tentativas FROM redacao_envios WHERE id=?", (envio_id,)
    ).fetchone()
    if atual is None or atual["status"] == "cancelado":
        return
    msg = " ".join(str(exc).split()) or exc.__class__.__name__
    # Cancelamento concorrente (dois containers/CLI): nunca escrever por cima
    # de um `cancelado`.
    con.execute(
        "UPDATE redacao_envios SET status='erro', fase_erro=?, erro=?, tentativas=?, atualizado_em=?"
        " WHERE id=? AND status!='cancelado'",
        (
            fase,
            msg[:300],
            atual["tentativas"] + 1,
            naive_iso(),
            envio_id,
        ),
    )
    con.commit()


def processar_job(
    con: sqlite3.Connection, envio_id: int, so_correcao: bool = False
) -> dict | None:
    """Núcleo das duas rodadas (sem thread; worker/smoke testável direto).

    1. sem `correcao_json` → rodada 1 (guarda notas: entrega progressiva);
       se já existe, PULA (retry não paga a rodada 1 de novo);
    2. `aulando` + rodada 2 (`so_correcao` para aqui — o job fica em
       `aulando`, pronto para retomar da fase 2 por um worker);
    3. concluído; exceção → `erro` com `fase_erro`/`tentativas`.
    """
    job = _linha(con, envio_id)
    if job is None:
        return None
    tema = montar_tema(con, job["questao_id"])
    if tema is None:
        _erro(con, envio_id, "correcao", RuntimeError("tema não existe no banco"))
        return _linha(con, envio_id)
    try:
        criterio = criterios_mod.carregar()
    except Exception as e:
        _erro(con, envio_id, "correcao", e)
        return _linha(con, envio_id)

    fase = "correcao"
    try:
        corr = json.loads(job["correcao_json"]) if job["correcao_json"] else None
        novo_aula = corr is None
        if corr is None:
            _status(con, envio_id, "corrigindo")
            corr = correcao_mod.corrigir(tema, job["texto"], criterio)
            _status(
                con,
                envio_id,
                "aulando",
                correcao_json=json.dumps(corr, ensure_ascii=False),
                nota_total=corr["nota_total"],
                anulado=int(corr["anulado"]),
                motivo_anulacao=corr["motivo_anulacao"],
                modelo_correcao=router.MODEL_CORRECAO,
            )
        if novo_aula and so_correcao:
            return _linha(con, envio_id)
        fase = "tutor"
        aula = tutor_mod.dar_aula(tema, job["texto"], corr, criterio)
        _status(
            con,
            envio_id,
            "concluido",
            aula_json=json.dumps(aula, ensure_ascii=False),
            modelo_tutor=router.MODEL_TUTOR,
            fase_erro=None,
            erro=None,
        )
    except Exception as e:
        _erro(con, envio_id, fase, e)
    return _linha(con, envio_id)


def detalhe(con: sqlite3.Connection, envio_id: int) -> dict | None:
    """Envio completo + tema (para render 100% do banco, nunca dispara IA)."""
    job = _linha(con, envio_id)
    if job is None:
        return None
    for campo in ("correcao_json", "aula_json"):
        key = campo[: -len("_json")]
        raw = job.get(campo)
        try:
            job[key] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            job[key] = {"_parse_error": True}
    job["tema"] = montar_tema(con, job["questao_id"])
    return job


def historico(con: sqlite3.Connection, usuario: str) -> list[dict]:
    rows = con.execute(
        """SELECT e.id, e.questao_id, e.status, e.fase_erro, e.erro, e.tentativas,
                  e.nota_total, e.anulado, e.palavras, e.criado_em, e.atualizado_em,
                  q.exame_label, q.numero
           FROM redacao_envios e
           JOIN questoes q ON q.id = e.questao_id
           WHERE e.usuario = ?
           ORDER BY e.id DESC""",
        (usuario,),
    ).fetchall()
    return [dict(r) for r in rows]


def jobs_ativos(con: sqlite3.Connection, usuario: str) -> list[dict]:
    """Envios não-terminais do usuário (dizem à UI quando vale auto-atualizar)."""
    rows = con.execute(
        f"""SELECT * FROM redacao_envios
            WHERE usuario = ? AND status IN ({','.join('?' * len(STATUS_ATIVOS))})
            ORDER BY id""",
        (usuario, *STATUS_ATIVOS),
    ).fetchall()
    return [dict(r) for r in rows]
