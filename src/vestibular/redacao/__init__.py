"""Módulo de correção de redação (UNIVESP) com aula do tutor — fila assíncrona.

Fluxo: o aluno escolhe um tema (questão `tipo='redacao'`), cola a redação e
envia — o envio vira um job em `redacao_envios` (SQLite), processado em
background por uma daemon-thread do app (`worker.guardar`). Duas rodadas de IA
em sequência: correção por competência (`correcao.py`, modelo
`MODEL_CORRECAO`) e aula do tutor em Markdown (`tutor.py`, modelo
`MODEL_TUTOR`), ambas via router OpenAI-compatível (`router.py`). A rubrica
oficial vem do JSON curado em `data/criterios/` (`criterios.py`); o app não
lê o manual em runtime.
"""
