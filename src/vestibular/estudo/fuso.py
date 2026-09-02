"""Fuso horário do app: America/Sao_Paulo (UTC-3, sem horário de verão desde 2019).

O container roda em UTC; se o "agora" do motor fosse UTC, o dia viraria às
21h locais (00h UTC) e a fila de revisão/premiação de "vencidos" adiantaria
as datas. Tudo que depende de "hoje" usa este fuso.
"""

import datetime as dt

FUSO_BR = dt.timezone(dt.timedelta(hours=-3), name="America/Sao_Paulo")


def agora() -> dt.datetime:
    """Agora no fuso do app (aware, UTC-3)."""
    return dt.datetime.now(FUSO_BR)


def hoje() -> dt.date:
    """Data de hoje no fuso do app."""
    return agora().date()


def naive_iso(agora_: dt.datetime | None = None) -> str:
    """ISO local SEM offset de um datetime, para gravar no DB e comparar por dia.

    SQLite `date()` renormaliza strings com offset para UTC; usar aqui um ISO
    sem offset garante que `date(vencimento) <= date(?)` compare dias locais
    (a virada à meia-noite de São Paulo, não das 21h/00h UTC).
    """
    a = agora_ or agora()
    return a.astimezone(FUSO_BR).replace(tzinfo=None).isoformat()