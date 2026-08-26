import json
import os
import re
import sys

DATA = "/work/data"
GAB = "/work/tmp/gabaritos"
JK = "/work/data/json"
CATALOG = "/work/data/assuntos.json"

LABELS = [f"univesp_{y}" for y in ("2017_2s", "2018_1s", "2018_2s", "2019_2")]
LABELS += [f"univesp_{ano}" for ano in range(2020, 2027)]

FUVEST_LABELS = [f"fuvest_{ano}" for ano in range(2010, 2027)]

ENEM_LABELS = ["enem_2011_2dia"]
ENEM_LABELS += [f"enem_{ano}_{dia}" for ano in range(2012, 2026) for dia in ("1dia", "2dia")]

FATEC_LABELS = ["fatec_2010_1S", "fatec_2010_2S", "fatec_2011_2S"]
FATEC_LABELS += [f"fatec_{ano}_{dia}" for ano in range(2012, 2021) for dia in ("1S", "2S")]
FATEC_LABELS += ["fatec_2020_1S", "fatec_2023_1S", "fatec_2023_2S"]

UNESP_LABELS = [f"unesp_{ano}" for ano in range(2010, 2021)]
UNESP_LABELS += [f"unesp_{ano}_{dia}" for ano in (2021, 2022) for dia in ("1dia", "2dia")]
UNESP_LABELS += [f"unesp_{ano}" for ano in range(2023, 2027)]

ALL = LABELS + FUVEST_LABELS + ENEM_LABELS + FATEC_LABELS + UNESP_LABELS


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


def parse_fuvest_gabarito(label):
    """Gabarito oficial FUVEST 1ª fase (prova versão V / V1).

    Formatos conhecidos:
    2010          tabela RESPOSTA+GRUPO: letras em ordem = gabarito do grupo V;
    2011–2017     tokens "V 01‐B  V 46‐A  ...";
    2018–2026     pares "01-B 46-A" ou "1 B 46 C" por coluna de versão (V/V1 = 1ª coluna).
    "*" = questão anulada (None).
    """
    path = f"{GAB}/{label}_gabarito.txt"
    text = open(path, encoding="utf-8").read()

    # 1) tokens por versão: "V 01-B ... V 46-A" (anulada pode vir como '*' ou 'ANULADA')
    toks = re.findall(r"\bV\s+(\d{1,2})\s*[-‐]\s*(?:([A-E*])|(Anulada|ANULADA))\b", text)
    if len(toks) >= 80:
        g = {}
        for n, let, word in toks:
            g[int(n)] = None if (word or let == "*") else let.lower()
        return g

    # 2) tabela RESPOSTA+GRUPO (2010): cada linha tem 2 letras — a 1ª = Q(n) do grupo V,
    #    a 2ª = Q(n+45); bloco esquerdo cobre Q1-45, direito Q46-90
    if "GRUPO" in text:
        left, right = [], []
        for line in text.splitlines():
            ls = re.findall(r"\b[A-E]\b", line)
            if len(ls) >= 2:
                left.append(ls[0])
                right.append(ls[1])
        if len(left) >= 40:
            g = {}
            for i, l in enumerate(left[:45]):
                g[i + 1] = l.lower()
            for i, l in enumerate(right[:45]):
                g[46 + i] = l.lower()
            return g

    # 3) colunas por versão: a primeira coluna é V (ou V1); cada linha tem 2 pares (n e n+45)
    g = {}
    for line in text.splitlines():
        pairs = re.findall(r"(\d{1,3})\s*[-‐–]?\s*(?:([A-E*])|(Anulada|ANULADA))", line)
        if len(pairs) >= 4:
            for n, let, word in pairs[0:2]:
                g[int(n)] = None if (word or let == "*") else let.lower()
    return g


def main():
    areas, assuntos = load_catalog()
    labels = sys.argv[1:] or ALL
    ok = True
    for label in labels:
        path = f"{JK}/{label}_questoes.json"
        j = json.load(open(path, encoding="utf-8"))
        qs = j["questoes"]
        nums = [q["numero"] for q in qs]
        errs = []
        # sequential coverage (sem lacunas; caderno do 2º dia do ENEM começa na 91)
        if nums != list(range(min(nums), max(nums) + 1)):
            errs.append(f"numeracao com lacunas/não sequencial: {nums[:5]}...{nums[-3:] if len(nums) > 5 else ''}")
        if min(nums) not in (1, 91):
            errs.append(f"numeracao começa em {min(nums)} (esperado 1 ou 91)")
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
            if not q.get("extraida_parcialmente") and (not q.get("enunciado") or (len(q.get("enunciado", "")) < 10 and not q.get("midia"))):
                errs.append(f"Q{n}: enunciado curto/ausente")
        # gabarito cross-check
        g = parse_fuvest_gabarito(label) if label.startswith("fuvest") else parse_gabarito_md(label)
        if g:
            for n, letter in g.items():
                q = next((q for q in qs if q["numero"] == n), None)
                if q is None:
                    errs.append(f"gabarito Q{n}: nao encontrada no json")
                    continue
                if letter is None:
                    if not q.get("anulada") or q.get("gabarito") is not None:
                        errs.append(f"gabarito Q{n}: oficial ANULADA; json anulada={q.get('anulada')} gabarito={q.get('gabarito')}")
                    continue
                if q.get("anulada"):
                    errs.append(f"gabarito Q{n}: json anulada mas oficial={letter}")
                    continue
                if q["tipo"] == "objetiva" and q.get("gabarito") != letter:
                    errs.append(f"gabarito Q{n}: json={q.get('gabarito')} oficial={letter}")
        # checagem reversa: questão objetiva que o gabarito oficial NÃO lista
        if g:
            for q in qs:
                if q["tipo"] == "objetiva" and q["numero"] not in g:
                    errs.append(f"Q{q['numero']}: não consta no gabarito oficial (extra/fantasma)")
        status = "OK" if not errs else "ERROS"
        ok = ok and not errs
        print(f"\n[{label}] {status} — {len(qs)} questões; gabarito_oficial_itens={len(g) if g else 'n/a'} semestre={j.get('semestre')}")
        for e in errs[:40]:
            print("   ", e)
    print("\nRESULTADO:", "TUDO OK" if ok else "HÁ ERROS")


if __name__ == "__main__":
    main()