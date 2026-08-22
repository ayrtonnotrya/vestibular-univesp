"""Extrai figuras/imagens/tabelas dos PDFs de provas UNIVESP usando PyMuPDF.

O bbox gravado pelo Gemini é usado apenas como SEED (localização aproximada)
e é refinado com a geometria real do PDF:

- Imagens raster embutidas: page.get_image_info() -> retângulo exato;
- Figuras vetoriais (gráficos/esquemas): agrupamento espacial de
  page.get_drawings() -> cluster de retângulos.

Estratégia de seleção (ver `figure_region`):
1) se existe uma imagem raster real que sobrepõe bem o seed -> usa o retângulo
   exato dela;
2) senão, agrupa os desenhos vetoriais próximos ao seed (janela ao redor) e
   expande com as palavras de texto vizinhas (títulos, legendas, rótulos de
   eixo) para o recorte não cortar as bordas.

Saída: PNG em data/imagens/univesp_<label>/, recortado do próprio PDF via
PyMuPDF (page.get_pixmap(clip=...)).

Formato do bbox no JSON: [y0, x0, y1, x1] em escala 0-1000 (permil da
dimensão da página). Convertemos para pontos do PDF:
    x0 = bbox[1]/1000*W ; y0 = bbox[0]/1000*H
"""
import argparse
import json
import os
from collections import defaultdict

import pymupdf

DATA = "/work/data"
JSON_DIR = os.path.join(DATA, "json")
IMG_DIR = os.path.join(DATA, "imagens")
DPI = 200

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"]

# fração da largura da página acima da qual um raster é tratado como faixa de
# alternativa/cabeçalho (e não figura)
W_STRIP = 0.86


def union(rects):
    xs0 = min(r.x0 for r in rects)
    ys0 = min(r.y0 for r in rects)
    xs1 = max(r.x1 for r in rects)
    ys1 = max(r.y1 for r in rects)
    return pymupdf.Rect(xs0, ys0, xs1, ys1)


def _cluster(rects, gap=12):
    """Agrupa retângulos espacialmente próximos (percolação por gap)."""
    groups = []
    for r in rects:
        if r.is_empty or r.width < 2 or r.height < 2:
            continue
        placed = False
        for g in groups:
            u = union(g)
            if pymupdf.Rect(u.x0 - gap, u.y0 - gap, u.x1 + gap, u.y1 + gap).intersects(r):
                g.append(r)
                placed = True
                break
        if not placed:
            groups.append([r])
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                ui = union(groups[i])
                uj = union(groups[j])
                if ui.intersects(pymupdf.Rect(uj.x0 - gap, uj.y0 - gap,
                                              uj.x1 + gap, uj.y1 + gap)):
                    groups[i] = groups[i] + groups[j]
                    groups.pop(j)
                    merged = True
                    break
            if merged:
                break
    return groups


def raw_rasters(page, w_page):
    """Imagens raster que não são faixas de alternativa/cabeçalhos nem objetos menores."""
    out = []
    for info in page.get_image_info(xrefs=True):
        r = pymupdf.Rect(info["bbox"])
        if r.width < 25 or r.height < 25:
            continue
        # faixa de alternativa/cabeçalho ocupa quase toda a largura
        if r.width >= W_STRIP * w_page:
            continue
        out.append(r)
    return out


def raster_region(seed, rasters, min_overlap=0.20):
    """Retorna o raster real que mais sobrepõe o seed (se sobrepõe o bastante)."""
    best = None
    best_ov = 0.0
    for r in rasters:
        ov = abs((r & seed).get_area())
        if ov > best_ov:
            best_ov = ov
            best = r
    if best is not None and best_ov > min_overlap * seed.get_area():
        return best
    return None


def vector_region(page, seed, words):
    """Cluster vetorial localizado à janela do seed, expandido com texto vizinho."""
    pad = 0.18 * max(seed.width, seed.height) + 45
    win = pymupdf.Rect(seed.x0 - pad, seed.y0 - pad, seed.x1 + pad, seed.y1 + pad)
    start = [it["rect"] for it in page.get_drawings()]
    start = [r for r in start if r.width > 6 and r.height > 6 and win.intersects(r)]
    if not start:
        return None

    best_core = None
    best_ov = -1.0
    for g in _cluster(start):
        u = union(g)
        ov = abs((u & seed).get_area())
        if ov > best_ov:
            best_ov = ov
            best_core = u
    if best_core is None:
        return None

    box = pymupdf.Rect(best_core.x0 - 15, best_core.y0 - 15,
                       best_core.x1 + 15, best_core.y1 + 15)
    near = [w for w in words if box.intersects(w)]
    return union([best_core] + near)


def figure_region(page, seed, w_page):
    """Região (em pontos do PDF) que melhor corresponde ao seed."""
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    rasters = raw_rasters(page, w_page)

    r = raster_region(seed, rasters)
    if r is not None:
        return r
    return vector_region(page, seed, words)


def seed_rect(bbox, w_page, h_page):
    """Converte o bbox [y0,x0,y1,x1] (0-1000) do JSON em rect de pontos."""
    x0 = bbox[1] / 1000 * w_page
    x1 = bbox[3] / 1000 * w_page
    y0 = bbox[0] / 1000 * h_page
    y1 = bbox[2] / 1000 * h_page
    return pymupdf.Rect(x0, y0, x1, y1)


def render_and_crop(page, rect, dpi):
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
    return page.get_pixmap(matrix=matrix, clip=rect)


def extract_label(label, dpi):
    jpath = os.path.join(JSON_DIR, f"{label}_imagens.json")
    pdf = os.path.join(DATA, f"{label}_questoes.pdf")
    if not os.path.exists(jpath) or not os.path.exists(pdf):
        print(f"[{label}] falta json/pdf, pulando", flush=True)
        return
    doc = pymupdf.open(pdf)
    data = json.load(open(jpath, encoding="utf-8"))
    figs = data.get("figuras_coordenadas", {})
    out_dir = os.path.join(IMG_DIR, label)
    os.makedirs(out_dir, exist_ok=True)

    n = 0
    by_page = defaultdict(list)
    for q, entries in figs.items():
        for i, e in enumerate(entries):
            b = e.get("bbox")
            if not (isinstance(b, list) and len(b) == 4):
                print(f"[{label}] Q{q}: bbox inválido, ignorando", flush=True)
                continue
            by_page[e["pagina"]].append((q, i, e))

    for page_num in sorted(by_page, key=int):
        page = doc[int(page_num) - 1]
        w_page, h_page = page.rect.width, page.rect.height
        for q, i, e in by_page[page_num]:
            seed = seed_rect(e["bbox"], w_page, h_page)
            if seed.is_empty:
                print(f"[{label}] Q{q} pg{page_num}: seed vazio, pulando", flush=True)
                continue
            region = figure_region(page, seed, w_page)
            if region is None or region.is_empty:
                print(f"[{label}] Q{q} pg{page_num}: nada encontrado, pulando", flush=True)
                continue
            # proteção: não devolver a página inteira (faixa de alternativa que
            # contaminou o cluster vetorial)
            if region.width >= 0.95 * w_page or region.height >= 0.95 * h_page:
                print(f"[{label}] Q{q} pg{page_num}: região virou página, pulando", flush=True)
                continue
            pix = render_and_crop(page, region, dpi)
            fname = f"q{q}_{i:02d}_{e.get('tipo', 'img')}.png"
            pix.save(os.path.join(out_dir, fname))
            n += 1
            print(f"[{label}] Q{q} pg{page_num} [{e.get('tipo')}] seed={e['bbox']} "
                  f"regiao=[{region.x0:.0f},{region.y0:.0f},{region.x1:.0f},{region.y1:.0f}] "
                  f"-> {fname}", flush=True)
    print(f"[{label}] {n} figuras extraídas em {out_dir}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="*", default=LABELS)
    ap.add_argument("--dpi", type=int, default=DPI)
    args = ap.parse_args()
    for label in args.labels or LABELS:
        extract_label(label, args.dpi)


if __name__ == "__main__":
    main()
