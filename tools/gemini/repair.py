import difflib
import json
import os
import re
import unicodedata

DATA = "/work/data"
JK = "/work/data/json"
CATALOG = "/work/data/assuntos.json"

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"]


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
}


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
    total_fixed = 0
    total_unmatched = []
    for label in LABELS:
        path = f"{JK}/{label}_questoes.json"
        j = json.load(open(path, encoding="utf-8"))
        fixed = 0
        for q in j["questoes"]:
            for a in q.get("areas", []):
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