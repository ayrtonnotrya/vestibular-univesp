"""Autenticação do app: contas (`usuarios`) e sessões por token opaco (`auth_sessoes`).

Senhas com PBKDF2-HMAC-SHA256 da stdlib (600k iterações, salt de 16 bytes) —
sem dependências novas. A sessão é um token opaco transportado no query param
`?sid=` da URL: o Streamlit perde `session_state` a cada refresh e não grava
cookies sem JS.

Timestamps em `expira_em` seguem o padrão do projeto (ISO local SEM offset,
`fuso.naive_iso()`); como o formato é fixo, a comparação lexicográfica de
strings ISO equivale à comparação temporal.
"""

import datetime as dt
import hashlib
import hmac
import re
import secrets
import sqlite3

from . import fuso

# Mínimo 2 (não 3) para permitir a conta `eu` — o progresso legado do banco
# inteiro está nessa chave e o rollout do plano exige cadastrar exatamente `eu`.
NOME_RE = re.compile(r"^[a-z0-9_.-]{2,30}$")
SENHA_MINIMA = 8
ITERACOES = 600_000
DURACAO_SESSAO = dt.timedelta(days=30)

# Hash de 600k iterações verificado no caminho "usuário inexistente" do
# login(): o tempo da resposta é equivalente ao de uma senha errada em conta
# real, o que impede enumerar contas pela latência.
_HASH_DUMMY = "pbkdf2_sha256$600000$" + "0" * 32 + "$" + "0" * 64


def hash_senha(senha: str) -> str:
    """`pbkdf2_sha256$<iterações>$<salt_hex>$<hash_hex>`."""
    salt = secrets.token_bytes(16)
    deriv = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, ITERACOES)
    return f"pbkdf2_sha256${ITERACOES}${salt.hex()}${deriv.hex()}"


def verificar_senha(senha: str, armazenada: str | None) -> bool:
    """Re-hash com o salt gravado + comparação em tempo constante."""
    if not armazenada:
        return False
    try:
        algo, iteracoes, salt_hex, hash_hex = armazenada.split("$")
        n = int(iteracoes)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256" or not hash_hex:
        return False
    deriv = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, n)
    return hmac.compare_digest(deriv.hex(), hash_hex)


def normalizar_nome(nome: str) -> str:
    return (nome or "").strip().lower()


def validar_nome(nome: str) -> str:
    n = normalizar_nome(nome)
    if not NOME_RE.match(n):
        raise ValueError(
            "nome inválido: use 2–30 caracteres de a-z, 0-9, '_', '.' ou '-' "
            "(maiúsculas e espaços não são permitidos)"
        )
    return n


def validar_senha(senha: str) -> str:
    if not senha or len(senha) < SENHA_MINIMA:
        raise ValueError(f"a senha deve ter pelo menos {SENHA_MINIMA} caracteres")
    return senha


def criar_usuario(con: sqlite3.Connection, nome: str, senha: str) -> str:
    """Cadastra uma conta (o nome é normalizado para minúsculas)."""
    nome = validar_nome(nome)
    validar_senha(senha)
    try:
        con.execute(
            "INSERT INTO usuarios (nome, hash_senha, criado_em) VALUES (?, ?, ?)",
            (nome, hash_senha(senha), fuso.naive_iso()),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"usuário {nome!r} já existe") from None
    con.commit()
    return nome


def definir_senha(con: sqlite3.Connection, nome: str, senha: str) -> None:
    """Redefinição pelo admin: troca o hash e derruba TODAS as sessões."""
    nome = validar_nome(nome)
    validar_senha(senha)
    cur = con.execute(
        "UPDATE usuarios SET hash_senha = ? WHERE nome = ?", (hash_senha(senha), nome)
    )
    if not cur.rowcount:
        raise ValueError(f"usuário {nome!r} não existe")
    revogar_sessoes(con, nome)
    con.commit()


def trocar_senha(
    con: sqlite3.Connection,
    nome: str,
    senha_atual: str,
    senha_nova: str,
    token_atual: str | None = None,
) -> bool:
    """Troca a senha do próprio usuário (app). False se a atual não confere.

    Em sucesso revoga os tokens da conta EXCETO `token_atual`: a sessão
    corrente sobrevive e as outras abas/dispositivos caem no login."""
    row = con.execute(
        "SELECT hash_senha FROM usuarios WHERE nome = ?", (nome,)
    ).fetchone()
    if row is None or not verificar_senha(senha_atual, row["hash_senha"]):
        return False
    validar_senha(senha_nova)
    con.execute(
        "UPDATE usuarios SET hash_senha = ? WHERE nome = ?",
        (hash_senha(senha_nova), nome),
    )
    if token_atual:
        con.execute(
            "DELETE FROM auth_sessoes WHERE usuario = ? AND token <> ?",
            (nome, token_atual),
        )
    else:
        con.execute("DELETE FROM auth_sessoes WHERE usuario = ?", (nome,))
    con.commit()
    return True


def login(con: sqlite3.Connection, nome: str, senha: str) -> str | None:
    """Confere as credenciais e abre uma sessão de 30 dias; devolve o token."""
    nome = normalizar_nome(nome)
    row = con.execute(
        "SELECT hash_senha FROM usuarios WHERE nome = ?", (nome,)
    ).fetchone()
    if row is None:
        verificar_senha(senha, _HASH_DUMMY)
        return None
    if not verificar_senha(senha, row["hash_senha"]):
        return None
    agora = fuso.naive_iso()
    con.execute("DELETE FROM auth_sessoes WHERE expira_em <= ?", (agora,))
    token = secrets.token_urlsafe(32)
    con.execute(
        """INSERT INTO auth_sessoes (token, usuario, criado_em, expira_em)
           VALUES (?, ?, ?, ?)""",
        (token, nome, agora, fuso.naive_iso(fuso.agora() + DURACAO_SESSAO)),
    )
    con.commit()
    return token


def usuario_autenticado(con: sqlite3.Connection, token: str | None) -> str | None:
    """Dono de um token válido e não expirado; None se expirou ou a conta foi
    excluída (o JOIN com `usuarios` mata o órfão)."""
    if not token:
        return None
    row = con.execute(
        """SELECT s.usuario
           FROM auth_sessoes s
           JOIN usuarios u ON u.nome = s.usuario
           WHERE s.token = ? AND s.expira_em > ?""",
        (token, fuso.naive_iso()),
    ).fetchone()
    return row["usuario"] if row else None


def revogar_token(con: sqlite3.Connection, token: str) -> None:
    con.execute("DELETE FROM auth_sessoes WHERE token = ?", (token,))
    con.commit()


def revogar_sessoes(con: sqlite3.Connection, nome: str | None = None) -> int:
    """Encerra sessões de um usuário (nome) ou de todos (None); devolve o nº."""
    if nome is None:
        cur = con.execute("DELETE FROM auth_sessoes")
    else:
        cur = con.execute("DELETE FROM auth_sessoes WHERE usuario = ?", (nome,))
    con.commit()
    return cur.rowcount


def listar_usuarios(con: sqlite3.Connection) -> list[dict]:
    return [
        dict(r)
        for r in con.execute(
            """SELECT u.nome, u.criado_em,
                      (SELECT COUNT(*) FROM auth_sessoes s
                        WHERE s.usuario = u.nome AND s.expira_em > ?) AS sessoes
               FROM usuarios u
               ORDER BY u.nome""",
            (fuso.naive_iso(),),
        )
    ]


def excluir_usuario(con: sqlite3.Connection, nome: str) -> bool:
    """Apaga a conta e seus tokens. O progresso (tentativas, FSRS, θ,
    redações) NÃO é apagado — fica no banco por segurança."""
    nome = validar_nome(nome)
    cur = con.execute("DELETE FROM usuarios WHERE nome = ?", (nome,))
    con.execute("DELETE FROM auth_sessoes WHERE usuario = ?", (nome,))
    con.commit()
    return bool(cur.rowcount)
