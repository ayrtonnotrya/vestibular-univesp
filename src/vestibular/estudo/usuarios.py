"""CLI de administração das contas do app de estudo.

Dentro do container:

    docker compose exec -T vestibular-app python -m vestibular.estudo.usuarios criar eu

Subcomandos: criar | senha | listar | excluir | revogar-sessoes.
`excluir` apaga a conta e os tokens dela, mas NÃO apaga o progresso
(tentativas, FSRS, θ, redações) — decisão deliberada (auditável/reversível).
"""

import argparse
import getpass
import sys

from .auth import (
    criar_usuario,
    definir_senha,
    excluir_usuario,
    listar_usuarios,
    revogar_sessoes,
)
from .db import connect


def _ler_senha(senha_arg: str | None, rotulo: str) -> str:
    if senha_arg:
        return senha_arg
    s1 = getpass.getpass(f"{rotulo}: ")
    s2 = getpass.getpass("Repita a senha: ")
    if s1 != s2:
        sys.exit("erro: as senhas não conferem")
    return s1


def _despachar(con, args) -> int:
    if args.cmd == "criar":
        nome = criar_usuario(con, args.nome, _ler_senha(args.senha, "Nova senha"))
        print(f"usuário `{nome}` criado")
        return 0
    if args.cmd == "senha":
        definir_senha(con, args.nome, _ler_senha(args.senha, "Nova senha"))
        print(f"senha de `{args.nome.strip().lower()}` redefinida — sessões derrubadas")
        return 0
    if args.cmd == "listar":
        contas = listar_usuarios(con)
        if not contas:
            print(
                "nenhum usuário cadastrado — crie o primeiro com "
                "`python -m vestibular.estudo.usuarios criar eu`"
            )
            return 0
        for u in contas:
            print(
                f"{u['nome']:12} criado em {u['criado_em'][:16]} · "
                f"{u['sessoes']} sessão(ões) ativa(s)"
            )
        return 0
    if args.cmd == "excluir":
        achou = excluir_usuario(con, args.nome)
        if not achou:
            print(f"usuário `{args.nome.strip().lower()}` não existe")
            return 1
        print(
            f"conta `{args.nome.strip().lower()}` e tokens excluídos "
            "— o progresso dela permanece no banco"
        )
        return 0
    n = revogar_sessoes(con, args.nome)
    alvo = f" de `{args.nome}`" if args.nome else " (todos os usuários)"
    print(f"{n} sessão(ões) revogada(s){alvo}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="usuarios",
        description="Administração de contas e sessões do app de estudo.",
    )
    p.add_argument(
        "--db",
        default=None,
        help="caminho do SQLite (default: env DB_PATH / data/vestibular.db)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("criar", help="cadastrar uma conta")
    sp.add_argument("nome")
    sp.add_argument("--senha", help="senha não interativa (default: getpass 2x)")

    sp = sub.add_parser(
        "senha", help="redefinir senha (admin — derruba as sessões da conta)"
    )
    sp.add_argument("nome")
    sp.add_argument("--senha")

    sub.add_parser("listar", help="contas e sessões ativas")

    sp = sub.add_parser(
        "excluir",
        help="apagar conta e tokens (o progresso fica no banco, por segurança)",
    )
    sp.add_argument("nome")

    sp = sub.add_parser(
        "revogar-sessoes", help="encerrar sessões (de um usuário ou de todos)"
    )
    sp.add_argument("nome", nargs="?")

    args = p.parse_args(argv)
    con = connect(args.db)
    try:
        with con:
            return _despachar(con, args)
    except ValueError as e:
        sys.exit(f"erro: {e}")
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
