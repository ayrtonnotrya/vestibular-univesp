import click


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


if __name__ == "__main__":
    main()
