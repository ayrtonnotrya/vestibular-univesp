"""Renderiza cada página (inteira) das provas UNIVESP em PNG de alta resolução.

Saída: data/paginas/<label>_p<NNN>.png

Usar como base do app de curadoria manual (ver app/curator.py): em vez de
depender de auto-crop, o humano vê a página e enquadra a figura.
"""
import argparse
import os

import pymupdf

DATA = "/work/data"
PAGES_DIR = os.path.join(DATA, "paginas")
DPI = 200

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"]


def render(label, dpi):
    pdf = os.path.join(DATA, f"{label}_questoes.pdf")
    if not os.path.exists(pdf):
        print(f"[{label}] sem {pdf}", flush=True)
        return 0
    doc = pymupdf.open(pdf)
    out_dir = os.path.join(PAGES_DIR, label)
    os.makedirs(out_dir, exist_ok=True)
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix)
        path = os.path.join(out_dir, f"p{i:03d}.png")
        pix.save(path)
    print(f"[{label}] {len(doc)} páginas -> {out_dir}", flush=True)
    return len(doc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="*", default=LABELS)
    ap.add_argument("--dpi", type=int, default=DPI)
    args = ap.parse_args()
    total = 0
    for label in args.labels or LABELS:
        total += render(label, args.dpi)
    print(f"total de páginas renderizadas: {total}", flush=True)


if __name__ == "__main__":
    main()
