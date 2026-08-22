import click

from vestibular.estudo import motiva
from vestibular.estudo.db import connect
from vestibular.estudo.import_questoes import import_todos


@click.group()
def main() -> None:
    """Ferramentas de ingesta e gestão das questões de vestibular."""


@main.command()
def ingest() -> None:
    """Baixa, extrai e importa questões de um PDF para o banco."""
    click.echo("ingest: a implementar (Fase 1)")


@main.command()
def classify() -> None:
    """Classifica questões por área/tema via IA (function calling)."""
    click.echo("classify: a implementar (Fase 1)")


@main.command()
def score() -> None:
    """Atribui dificuldade empírica às questões via IA low-thinking."""
    click.echo("score: a implementar (Fase 1)")


@main.command()
def db_import() -> None:
    """Importa os JSONs de data/json/ para o SQLite (banco do motor de estudo)."""
    with connect() as con:
        res = import_todos(con)
    total = sum(r["questoes"] for r in res)
    click.echo(f"Importados {len(res)} exames, {total} questões.")
    for r in res:
        click.echo(
            f"  {r['label']}: {r['questoes']} ({r['objetivas']} objetivas, {r['redacoes']} redações)"
        )


@main.command()
@click.option("--usuario", default="eu", help="Identificador do usuário")
def proxima(usuario: str) -> None:
    """Mostra a próxima questão adaptativa (FSRS + Rasch)."""
    with connect() as con:
        q = motiva.proxima_questao(con, usuario)
    if q is None:
        click.echo("Nada vencido para estudar.")
        return
    click.echo(f"[{q['tema_nome']}] #{q['numero']} ({q['exame_label']})")
    click.echo(q["enunciado"])
    for letra, txt in (q["alternativas"] or {}).items():
        click.echo(f"  {letra}) {txt}")


@main.command()
@click.option("--usuario", default="eu", help="Identificador do usuário")
@click.option("--resposta", required=True, help="Alternativa escolhida (a-e)")
@click.argument("questao_id", type=int)
def responder(usuario: str, resposta: str, questao_id: int) -> None:
    """Registra a resposta de uma questão e atualiza FSRS/Rasch/item."""
    with connect() as con:
        r = motiva.responder(con, usuario, questao_id, resposta)
    status = "correta" if r["correta"] else ("anulada" if r["correta"] is None else "errada")
    click.echo(f"Q{r['questao_id']}: resposta {r['resposta']} ({status}); gabarito {r['gabarito']}")
    for t in r["temas"]:
        click.echo(f"  tema {t['tema_id']} -> vencimento {t['vencimento'].isoformat()} ({t['estado']})")


@main.command()
@click.option("--usuario", default="eu", help="Identificador do usuário")
def progresso(usuario: str) -> None:
    """Resumo do progresso por área (theta e temas vencidos)."""
    with connect() as con:
        rows = motiva.progresso(con, usuario)
    for r in rows:
        theta = f"{r['theta']:.2f}" if r["n_obs"] else "-"
        click.echo(
            f"{r['area']:<28} θ={theta:>7} n={r['n_obs'] or 0:>3} vencidos={r['temas_vencidos']}"
        )


if __name__ == "__main__":
    main()
