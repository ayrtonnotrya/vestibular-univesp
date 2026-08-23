"""Renderiza cada página (inteira) das provas em JPEG colorido (qualidade alta).

Saída: data/paginas/<label>/p<NNN>.jpg

As imagens são usadas pelo app de estudo (app/panzoom.py) no lugar da
renderização realtime do PDF. JPEG manter a cor (gráficos dependem dela).

Como rodar (imagem do app tem PyMuPDF):
  docker run --rm -v "$PWD/data:/app/data" -w /app vestibular-app:latest \
    python tools/gemini/render_pages.py [labels...]
"""
import glob
import os

import pymupdf

DATA = "/app/data"
PAGES_DIR = os.path.join(DATA, "paginas")
MAX_SIDE = 1400
DPI = 200
QUALITY = 75


def labels_disponiveis():
    pdfs = sorted(glob.glob(os.path.join(DATA, "*_questoes.pdf")))
    return [os.path.basename(p)[: -len("_questoes.pdf")] for p in pdfs]


def render(label, quality=QUALITY):
    pdf = os.path.join(DATA, f"{label}_questoes.pdf")
    if not os.path.exists(pdf):
        print(f"[{label}] sem {pdf}", flush=True)
        return 0
    doc = pymupdf.open(pdf)
    out_dir = os.path.join(PAGES_DIR, label)
    os.makedirs(out_dir, exist_ok=True)
    for i, page in enumerate(doc, start=1):
        rect = page.rect
        scale = min(MAX_SIDE / rect.width, MAX_SIDE / rect.height, DPI / 72)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        path = os.path.join(out_dir, f"p{i:03d}.jpg")
        pix.save(path, jpg_quality=quality)
    print(f"[{label}] {len(doc)} páginas -> {out_dir}", flush=True)
    return len(doc)


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="*", default=labels_disponiveis())
    ap.add_argument("--quality", type=int, default=QUALITY)
    args = ap.parse_args()
    for label in args.labels:
        render(label, args.quality)


if __name__ == "__main__":
    main()