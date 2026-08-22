"""Alinha gabarito/anulada dos JSONs FUVEST com o gabarito oficial (texto do PDF).

O texto do gabarito oficial é a fonte da verdade (parse determinístico em
validate.parse_fuvest_gabarito). Para cada questão, sobrescreve "gabarito" e
"anulada" conforme o oficial e imprime as divergências em relação ao que o
modelo havia extraído, para conferência visual.

Uso: python gabfix.py [labels...]   (default: todos os fuvest)
"""
import json
import os
import sys

import validate as V

JK = "/work/data/json"


def main():
    labels = sys.argv[1:] or V.FUVEST_LABELS
    for label in labels:
        path = f"{JK}/{label}_questoes.json"
        if not os.path.exists(path):
            print(f"[{label}] sem arquivo, pulado", flush=True)
            continue
        j = json.load(open(path, encoding="utf-8"))
        oficial = V.parse_fuvest_gabarito(label)
        mudou = 0
        for q in j["questoes"]:
            n = q["numero"]
            if n not in oficial:
                continue
            o = oficial[n]
            if o is None:
                if not (q.get("anulada") and q.get("gabarito") is None):
                    print(f"[{label}] Q{n}: modelo={q.get('gabarito')} -> ANULADA")
                    q["gabarito"] = None
                    q["anulada"] = True
                    mudou += 1
            else:
                if q.get("anulada"):
                    print(f"[{label}] Q{n}: modelo ANULADA -> oficial={o}")
                    mudou += 1
                elif (q.get("gabarito") or "").lower() != o:
                    print(f"[{label}] Q{n}: modelo={q.get('gabarito')} oficial={o}")
                    mudou += 1
                q["anulada"] = False
                q["gabarito"] = o
        with open(path, "w", encoding="utf-8") as f:
            json.dump(j, f, ensure_ascii=False, indent=2)
        print(f"[{label}] {mudou} correções aplicadas", flush=True)


if __name__ == "__main__":
    main()