import difflib
import json
import os
import re
import unicodedata

DATA = "/work/data"
JK = "/work/data/json"
CATALOG = "/work/data/assuntos.json"

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"] + \
         [f"fuvest_{ano}" for ano in range(2010, 2027)]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def load_catalog_strings():
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)
    exact = []
    for d in cat["plano_de_estudos_vestibular"]["disciplinas"]:
        for mod in d["modulos"]:
            for a in mod["assuntos"]:
                exact.append(a)
    return exact


EXPLICIT = {
    "Modernismo Brasileiro: 3ª Fase (Geração de 45/Pós-Modernismo)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Modernismo Brasileiro: 2ª Fase (Geração de 30 - romance regionalista e poesia de 30)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Modernismo Brasileiro: 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Modernismo Brasileiro: 1ª Fase (1922)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Literatura Brasileira: Realismo/Naturalismo (Machado de Assis)":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Literatura Brasileira: Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Literatura Brasileira: Modernismo Brasileiro: 2ª Fase (Geração de 30 - romance regionalista e poesia de 30)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Espaço amostral (discreto e continuação) e conceito de probabilidade":
        "Espaço amostral (discreto e contínuo) e conceito de probabilidade",
    "Classes gramaticais: preposição, conjunção":
        "Classes gramaticais: substantivo, adjetivo, artigo, numeral, pronome, verbo, advérbio, preposição, conjunção e interjeição",
    "Geopolítica da água":
        "Hidrografia: bacias hidrográficas mundiais e brasileiras e geopolítica da água",
    "Arces e ângulos: medidas (graus e radianos) e relações entre arcos":
        "Arcos e ângulos: medidas (graus e radianos) e relações entre arcos",
    "Arranjos, permutações e combinações simples":
        "Arranjos, permutações e combinações simples",
    "Literatura Brasileira: Modernismo Brasileiro":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Matriz e determinantes":
        "Matrizes e determinantes",
    "Literatura Brasileira: Modernismo Brasileiro: 1ª Fase (1922)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Literatura Brasileira: Modernismo Brasileiro: 2ª Fase (Geração de 30 - romance regionalista e poesia de 30)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Literatura Brasileira: Modernismo Brasileiro: 3ª Fase (Geração de 45/Pós-Modernismo)":
        "Modernismo Brasileiro: 1ª Fase (1922), 2ª Fase (Geração de 30 - romance regionalista e poesia de 30) e 3ª Fase (Geração de 45/Pós-Modernismo)",
    "Literatura Brasileira: Romantismo":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Literatura Brasileira: Parnasianismo e Simbolismo":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos)":
        "Literatura Brasileira: Quinhentismo, Barroco (Gregório de Matos), Arcadismo, Romantismo, Realismo/Naturalismo (Machado de Assis), Parnasianismo e Simbolismo",
    "Impactos ambientais: poluição, desmatamento, degradação de solos e conferências internacionais do clima":
        "Impactos ambientais: mudanças climáticas, efeito estufa, desmatamento, degradação de solos e conferências internacionais do clima",
}

LABELS = [f"univesp_{y}" for y in ("2017_2s", "2018_1s", "2018_2s", "2019_2")]
LABELS += [f"univesp_{ano}" for ano in range(2020, 2027)]
LABELS += [f"fuvest_{ano}" for ano in range(2010, 2027)]
LABELS += ["enem_2011_2dia"]
LABELS += [f"enem_{ano}_{dia}" for ano in range(2012, 2026) for dia in ("1dia", "2dia")]
LABELS += ["fatec_2010_1S", "fatec_2010_2S", "fatec_2011_2S"]
LABELS += [f"fatec_{ano}_{dia}" for ano in range(2012, 2021) for dia in ("1S", "2S")]
LABELS += ["fatec_2020_1S", "fatec_2023_1S", "fatec_2023_2S"]
LABELS += [f"unesp_{ano}" for ano in range(2010, 2021)]
LABELS += [f"unesp_{ano}_{dia}" for ano in (2021, 2022) for dia in ("1dia", "2dia")]
LABELS += [f"unesp_{ano}" for ano in range(2023, 2027)]


def load_catalog_areas():
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)
    areas = set()
    area_of = {}
    for d in cat["plano_de_estudos_vestibular"]["disciplinas"]:
        areas.add(d["area"])
        for mod in d["modulos"]:
            for a in mod["assuntos"]:
                area_of.setdefault(a, set()).add(d["area"])
    return areas, area_of


def best_match(target, exact):
    t = norm(target)
    best, best_score = None, 0.0
    for cand in exact:
        c = norm(cand)
        if t == c:
            return cand, 1.0
        score = difflib.SequenceMatcher(None, t, c).ratio()
        if t in c:
            score = max(score, 0.75 + min(len(t), 60) / 200.0)
        if score > best_score:
            best, best_score = cand, score
    return best, best_score


def main():
    exact = load_catalog_strings()
    areas, area_of = load_catalog_areas()
    total_fixed = 0
    total_unmatched = []
    for label in LABELS:
        path = f"{JK}/{label}_questoes.json"
        if not os.path.exists(path):
            print(f"[{label}] sem arquivo, pulado", flush=True)
            continue
        j = json.load(open(path, encoding="utf-8"))
        fixed = 0
        for q in j["questoes"]:
            for a in q.get("areas", []):
                if a["area"] not in areas:
                    cands = set()
                    for s in a["assuntos"]:
                        cands |= area_of.get(s, set())
                    if cands:
                        a["area"] = sorted(cands)[0]
                        fixed += 1
                    else:
                        total_unmatched.append((label, q["numero"], f"area>{a['area']}"))
                new_list = []
                for s in a["assuntos"]:
                    if s in EXPLICIT:
                        s2 = EXPLICIT[s]
                    else:
                        s2, score = best_match(s, exact)
                        if s2 is None or score < 0.45:
                            total_unmatched.append((label, q["numero"], s))
                            s2 = s
                    if s2 != s:
                        fixed += 1
                    new_list.append(s2)
                a["assuntos"] = new_list
        with open(path, "w", encoding="utf-8") as f:
            json.dump(j, f, ensure_ascii=False, indent=2)
        total_fixed += fixed
        if fixed:
            print(f"[{label}] {fixed} assuntos ajustados", flush=True)
    print("TOTAL ajustados:", total_fixed, flush=True)
    if total_unmatched:
        print("SEM MATCH (mantidos):")
        for u in total_unmatched:
            print("   ", u)


if __name__ == "__main__":
    main()