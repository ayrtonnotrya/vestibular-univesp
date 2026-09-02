"""Remove de ``textos_de_apoio`` os itens já contidos INTEGRALMENTE no enunciado.

Origem do problema: o prompt de extração manda transcrever "enunciados" e
"textos de apoio/motivadores" íntegros e repetir textos comuns a várias
questões; sem uma regra separando comando de motivador, o modelo gravou o
bloco inteiro (motivador + comando) no ``enunciado`` E também copiou o motivador
em ``textos_de_apoio`` — o texto aparece duas vezes no app (enunciado + apoio).

Só remove quando a CONTAINMENT é integral (verbatim, após colapsar espaços/
quebras de linha e minúsculas): o texto de apoio inteiro existe como substring
do enunciado. Enunciados que apenas PARAFRASEIAM parte do texto não são
afetados (a paráfrase não é substring do texto original).

Uso: python dedupe_apoios.py [labels...] [--check]
  sem argumentos: todos os exames em data/json
  --check: reporta sem escrever
"""
import json
import os
import re
import sys

JK = "/work/data/json"


def norm(s):
    return re.sub(r"[\s\u00a0\u200b]+", " ", str(s)).strip().lower()


def dedupe_label(label, check):
    path = f"{JK}/{label}_questoes.json"
    if not os.path.exists(path):
        print(f"[{label}] sem arquivo, pulado", flush=True)
        return 0, 0
    j = json.load(open(path, encoding="utf-8"))
    removidos = 0
    for q in j.get("questoes", []):
        apoios = q.get("textos_de_apoio") or []
        if not apoios:
            continue
        e = norm(q.get("enunciado", ""))
        if not e:
            continue
        novos = []
        for t in apoios:
            tn = norm(t)
            if tn and tn in e:
                removidos += 1
            else:
                novos.append(t)
        q["textos_de_apoio"] = novos
    total = len(j.get("questoes", []))
    if check:
        print(f"[{label}] removeria {removidos}/{total}", flush=True)
    else:
        if removidos:
            json.dump(j, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[{label}] {removidos}/{total} apoios duplicados removidos", flush=True)
    return removidos, total


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    check = "--check" in sys.argv
    if args:
        labels = [a.removesuffix("_questoes") for a in args]
    else:
        labels = sorted(
            f.rsplit("_questoes.json", 1)[0]
            for f in os.listdir(JK)
            if f.endswith("_questoes.json")
        )
    tot = 0
    for label in labels:
        r, _ = dedupe_label(label, check)
        tot += r
    modo = "removeria" if check else "removidos"
    print(f"TOTAL {modo}: {tot}", flush=True)


if __name__ == "__main__":
    main()