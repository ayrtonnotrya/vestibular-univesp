"""Grava a página (1-indexed) de cada questão nos JSONs extraídos.

Fonte de verdade: os PDFs são insumo descartável; depois desta rodada, o app
lê `pagina` exclusivamente dos JSONs (`data/json/<label>_questoes.json` /
`_imagens.json`), sem abrir PDF em runtime.

Ordem das fontes para cada questão:
1. página registrada da figura (bbox de `_imagens.json`) quando houver — o
   enquadramento do app usa essa mesma página, garantindo consistência;
2. página localizada pelo texto do enunciado no PDF (trechos progressivamente
   menores + votação de tokens p/ notações matemáticas divergentes);
3. "Página N:" na descrição de mídia do JSON;
4. interpolação entre as páginas conhecidas do exame.

Caso a página vinda do modelo esteja inválida (capa/fora do intervalo), ela é
recalculada e corrigida também em `_imagens.json` (para o bbox continuar na
mesma página do JSON da questão).

Uso: python fix_paginas.py [labels...]   (default: todos os exames em data/json)
PDFs: env PDF_DIR (default /work/data, fallback /work/tmp/bkp_pdfs).
"""
import json
import os
import re
import sys
from pathlib import Path

import pymupdf

DATA = "/work/data"
JK = "/work/data/json"

LABELS = [f"univesp_{y}" for y in ("2017_2s", "2018_1s", "2018_2s", "2019_2")]
LABELS += [f"univesp_{ano}" for ano in range(2020, 2025)]
LABELS += [f"fuvest_{ano}" for ano in range(2010, 2027)]


def norm(s):
    """Minúsculas; não alfanumérico vira espaço; colapsa espaços.

    Mantém separadores porque a extração dos PDFs da FUVEST insere o caractere
    ¬ (U+00AC) entre palavras; removê-lo colaria as palavras.
    """
    s = s.lower()
    s = "".join(c if c.isalnum() else " " for c in s)
    return re.sub(r"\s+", " ", s)


def pdf_path(label):
    for base in (os.environ.get("PDF_DIR", DATA), "/work/tmp/bkp_pdfs"):
        p = Path(base) / f"{label}_questoes.pdf"
        if p.exists():
            return p
    return None


def page_texts(path):
    doc = pymupdf.open(str(path))
    raw = [p.get_text() for p in doc]
    n = doc.page_count
    doc.close()
    texts = []
    for t in raw:
        t = t.replace("-\n", "").replace("-\r", "")
        texts.append(norm(t))
    return texts, n, raw


def text_page(texts, enunciado):
    needle = norm(enunciado.strip()[:40])
    if needle:
        for pag, t in enumerate(texts, 1):
            if pag > 1 and needle in t:
                return pag
        short = norm(enunciado.strip()[:20])
        for pag, t in enumerate(texts, 1):
            if pag > 1 and short in t:
                return pag
    toks = [w for w in norm(enunciado).split() if len(w) >= 5]
    if toks:
        best, bestn = 1, 0
        for pag, t in enumerate(texts, 1):
            if pag == 1:
                continue
            n = sum(1 for w in toks if w in t)
            if n > bestn:
                best, bestn = pag, n
        if bestn >= 3 and bestn / len(toks) >= 0.6:
            return best
    return 1


def midia_page(q):
    for m in q.get("midia") or []:
        mh = re.search(r"[Pp]ágina\s*(\d+)", m)
        if mh:
            return int(mh.group(1))
    return None


def interp_page(numero, known):
    if not known:
        return None
    antes = [x for x in known if x[0] <= numero]
    depois = [x for x in known if x[0] >= numero]
    if antes and depois:
        (n1, p1), (n2, p2) = antes[-1], depois[0]
        if n1 == n2:
            return p1
        return round(p1 + (numero - n1) * (p2 - p1) / (n2 - n1))
    return antes[-1][1] if antes else depois[0][1]


def valid(page, npages):
    return isinstance(page, int) and 1 < page <= npages


def fix_label(label):
    jq = Path(JK) / f"{label}_questoes.json"
    ji = Path(JK) / f"{label}_imagens.json"
    if not jq.exists():
        print(f"[{label}] sem {jq} — pulado", flush=True)
        return
    questoes = json.loads(jq.read_text())
    imagens = json.loads(ji.read_text()) if ji.exists() else {"figuras_coordenadas": {}}
    figs = imagens["figuras_coordenadas"]

    pdf = pdf_path(label)
    if pdf is None:
        print(f"[{label}] PDF não encontrado — pulado (JSONs intactos)", flush=True)
        return
    texts, npages, _ = page_texts(pdf)

    known = [
        (int(str(k)), recs[0]["pagina"])
        for k, recs in figs.items()
        if recs and isinstance(recs[0].get("pagina"), int)
        and valid(recs[0]["pagina"], npages)
    ]
    known.sort()
    invalidos = []
    for q in questoes["questoes"]:
        n = q["numero"]
        recs = figs.get(str(n), [])
        bbox = recs[0].get("bbox") if recs and isinstance(recs[0].get("bbox"), list) else None
        rec = recs[0].get("pagina") if recs and isinstance(recs[0].get("pagina"), int) else None

        pagina = rec if (bbox is not None and valid(rec, npages)) else None
        if pagina is None:
            pagina = text_page(texts, q["enunciado"]) if texts else 1
        if not valid(pagina, npages):
            pagina = midia_page(q)
        if not valid(pagina, npages):
            pagina = interp_page(n, known)
        if not valid(pagina, npages):
            pagina = 1
            invalidos.append(n)
        q["pagina"] = pagina
        if bbox is not None and not valid(rec, npages):
            recs[0]["pagina"] = pagina

    jq.write_text(json.dumps(questoes, ensure_ascii=False, indent=2), encoding="utf-8")
    ji.write_text(json.dumps(imagens, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{label}] {len(questoes['questoes'])} questões com pagina; "
          f"inválidas={invalidos}; PDF={pdf.name if pdf else '-'}",
          flush=True)


def main():
    labels = sys.argv[1:] or LABELS
    for label in labels:
        fix_label(label.removesuffix("_questoes"))


if __name__ == "__main__":
    main()