"""Configuração central do agendamento FSRS por tema.

A política por tema não é de flashcards: um tema só começa a ser agendado com
evidência suficiente (`MIN_TENTATIVAS_REVISAO` respostas); antes disso é
"explorável" (sempre candidato no modo Estudar, com `vencimento=None`). A fila
de vencidos é limitada por sessão (`CAP_REVISOES_SESSAO`) para não monopolizar
o estudo. Os passos de aprendizagem são em dias, não minutos (o "card" do tema
não faz sentido numa sessão).

Um único `Scheduler` (via `make_scheduler`) é compartilhado por
`vestibular.estudo.fsrs` e `app/estatisticas` para ninguém divergir no R.
"""
import datetime as dt

from fsrs import Scheduler

# Portão de evidência: nº mínimo de respostas para o tema ganhar card FSRS e
# entrar na fila de revisão (contagem ANTIGA + 1 >= MIN).
MIN_TENTATIVAS_REVISAO = 3

# Teto da fila de vencidos por sessão (só conta o subgrupo "vencido").
CAP_REVISOES_SESSAO = 5

# Taxa de retenção desejada para o intervalo do card.
DESIRED_RETENTION = 0.87

# Passo de aprendizagem do tema: 1 dia (o tema evolui entre dias, não em minutos).
LEARNING_STEPS = (dt.timedelta(days=1),)
RELEARNING_STEPS = (dt.timedelta(days=1),)


def make_scheduler() -> Scheduler:
    """`Scheduler` único do projeto (parâmetros FSRS-6 padrão, sem calibrar)."""
    return Scheduler(
        desired_retention=DESIRED_RETENTION,
        learning_steps=LEARNING_STEPS,
        relearning_steps=RELEARNING_STEPS,
    )