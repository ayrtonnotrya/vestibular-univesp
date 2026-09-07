"""Smoke do módulo de redação — valida sem UI o caminho assíncrono de verdade.

Uso (imagem `vestibular-app`, com `--network host` para alcançar o router e
`--env-file .env`):
  python -m vestibular.redacao.smoke criterios
  python -m vestibular.redacao.smoke temas
  python -m vestibular.redacao.smoke enviar univesp_2026 57 --arquivo tmp/red.txt
  python -m vestibular.redacao.smoke enviar ... --so-correcao   # para após a rodada 1
  python -m vestibular.redacao.smoke enviar ... --esperar 0     # só enqueue
"""

import argparse
import sys
import time

from ..estudo.db import connect
from . import criterios as criterios_mod
from . import servico, worker


def _con():
    con = connect()
    con.execute("PRAGMA busy_timeout=30000")
    return con


def cmd_criterios(_args) -> int:
    c = criterios_mod.carregar()
    print(f"OK: {c['fonte']}")
    print(f"nota_maxima={c['nota_maxima']} | competências:")
    for comp in c["competencias"]:
        niveis = sorted({int(n["pontos"]) for n in comp["níveis"]})
        print(f"  {comp['id']}. {comp['nome'][:60]} (máx {comp['maximo']}, níveis {niveis})")
    print(
        f"regras de anulação: {len(c['regras_anulacao'])} | "
        f"extrato: {len(c['extrato_manual'])} chars"
    )
    return 0


def cmd_temas(_args) -> int:
    con = _con()
    try:
        for t in servico.listar_temas(con):
            temas = "; ".join(x["tema"] for x in t["temas"]) or "—"
            enunciado = str(t["enunciado"])[:60].replace("\n", " ")
            print(f"{t['id']:>6}  {t['exame_label']:<16} Q{t['numero']:<4} {enunciado!r}  [{temas}]")
    finally:
        con.close()
    return 0


def _impressao_final(job: dict) -> None:
    corr = job.get("correcao") or {}
    maximo = criterios_mod.carregar()["nota_maxima"]
    anulado = " — ANULADA: " + str(corr.get("motivo_anulacao")) if corr.get("anulado") else ""
    print(f"\nnota_total = {corr.get('nota_total')} / {maximo}{anulado}")
    for comp in corr.get("competencias", []):
        print(f"  {comp['id']}. {comp['nome'][:50]:<52} {comp['nota']:>3}/{comp['maximo']}  {comp['resumo'][:80]}")
    if corr.get("comentario_geral"):
        print(f"comentário: {corr['comentario_geral'][:300]}")
    aula = job.get("aula")
    if aula:
        corpo = str(aula.get("aula_md") or "")
        print(f"\naula_md ({len(corpo)} chars): {corpo[:300].replace(chr(10), ' ')}...")
        if aula.get("conceitos_estudar"):
            print(f"conceitos_estudar: {aula['conceitos_estudar']}")
        if aula.get("reescritura_sugerida"):
            print(f"reescrituras: {len(aula['reescritura_sugerida'])} parágrafo(s)")


def cmd_enviar(args) -> int:
    con = _con()
    try:
        row = con.execute(
            "SELECT id FROM questoes WHERE exame_label=? AND numero=? AND tipo='redacao'",
            (args.label, args.numero),
        ).fetchone()
        if row is None:
            print(f"sem redação no exame {args.label} questão {args.numero}", file=sys.stderr)
            return 2
        with open(args.arquivo, encoding="utf-8") as f:
            texto = f.read().strip()
        if not texto:
            print("arquivo de redação vazio", file=sys.stderr)
            return 2
        envio_id = servico.enqueue(con, "smoke", row["id"], texto)
        print(f"envio {envio_id} na fila ({len(texto.split())} palavras)", flush=True)
        if args.esperar == 0:
            print("--esperar 0: só enfileirei (processar com `python -m vestibular.redacao.worker --once`)")
            return 0

        prazo = time.monotonic() + args.esperar
        ultimo = None
        job = None
        while time.monotonic() < prazo:
            worker.rodar_uma(con, so_correcao=args.so_correcao)
            job = servico.detalhe(con, envio_id)
            estado = (job or {}).get("status")
            if estado != ultimo:
                extras = f" fase_erro={job['fase_erro']} tentativas={job['tentativas']}" if estado == "erro" else ""
                nota = f" nota={job.get('nota_total')}" if job and job.get("nota_total") is not None else ""
                print(f"[tick] status={estado}{nota}{extras}", flush=True)
                ultimo = estado
            if estado in ("concluido", "erro", "cancelado"):
                break
            if args.so_correcao and estado == "aulando":
                break
            time.sleep(2)
        if job is None:
            print("envio sumiu do banco", file=sys.stderr)
            return 1
        if args.so_correcao and job["status"] == "aulando":
            print("(parou após a rodada 1: --so-correcao; rode sem a flag para a aula)")
    finally:
        con.close()

    if job["status"] == "erro":
        print(f"ERRO na fase {job['fase_erro']}: {job['erro']}", file=sys.stderr)
        return 1
    _impressao_final(job)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="smoke do módulo de redação")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("criterios", help="valida o JSON curado de critérios")
    sub.add_parser("temas", help="lista os temas de redação do banco")
    p = sub.add_parser("enviar", help="enqueue + processa em loop (fila real, sem thread)")
    p.add_argument("label", help="exame_label (ex.: univesp_2026)")
    p.add_argument("numero", type=int, help="número da questão de redação")
    p.add_argument("--arquivo", required=True, help="txt com a redação do aluno")
    p.add_argument("--so-correcao", action="store_true", help="para após a rodada 1 (aulando)")
    p.add_argument("--esperar", type=float, default=1800, help="segundos de espera (0 = só enqueue)")
    args = ap.parse_args()
    if args.cmd == "criterios":
        sys.exit(cmd_criterios(args))
    if args.cmd == "temas":
        sys.exit(cmd_temas(args))
    sys.exit(cmd_enviar(args))


if __name__ == "__main__":
    main()
