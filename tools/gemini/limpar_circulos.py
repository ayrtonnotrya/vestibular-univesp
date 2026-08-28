"""Remove círculos verdes que marcam a alternativa correta em "provas resolvidas".

O círculo é um traçado vetorial verde puro, sem outros conteúdos:
- ENEM 2012–2014 (fonte: Curso Objetivo): um Form XObject por círculo
  (`0 0.666656 0 RG` + caminho + `h S`);
- ENEM 2011 (2º dia): ops diretas no conteúdo da página
  (`/Cs6 CS 0 0.66667 0 SCN` + caminho + `s`/`S`).

A limpeza esvazia os Forms verdes isolados e remove os trechos verdes do
conteúdo das páginas — só o traço do círculo é eliminado; texto, imagens e
demais vetores ficam intactos. Não sobra vestígio identificável do gabarito.

Verificação por arquivo: reconta círculos nos desenhos (0 após), confere o
texto extraído antes/depois (idêntico) e re-renderiza as páginas afetadas
exigindo 0 pixels verdes remanescentes (o total de verde da página cai
exatamente pela soma dos círculos removidos).

Uso: python limpar_circulos.py [labels...]
      --check             só reporta (não escreve nada)
      --out DIR           grava PDFs limpos em DIR, sem tocar nos originais
      --render            re-renderiza data/paginas/<label>/ após limpar
Padrão: limpa no lugar em `data/<label>_questoes.pdf` (env PDF_DIR, default
/work/data), preservando o original em /work/tmp/bkp_pdfs/.
"""
import argparse
import os
import re
import shutil
import sys
from collections import deque
from pathlib import Path

import pymupdf

DATA = "/work/data"
BKP = "/work/tmp/bkp_pdfs"

LABELS = ["enem_2011_2dia"]
LABELS += [f"enem_{ano}_{dia}" for ano in range(2012, 2015) for dia in ("1dia", "2dia")]

GREEN_RE = re.compile(
    rb"([+-]?\d*\.?\d+)\s+0\.66\d+\s+([+-]?\d*\.?\d+)\s+(?:SCN|RG)"
)
NUM_RE = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
DELIMS = b"[]()<>{}%/"

PATH_OPS = {b"m": 2, b"c": 6, b"l": 2, b"v": 4, b"y": 4, b"re": 4, b"h": 0}
TERMINAL_OPS = {b"S", b"s"}
ALLOWED_OPS = {
    b"w", b"J", b"j", b"M", b"i", b"d", b"gs", b"cm",
    b"CS", b"cs", b"SCN", b"scn", b"RG", b"rg", b"SC", b"sc",
    b"n", b"W", b"q", b"Q",
}
REJECT_OPS = {
    b"f", b"F", b"Tj", b"TJ", b"BT", b"ET", b"Do", b"BI", b"EI",
    b"sh", b"BMC", b"EMC", b"BDC", b"DP", b"MP", b"Tf",
}

MIN_RING = 6.0
MAX_RING = 60.0
RING_RATIO = (0.55, 1.8)


def pdf_path(label):
    for base in (os.environ.get("PDF_DIR", DATA), "/work/tmp/bkp_pdfs"):
        p = Path(base) / f"{label}_questoes.pdf"
        if p.exists():
            return p
    return None


def greenish(r, g, b):
    """Verde de marcador (variantes observadas: 0 0.6667 0 e 0.25 0.6667 0.3333)."""
    return (
        0.45 <= g <= 0.85
        and r < 0.45
        and b < 0.5
        and g >= r
        and g >= b
        and g - max(r, b) >= 0.1
    )


def ring_ok(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if not (MIN_RING <= max(w, h) <= MAX_RING):
        return False
    if w <= 0 or h <= 0:
        return False
    return RING_RATIO[0] <= w / h <= RING_RATIO[1]


def scan_tokens(data, start):
    """Do `start`, percorre tokens de caminho/estado gráfico até `s`/`S`.

    Devolve o índice do fim do op terminal se o trecho é só caminho/estado e o
    caminho parece um anel pequeno; senão None (não mexer no trecho).
    """
    n = len(data)
    i = start
    pend = []
    pts = []
    m_count = 0
    depth = 0
    while i < n:
        while i < n and data[i : i + 1].isspace():
            i += 1
        if i >= n:
            return None
        b = data[i : i + 1]
        if b == b"[":
            depth += 1
            i += 1
            continue
        if b == b"]":
            depth = max(0, depth - 1)
            i += 1
            continue
        if b in (b"(", b")", b"<", b">", b"{", b"}", b"%"):
            return None  # string/dict/array/comment no meio: não mexer
        if b == b"/":
            j = i
            while j < n and not data[j : j + 1].isspace():
                j += 1
            i = j
            continue
        j = i
        while j < n and not data[j : j + 1].isspace() and data[j : j + 1] not in DELIMS:
            j += 1
        tok = data[i:j]
        i = j
        if not tok:
            continue
        if tok.startswith(b"/"):
            continue
        if NUM_RE.fullmatch(tok):
            if depth == 0:
                pend.append((tok, i))
            continue
        if tok in TERMINAL_OPS:
            if m_count == 1 and pts and ring_ok(pts):
                return i
            return None
        if tok in REJECT_OPS:
            return None
        if tok in PATH_OPS and depth == 0 and len(pend) >= PATH_OPS[tok]:
            k = PATH_OPS[tok]
            if k == 0:
                continue
            nums = [float(t[0]) for t in pend[-k:]]
            pend = pend[:-k]
            if tok == b"m":
                m_count += 1
                pts.append((nums[0], nums[1]))
            elif tok == b"l":
                pts.append((nums[0], nums[1]))
            elif tok == b"re":
                pts.append((nums[0], nums[1]))
                pts.append((nums[0] + nums[2], nums[1] + nums[3]))
            else:
                pts.append((nums[0], nums[1]))
                pts.append((nums[2], nums[3]))
                pts.append((nums[4], nums[5]))
            if m_count > 1:
                return None
            continue
        if tok in ALLOWED_OPS:
            pend = []
            continue
        return None
    return None


def strip_stream_green(data):
    out = bytearray(data)
    removed = 0
    pos = 0
    while True:
        m = GREEN_RE.search(bytes(out[pos:]))
        if not m:
            break
        off = pos + m.start()
        if not greenish(float(m.group(1)), 0.6667, float(m.group(2))):
            pos = off + m.end()
            continue
        # guarda estrutural: o token antes das operações deve ser `CS` (cor de
        # traço vinculada a /Name CS), como nas páginas do ENEM 2011.
        pre = out[:off]
        i = len(pre) - 1
        while i >= 0 and pre[i : i + 1].isspace():
            i -= 1
        if i < 0:
            break
        j = i
        while j >= 0 and not pre[j : j + 1].isspace() and pre[j : j + 1] not in DELIMS:
            j -= 1
        prev = bytes(pre[j + 1 : i + 1])
        start = j + 1
        if prev == b"CS":
            k = j - 1
            while k >= 0 and pre[k : k + 1].isspace():
                k -= 1
            l = k
            while l >= 0 and not pre[l : l + 1].isspace() and pre[l : l + 1] not in DELIMS:
                l -= 1
            if bytes(pre[l + 1 : k + 1]).startswith(b"/"):
                start = l + 1
        end = scan_tokens(bytes(out), pos + m.end())
        if end is None:
            break
        del out[start:end]
        removed += 1
    return bytes(out), removed


def green_only_forms(pdf):
    removed = 0
    for xref in range(1, pdf.xref_length()):
        try:
            if pdf.xref_get_key(xref, "Subtype") != ("name", "/Form"):
                continue
            data = pdf.xref_stream(xref)
            if not data or len(data) > 2000:
                continue
        except Exception:
            continue
        if is_green_only_form_stream(data):
            pdf.update_stream(xref, b"n\n", compress=False)
            removed += 1
    return removed


def is_green_only_form_stream(data):
    n = len(data)
    i = 0
    pend = []
    colors = 0
    m_count = 0
    pts = []
    depth = 0
    green = False
    while i < n:
        while i < n and data[i : i + 1].isspace():
            i += 1
        if i >= n:
            break
        b = data[i : i + 1]
        if b == b"[":
            depth += 1
            i += 1
            continue
        if b == b"]":
            depth = max(0, depth - 1)
            i += 1
            continue
        if b in (b"(", b")", b"<", b">", b"{", b"}", b"%"):
            return False
        if b == b"/":
            j = i
            while j < n and not data[j : j + 1].isspace():
                j += 1
            i = j
            continue
        j = i
        while j < n and not data[j : j + 1].isspace() and data[j : j + 1] not in DELIMS:
            j += 1
        tok = data[i:j]
        i = j
        if not tok:
            continue
        if tok.startswith(b"/"):
            continue
        if NUM_RE.fullmatch(tok):
            if depth == 0:
                pend.append(float(tok))
            continue
        if tok in (b"RG", b"rg", b"SCN", b"scn", b"SC", b"sc"):
            colors += 1
            if len(pend) >= 3:
                r, g, b = pend[-3:]
                if greenish(r, g, b):
                    green = True
            pend = []
            continue
        if tok in (b"G", b"g"):
            colors += 1
            if pend and 0.45 < pend[-1] < 0.85:
                green = True
            pend = []
            continue
        if tok in PATH_OPS and depth == 0 and len(pend) >= PATH_OPS[tok]:
            k = PATH_OPS[tok]
            if k == 0:
                continue
            nums = pend[-k:]
            pend = pend[:-k]
            if tok == b"m":
                m_count += 1
                pts.append((nums[0], nums[1]))
            elif tok == b"l":
                pts.append((nums[0], nums[1]))
            elif tok == b"re":
                pts.append((nums[0], nums[1]))
                pts.append((nums[0] + nums[2], nums[1] + nums[3]))
            else:
                pts.append((nums[0], nums[1]))
                pts.append((nums[2], nums[3]))
                pts.append((nums[4], nums[5]))
            if m_count > 1:
                return False
            continue
        if tok in (b"w", b"J", b"j", b"M", b"i", b"d", b"gs", b"cm", b"n", b"W", b"q", b"Q"):
            pend = []
            continue
        if tok in (b"S", b"s") and m_count == 1 and pts and ring_ok(pts):
            pend = []
            continue
        return False
    return colors == 1 and green and m_count == 1


def drawing_rings(page):
    n = 0
    for d in page.get_drawings():
        c = d.get("color")
        if not (c and c[1] > 0.5 and c[0] < 0.4 and c[2] < 0.4):
            continue
        if d["type"] != "s" or len(d["items"]) != 4:
            continue
        w = d["rect"].width
        h = d["rect"].height
        if abs(w - h) <= 8 and w < 60:
            n += 1
    return n


class Pixel:
    __slots__ = ("h", "n", "samples", "w")

    def __init__(self, pix):
        self.w = pix.width
        self.h = pix.height
        self.n = pix.n
        self.samples = pix.samples

    def green(self, x, y):
        o = (y * self.w + x) * self.n
        r, g, b = self.samples[o], self.samples[o + 1], self.samples[o + 2]
        return g > 100 and g > r + 45 and g > b + 45


def green_pixel_components(pix):
    mask = {}
    for y in range(pix.h):
        for x in range(pix.w):
            if pix.green(x, y):
                mask[(x, y)] = True
    visited = set()
    comps = []
    for (x, y) in mask:
        if (x, y) in visited:
            continue
        q = deque([(x, y)])
        visited.add((x, y))
        pts = [(x, y)]
        while q:
            cx, cy = q.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (cx + dx, cy + dy)
                    if nb in mask and nb not in visited:
                        visited.add(nb)
                        q.append(nb)
                        pts.append(nb)
        comps.append(pts)
    rings_px = 0
    total = 0
    for c in comps:
        xs = [p[0] for p in c]
        ys = [p[1] for p in c]
        w = max(xs) - min(xs) + 1
        h = max(ys) - min(ys) + 1
        total += len(c)
        if 6 <= max(w, h) <= 80 and 0.55 <= w / h <= 1.8:
            rings_px += len(c)
    return total, rings_px, len(comps)


def pixel_page(pdf, pno, zoom=1.0):
    page = pdf[pno]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    return green_pixel_components(Pixel(pix))


def process_label(label, out_dir, check_only, do_render):
    src = pdf_path(label)
    if src is None:
        print(f"[{label}] PDF não encontrado", flush=True)
        return
    doc = pymupdf.open(str(src))
    pre_texts = [p.get_text() for p in doc]
    pre_rings = {}
    pre_pix = {}
    for pno in range(len(doc)):
        n = drawing_rings(doc[pno])
        if n:
            pre_rings[pno + 1] = n
            pre_pix[pno + 1] = pixel_page(doc, pno)
    total_rings = sum(pre_rings.values())
    print(f"[{label}] {len(doc)} páginas; círculos verdes={total_rings}; "
          f"páginas={sorted(pre_rings)[:12]}{'...' if len(pre_rings) > 12 else ''}",
          flush=True)
    if not total_rings:
        return
    if check_only:
        return

    n_forms = green_only_forms(doc)
    n_pages = 0
    touched = set()
    for pno in range(len(doc)):
        for xr in doc[pno].get_contents():
            if xr in touched:
                continue
            touched.add(xr)
            data = doc.xref_stream(xr)
            if not data:
                continue
            new, removed = strip_stream_green(data)
            if removed:
                doc.update_stream(xr, new, compress=False)
                n_pages += removed

    target = (out_dir or src.parent) / f"{label}_questoes.pdf"
    tmp = target.with_suffix(".tmp")
    doc.save(str(tmp), garbage=3, deflate=True)
    doc.close()

    if out_dir is None:
        bkp = Path(BKP) / src.name
        if not bkp.exists():
            bkp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, bkp)
    tmp.replace(target)

    doc = pymupdf.open(str(target))
    post_texts = [p.get_text() for p in doc]
    ok_text = post_texts == pre_texts
    post_rings = sum(drawing_rings(doc[p]) for p in range(len(doc)))
    problems = []
    for pno, (total_pre, rings_pre, _) in pre_pix.items():
        total_post, rings_post, _ = pixel_page(doc, pno - 1)
        if rings_post or total_post != total_pre - rings_pre:
            problems.append(pno)
    doc.close()
    status = "OK" if (ok_text and post_rings == 0 and not problems) else "FALHOU"
    print(f"  -> {status}: forms esvaziados={n_forms}, ops removidas={n_pages}, "
          f"círculos restantes={post_rings}, texto idêntico={ok_text}, "
          f"pixels residuais em {problems or 'nenhuma'} página "
          f"({target})", flush=True)
    if status == "FALHOU":
        sys.exit(f"[{label}] verificação falhou")

    if do_render:
        import subprocess

        subprocess.run(
            [sys.executable, "render_pages.py", label], check=False
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="*", default=LABELS)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()
    for label in args.labels:
        process_label(
            label.removesuffix("_questoes"),
            Path(args.out) if args.out else None,
            args.check,
            args.render,
        )


if __name__ == "__main__":
    main()