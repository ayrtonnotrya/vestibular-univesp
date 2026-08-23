"""Viewer pan/zoom de página de prova para o app de estudo.

A página vem de um JPEG pré-renderizado (data/paginas/<label>/p<NNN>.jpg,
gerado por tools/gemini/render_pages.py); se faltar, cai para renderização
na hora a partir do PDF (PyMuPDF). Exibida num <div> HTML com transform CSS,
permitindo:
- arrastar (pan);
- zoom in/out pela roda do mouse, double-click e botões + / -;
- "ver página inteira" (reset) e voltar ao enquadramento da questão (bbox).

Caminhos: /app/data/paginas/<label>/p<NNN>.jpg e /app/data/<label>_questoes.pdf
(montados pelo docker-compose).
"""
import base64
import re
from pathlib import Path

import pymupdf
import streamlit as st

DATA_DIR = Path("/app/data")
PAGES_DIR = DATA_DIR / "paginas"
MAX_SIDE = 1400
DPI = 200


def _norm(s):
    s = s.lower()
    s = "".join(c for c in s if c.isalnum() or c == " ")
    return re.sub(r"\s+", " ", s)


@st.cache_data(show_spinner=False)
def _page_texts(label: str):
    """Textos normalizados das páginas do exame (uma vez por exame)."""
    pdf = DATA_DIR / f"{label}_questoes.pdf"
    if not pdf.exists():
        return []
    doc = pymupdf.open(str(pdf))
    texts = [_norm(p.get_text()) for p in doc]
    doc.close()
    return texts


@st.cache_data(show_spinner=False)
def total_pages(label: str) -> int:
    pdf = DATA_DIR / f"{label}_questoes.pdf"
    if not pdf.exists():
        return 0
    doc = pymupdf.open(str(pdf))
    n = doc.page_count
    doc.close()
    return n


_JS = r"""
<script>
(function() {
  var naturalW = %d, naturalH = %d;
  var bbox = %s; // [y0,x0,y1,x1] em fração 0..1

  var container = document.getElementById("pz");
  var img = document.getElementById("pz-img");
  var scale = 1, tx = 0, ty = 0, baseScale = 1;

  function vp(){ return {w: container.clientWidth, h: container.clientHeight}; }
  function apply(){ img.style.transform = "translate("+tx+"px,"+ty+"px) scale("+(baseScale*scale)+")"; }

  function focusBBox(){
    var b = vp();
    var x0=bbox[1]*naturalW, y0=bbox[0]*naturalH, x1=bbox[3]*naturalW, y1=bbox[2]*naturalH;
    var bw=Math.max(1,x1-x0), bh=Math.max(1,y1-y0);
    var m=1.18;
    var s=Math.min(b.w/(bw*m), b.h/(bh*m));
    baseScale=s; scale=1;
    var cx=(x0+x1)/2, cy=(y0+y1)/2;
    tx=b.w/2-cx*s; ty=b.h/2-cy*s;
    apply();
  }
  function fitPage(){
    var b=vp();
    var s=Math.min(b.w/naturalW, b.h/naturalH);
    baseScale=s; scale=1;
    tx=(b.w-naturalW*s)/2; ty=(b.h-naturalH*s)/2;
    apply();
  }
  function zoomAt(cx,cy,f){
    var ns=Math.max(0.05,Math.min(40,scale*f));
    var r=ns/scale;
    tx=cx-(cx-tx)*r; ty=cy-(cy-ty)*r; scale=ns; apply();
  }
  function px(e){ var r=container.getBoundingClientRect(); return {x:e.clientX-r.left, y:e.clientY-r.top}; }

  // ---- drag (mouse no window, nao depende de pointer capture) ----
  var down=false, sx=0, sy=0, stx=0, sty=0;

  container.addEventListener("mousedown", function(e){
    down=true;
    sx=e.clientX; sy=e.clientY; stx=tx; sty=ty;
    container.style.cursor="grabbing";
    e.preventDefault();
  });
  window.addEventListener("mousemove", function(e){
    if(!down) return;
    tx=stx+(e.clientX-sx); ty=sty+(e.clientY-sy); apply();
  });
  window.addEventListener("mouseup", function(e){
    if(!down) return;
    down=false; container.style.cursor="grab";
  });
  container.addEventListener("mouseleave", function(){
    if(down){ /* mantem o arrasto mesmo fora do quadro */ }
  });

  container.addEventListener("wheel", function(e){
    e.preventDefault();
    var p=px(e); zoomAt(p.x,p.y, e.deltaY<0?1.15:0.87);
  }, {passive:false});
  container.addEventListener("dblclick", function(e){
    var p=px(e); zoomAt(p.x,p.y,1.6);
  });

  window.__pz = {
    focus: focusBBox, fit: fitPage,
    zoomIn: function(){ var b=vp(); zoomAt(b.w/2,b.h/2,1.4); },
    zoomOut: function(){ var b=vp(); zoomAt(b.w/2,b.h/2,1/1.4); }
  };
  container.style.cursor="grab";
  focusBBox();
})();
</script>
"""


@st.cache_data(show_spinner=False)
def question_page(label: str, enunciado: str) -> int:
    """Página (1-indexed) em que está a questão de texto `enunciado`.

    Estratégia: localiza o início do enunciado no texto das páginas (normalizado),
    testando trechos progressivamente menores (para enunciados longos em que o
    início possa variar). Funciona para qualquer exame, independente do layout.
    """
    texts = _page_texts(label)
    if not texts:
        return 1
    needle = _norm(enunciado.strip()[:40])
    if not needle:
        return 1
    for pag, t in enumerate(texts, 1):
        if needle in t:
            return pag
    # fallback: trecho menor (20 chars) sem o início variável
    for pag, t in enumerate(texts, 1):
        if _norm(enunciado.strip()[:20]) in t:
            return pag
    # fallback: busca por qualquer trecho das alternativas/textos de apoio
    for pag, t in enumerate(texts, 1):
        for part in _norm(enunciado).split():
            if len(part) >= 8 and part in t:
                return pag
    return 1


@st.cache_data(show_spinner=False)
def _page_jpeg(label: str, page_num: int):
    """Lê a página pré-renderizada (JPEG) em base64 + dims; None se ausente."""
    path = PAGES_DIR / label / f"p{int(page_num):03d}.jpg"
    if not path.exists():
        return None
    pix = pymupdf.Pixmap(str(path))
    w, h = pix.width, pix.height
    b64 = base64.b64encode(path.read_bytes()).decode()
    return b64, w, h


@st.cache_data(show_spinner=False)
def _render_pdf(label: str, page_num: int):
    """Fallback: renderiza a página `page_num` do PDF em PNG (base64) + dims."""
    pdf = DATA_DIR / f"{label}_questoes.pdf"
    if not pdf.exists():
        return None
    doc = pymupdf.open(str(pdf))
    page = doc[int(page_num) - 1]
    rect = page.rect
    scale = min(MAX_SIDE / rect.width, MAX_SIDE / rect.height, DPI / 72)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
    b64 = base64.b64encode(pix.tobytes("png")).decode()
    w, h = pix.width, pix.height
    doc.close()
    return b64, w, h


def _page_image(label: str, page_num: int):
    return _page_jpeg(label, page_num) or _render_pdf(label, page_num)


def panzoom_component(label: str, page_num: int, bbox_percent, height=720):
    rendered = _page_image(label, page_num)
    if rendered is None:
        st.error(f"Página não encontrada: {PAGES_DIR / (label + f'/p{page_num:03d}.jpg')} "
                 f"nem o PDF {DATA_DIR / (label + '_questoes.pdf')}")
        return
    b64, naturalW, naturalH = rendered
    src = "data:image/png;base64," + b64
    bbox = [b / 1000 for b in bbox_percent]

    bstyle = ("padding:2px 10px;border:1px solid #ccc;background:#fff;border-radius:6px;"
              "cursor:pointer;font-size:14px")
    html = """<div style="font-family:sans-serif">
  <div style="display:flex;gap:6px;margin-bottom:6px;align-items:center;flex-wrap:wrap">
    <button onclick="__pz.zoomOut()" style="%(b)s">&#8722;</button>
    <button onclick="__pz.zoomIn()" style="%(b)s">&#43;</button>
    <button onclick="__pz.fit()" style="%(b)s">Página inteira</button>
    <button onclick="__pz.focus()" style="%(b)s">Enquadrar questão</button>
    <small style="color:#666">arraste p/ mover &middot; roda ou 2 cliques p/ zoom</small>
  </div>
  <div id="pz" style="position:relative;width:100%%;height:%(h)dpx;overflow:hidden;
       border:1px solid #ddd;background:#8a8a8a;touch-action:none;user-select:none;-webkit-user-select:none">
    <img id="pz-img" src="%(src)s" draggable="false"
         style="position:absolute;top:0;left:0;transform-origin:0 0;max-width:none;will-change:transform;
                user-drag:none;-webkit-user-drag:none;pointer-events:none" />
  </div>
</div>""" % {"b": bstyle, "h": height, "src": src}
    html += _JS % (naturalW, naturalH, bbox)
    st.iframe(html, height=height + 60, width="stretch")


def view_page(label: str, page_num: int, bbox_percent, height=720):
    panzoom_component(label, page_num, bbox_percent, height)
