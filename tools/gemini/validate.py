import json
import os
import re

DATA = "/work/data"
GAB = "/work/tmp/gabaritos"
JK = "/work/data/json"
CATALOG = "/work/data/assuntos.json"

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"]


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)
    areas = {}
    assuntos = set()
    for d in cat["plano_de_estudos_vestibular"]["disciplinas"]:
        areas[d["area"]] = None
        for mod in d["modulos"]:
            for a in mod["assuntos"]:
                assuntos.add(a)
    return areas, assuntos


def parse_gabarito(label):
    path = f"{GAB}/{label}_gabarito.txt"
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    g = {}
    # pair lines ques->alt, sequential
    prev = None
    for line in text.splitlines():
        m = re.search(r"\b(\d{1,3})\s+([A-E])\b", line)
        if m:
            n = int(m.group(1))
            g[n] = m.group(2).lower()
            prev = None
        else:
            # e.g. "1 C  " double column layout already handled by regex per line
            pass
    return g


def parse_gabarito_md(label):
    """handle e.g. 2021 '| 1 - C | 2 - B |...' style"""
    path = f"{GAB}/{label}_gabarito.txt"
    text = open(path, encoding="utf-8").read()
    g = {}
    for m in re.finditer(r"\b(\d{1,3})\s*[-–]\s*([A-E])\b", text):
        g[int(m.group(1))] = m.group(2).lower()
    for m in re.finditer(r"\b(\d{1,3})\s+([A-E])\b", text):
        n = int(m.group(1))
        if n not in g:
            g[n] = m.group(2).lower()
    return g


def main():
    areas, assuntos = load_catalog()
    ok = True
    for label in LABELS:
        path = f"{JK}/{label}_questoes.json"
        j = json.load(open(path, encoding="utf-8"))
        qs = j["questoes"]
        nums = [q["numero"] for q in qs]
        errs = []
        # sequential coverage
        if nums != list(range(1, len(qs) + 1)):
            errs.append(f"numeracao nao sequencial: {nums[:10]}...")
        # metadata
        for field in ["exame", "ano", "fonte_questoes", "fonte_gabarito", "total_questoes"]:
            if field not in j:
                errs.append(f"sem campo {field}")
        if j.get("total_questoes") != len(qs):
            errs.append(f"total_questoes={j.get('total_questoes')} != {len(qs)}")
        if j.get("exame") != f"{label}_questoes":
            errs.append(f"exame={j.get('exame')}")
        # per question
        for q in qs:
            n = q["numero"]
            if q["tipo"] == "objetiva":
                for k in "abcde":
                    if k not in (q.get("alternativas") or {}):
                        errs.append(f"Q{n}: faltando alternativa {k}")
                        break
                if q.get("anulada"):
                    if q.get("gabarito") is not None:
                        errs.append(f"Q{n}: anulada com gabarito {q.get('gabarito')}")
                else:
                    if not q.get("gabarito"):
                        errs.append(f"Q{n}: gabarito vazio ({q.get('gabarito')})")
                    elif q["gabarito"] not in list("abcde"):
                        errs.append(f"Q{n}: gabarito invalido {q['gabarito']}")
                for a in q.get("areas", []):
                    if a["area"] not in areas:
                        errs.append(f"Q{n}: area invalida '{a['area']}'")
                    for s in a["assuntos"]:
                        if s not in assuntos:
                            errs.append(f"Q{n}: assunto fora do catalogo '{s}'")
            elif q["tipo"] == "redacao":
                if q.get("gabarito") is not None or q.get("alternativas") is not None:
                    errs.append(f"Q{n}: redacao com gabarito/alternativas")
                areas_red = [a["area"] for a in q.get("areas", [])]
                if "Redação" not in areas_red:
                    errs.append(f"Q{n}: redacao sem area Redação")
            else:
                errs.append(f"Q{n}: tipo invalido '{q['tipo']}'")
            if not q.get("enunciado") or len(q["enunciado"]) < 10:
                errs.append(f"Q{n}: enunciado curto/ausente")
        # gabarito cross-check
        g = parse_gabarito_md(label)
        if g:
            for n, letter in g.items():
                q = next((q for q in qs if q["numero"] == n), None)
                if q is None:
                    errs.append(f"gabarito Q{n}: nao encontrada no json")
                    continue
                if q.get("anulada"):
                    continue
                if q["tipo"] == "objetiva" and q.get("gabarito") != letter:
                    errs.append(f"gabarito Q{n}: json={q.get('gabarito')} oficial={letter}")
        status = "OK" if not errs else "ERROS"
        ok = ok and not errs
        print(f"\n[{label}] {status} — {len(qs)} questões; gabarito_oficial_itens={len(g) if g else 'n/a'} semestre={j.get('semestre')}")
        for e in errs[:40]:
            print("   ", e)
    print("\nRESULTADO:", "TUDO OK" if ok else "HÁ ERROS")


if __name__ == "__main__":
    main()