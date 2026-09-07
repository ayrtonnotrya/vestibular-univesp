"""Execução da fila em background (daemon-thread no mesmo processo do app).

O app chama `guardar()` uma única vez (idempotente; no Streamlit via
`@st.cache_resource` — 1 worker por container). A thread roda jobs
sequencialmente (FIFO, single-user): recupera órfãos (container reiniciou no
meio de um job; a fase 1 reaproveita `correcao_json`), reclama o job `fila`
mais antigo com claim atômico e chama `servico.processar_job`. Erros por ciclo
com backoff — a thread nunca morre. Jobs com `tentativas >= RED_MAX_TENTATIVAS`
não são reclamados (ficam em `erro` para "Tentar de novo" manual).

Nada aqui depende de sessão Streamlit. CLI:
  python -m vestibular.redacao.worker --once   # processa 1 job (smoke)
  python -m vestibular.redacao.worker --loop   # debug fora do app
"""

import os
import sqlite3
import threading
import time
from datetime import timedelta

from ..estudo.db import connect
from ..estudo.fuso import agora, naive_iso
from . import servico

INTERVALO_S = float(os.environ.get("RED_INTERVALO", "3"))
MAX_TENTATIVAS = int(os.environ.get("RED_MAX_TENTATIVAS", "3"))
PAUSA_RETRY_S = float(os.environ.get("RED_PAUSA_RETRY", "15"))

_lock = threading.Lock()
_thread: threading.Thread | None = None


def _abrir() -> sqlite3.Connection:
    """Conexão própria do worker com timeout alto (SQLite multi-escritor)."""
    con = connect()
    con.execute("PRAGMA busy_timeout=30000")
    return con


def recuperar_orfaos(con: sqlite3.Connection) -> int:
    """`corrigindo|aulando` → `fila` (thread morreu no meio de um job)."""
    cur = con.execute(
        "UPDATE redacao_envios SET status='fila', atualizado_em=? "
        "WHERE status IN ('corrigindo', 'aulando')",
        (naive_iso(),),
    )
    con.commit()
    return cur.rowcount


def reclamar_erros(con: sqlite3.Connection) -> int:
    """`erro` com `tentativas < RED_MAX_TENTATIVAS` volta para a fila.

    Retries automáticos pausados em `RED_PAUSA_RETRY` desde a última falha
    (sem loop de custo); jobs no teto ficam em `erro` até o "Tentar de novo"."""
    corte = naive_iso(agora() - timedelta(seconds=PAUSA_RETRY_S))
    cur = con.execute(
        """UPDATE redacao_envios
           SET status='fila', fase_erro=NULL, atualizado_em=?
           WHERE status='erro' AND tentativas < ? AND atualizado_em <= ?""",
        (naive_iso(), MAX_TENTATIVAS, corte),
    )
    con.commit()
    return cur.rowcount


def rodar_uma(con: sqlite3.Connection, so_correcao: bool = False) -> bool:
    """Reclama e processa o job `fila` mais antigo; False se não havia job.

    Claim atômico: o CAS marca a fase correta para onde o trabalho deve
    continuar (se `correcao_json` já existe → pula direto para a aula).
    `so_correcao` propaga para `processar_job` (para após a rodada 1)."""
    row = con.execute(
        """SELECT id FROM redacao_envios
           WHERE status = 'fila' AND tentativas < ?
           ORDER BY id LIMIT 1""",
        (MAX_TENTATIVAS,),
    ).fetchone()
    if row is None:
        return False
    cur = con.execute(
        """UPDATE redacao_envios
           SET status = CASE WHEN correcao_json IS NULL THEN 'corrigindo' ELSE 'aulando' END,
               atualizado_em = ?
           WHERE id = ? AND status = 'fila'""",
        (naive_iso(), row["id"]),
    )
    con.commit()
    if cur.rowcount == 0:
        return False
    servico.processar_job(con, row["id"], so_correcao=so_correcao)
    return True


def _loop() -> None:
    con: sqlite3.Connection | None = None
    recuperou_orfaos = False
    while True:
        try:
            if con is None:
                con = _abrir()
            if not recuperou_orfaos:
                n = recuperar_orfaos(con)
                recuperou_orfaos = True
                if n:
                    print(f"[redacao] {n} job(s) órfão(s) re-enfileirado(s)", flush=True)
            reclamar_erros(con)
            if rodar_uma(con):
                time.sleep(0.5)
            else:
                time.sleep(INTERVALO_S)
        except sqlite3.Error as e:
            # `database is locked` transitório etc.: reconecta e volta com backoff.
            print(f"[redacao] sqlite indisponível ({e}); retry em {INTERVALO_S * 4:.0f}s", flush=True)
            time.sleep(INTERVALO_S * 4)
            recuperou_orfaos = False
            con = None
        except Exception as e:
            print(f"[redacao] erro no ciclo: {e}", flush=True)
            time.sleep(INTERVALO_S * 2)


def guardar() -> bool:
    """Inicia a daemon-thread do worker exatamente 1× por processo.

    Retorna True se criou o worker, False se já existia. Chamar no `main()` do
    app (ou direto no CLI)."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _thread = threading.Thread(target=_loop, name="redacao-worker", daemon=True)
        _thread.start()
        return True


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="worker da fila de redação")
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--once", action="store_true", help="processa 1 job e sai")
    grupo.add_argument("--loop", action="store_true", help="loop de debug fora do app")
    args = ap.parse_args()

    con = _abrir()
    try:
        if args.once:
            recuperar_orfaos(con)
            reclamar_erros(con)
            print("processado" if rodar_uma(con) else "nada na fila")
        else:
            orfaos = recuperar_orfaos(con)
            if orfaos:
                print(f"[redacao] {orfaos} órfão(s) re-enfileirado(s)")
            while True:
                if not rodar_uma(con):
                    time.sleep(INTERVALO_S)
    finally:
        con.close()


if __name__ == "__main__":
    main()
