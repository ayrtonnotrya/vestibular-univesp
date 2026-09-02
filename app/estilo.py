"""Tema visual estilo QConcursos (paleta roxa) adaptável ao modo claro/escuro.

As cores são derivadas do tema ativo do Streamlit (``theme.base`` — "light"
ou "dark") via ``st.get_option``, com a paleta roxa como acento principal.
Os elementos customizados (topbar, badges, cartões de opção, banda de
feedback) usam essas cores para manter legibilidade nos dois modos.
"""

import html

import streamlit as st


def _paleta():
    """Paleta ajustada ao tema ativo (light/dark) do Streamlit."""
    base = (st.get_option("theme.base") or "").lower()
    if base not in ("light", "dark"):
        try:
            base = str(getattr(st.context.theme, "type", "") or "").lower()
        except Exception:  # noqa: BLE001 — contexto ausente durante build
            base = ""
    dark = base == "dark"

    def get(k):
        return st.get_option(f"theme.{k}")

    primaria = get("primaryColor") or ("#8E5BF5" if dark else "#6C2BD9")
    bg = get("backgroundColor")  # light: #FFFFFF | dark: #0E1117
    bg2 = get("secondaryBackgroundColor")  # light: #F0F2F6 | dark: #262730
    texto = get("textColor") or ("#E8E6EF" if dark else "#2E2637")

    cartao = bg2 or ("#262730" if dark else "#FFFFFF")
    if not dark:
        cartao = "#FFFFFF"
    fundo = bg or ("#0E1117" if dark else "#F3F1F8")
    borda = "#4A4458" if dark else "#DFD5F2"
    fundo_prim = "#2F2A3D" if dark else "#F3EDFB"
    texto_suave = "#A9A4B8" if dark else "#6B6478"
    return {
        "primaria": primaria,
        "primaria_hover": "#5521B5",
        "primaria_clara": "#8E5BF5",
        "fundo_prim": fundo_prim,
        "borda": borda,
        "texto": texto,
        "texto_suave": texto_suave,
        "cinza": "#7A8494",
        "fundo": fundo,
        "certa": "#38C896" if dark else "#1E9E6E",
        "certa_bg": "#14402F" if dark else "#E4F8F0",
        "errada": "#EF8A98" if dark else "#D9334A",
        "errada_bg": "#40232B" if dark else "#FDE9EC",
        "anulada": "#E3B94A" if dark else "#B98A00",
        "anulada_bg": "#3A2F14" if dark else "#FBF3D9",
        "branco": cartao,
    }


def _css() -> str:
    P = _paleta()
    return f"""
<style>
/* ---- base ---- */
html, body, [class*="css" i], .stApp {{
  font-family: "Open Sans", "Inter", "Segoe UI", Arial, sans-serif;
}}
.stApp {{ background: {P['fundo']}; }}
footer {{ visibility: hidden; height: 0; }}
#MainMenu, [data-testid="stAppDeployButton"], [data-testid="stStatusWidget"] {{
  visibility: hidden !important;
}}
[data-testid="stHeader"] {{ background: transparent !important; }}
[data-testid="stMainBlockContainer"] {{
  background: transparent;
  border-radius: 16px;
  padding: 4px 24px 40px;
  max-width: 1000px; margin: 0 auto;
}}

/* ---- topbar ---- */
.q-topbar {{
  display: flex; align-items: center; gap: 12px;
  background: linear-gradient(96deg, {P['primaria']} 0%, {P['primaria_clara']} 100%);
  border-radius: 14px; padding: 16px 22px; color: #fff;
  box-shadow: 0 3px 14px rgba(40,20,80,.22);
  margin-bottom: 18px;
}}
.q-topbar .q-logo {{
  width: 40px; height: 40px; border-radius: 12px;
  background: rgba(255,255,255,.2); display: flex; align-items: center;
  justify-content: center; font-weight: 800; font-size: 20px;
}}
.q-topbar h1 {{ margin: 0; font-size: 17px; font-weight: 700; letter-spacing: .2px; }}
.q-topbar .q-sub {{
  font-size: 11.5px; opacity: .9; font-weight: 400; margin-left: auto;
  background: rgba(255,255,255,.16); padding: 4px 12px; border-radius: 999px;
}}

/* ---- header da questão ---- */
.q-question-header {{
  display: flex; align-items: stretch;
  background: {P['fundo_prim']}; border: 1px solid {P['borda']};
  min-height: 42px; border-radius: 10px; margin: 14px 0 10px;
}}
.q-qid {{
  display: flex; flex-direction: column; justify-content: center;
  background: {P['primaria']}; color: #fff;
  font-weight: 700; font-size: 13px; letter-spacing: .4px;
  padding: 0 16px; white-space: nowrap; text-align: center;
}}
.q-qid small {{ font-size: 9px; font-weight: 400; opacity: .85; }}
.q-breadcrumb {{
  display: flex; align-items: center; flex-wrap: wrap; gap: 0 2px;
  padding: 8px 16px; font-size: 12px; color: {P['texto_suave']};
}}
.q-breadcrumb .clink {{ color: {P['texto_suave']}; }}
.q-breadcrumb .q-sep {{ color: {P['cinza']}; margin: 0 8px; font-size: 11px; }}

.q-question-info {{
  display: flex; flex-wrap: wrap; gap: 6px 22px;
  padding: 10px 16px; border: 1px solid {P['borda']};
  border-radius: 10px; margin-bottom: 12px;
  font-size: 12px; color: {P['texto_suave']}; background: {P['branco']};
}}
.q-question-info strong {{ color: {P['texto']}; font-weight: 600; }}
.q-enun {{
  font-size: 14.5px; color: {P['texto']}; line-height: 1.7;
  background: {P['branco']};
  border: 1px solid {P['borda']}; border-radius: 10px;
  padding: 14px 16px; margin-bottom: 12px;
}}
.q-leituras {{ color: {P['texto_suave']}; }}
.q-enun > p {{ margin: 8px 0 0; font-weight: 600; }}

/* ---- opções do rádio transformadas em fileiras estilo QConcursos ---- */
div[data-testid="stRadio"] div[data-testid="stRadioGroup"] {{
  display: flex; flex-direction: column; gap: 6px; width: 100%;
}}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"] {{
  display: flex !important; align-items: flex-start; gap: 12px;
  width: 100%; margin: 0 !important; padding: 10px 14px !important;
  border: 1px solid {P['borda']}; border-radius: 10px;
  background: {P['branco']}; cursor: pointer; box-sizing: border-box;
  transition: box-shadow .12s ease, border-color .12s ease, background .12s ease;
}}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]:hover {{
  border-color: {P['primaria']}; box-shadow: 0 2px 8px rgba(108,43,217,.14);
  background: {P['fundo_prim']};
}}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"][data-selected="true"],
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"][data-selected] {{
  border-color: {P['primaria']}; background: {P['fundo_prim']};
}}
/* esconde o círculo nativo do rádio (a 1ª div dentro da fileira) */
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"] > div > div > div:first-child {{
  display: none !important;
}}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"] > div {{
  flex: 1 1 auto; font-size: 13.5px; color: {P['texto']};
}}
/* selo circular com a letra (o rádio renderiza as opções em ordem) */
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]::before {{
  content: "A";
  flex: 0 0 28px; width: 28px; height: 28px; margin-top: 1px;
  border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 13px; color: {P['primaria']};
  background: {P['branco']}; border: 1.5px solid {P['borda']};
  box-sizing: border-box; line-height: 1; flex-shrink: 0;
}}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]:nth-of-type(2)::before {{ content: "B"; }}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]:nth-of-type(3)::before {{ content: "C"; }}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]:nth-of-type(4)::before {{ content: "D"; }}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]:nth-of-type(5)::before {{ content: "E"; }}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"]:nth-of-type(6)::before {{ content: "F"; }}
[data-testid="stRadioGroup"] > label[data-testid="stRadioOption"][data-selected]::before {{
  background: {P['primaria']}; border-color: {P['primaria']}; color: #fff;
}}
label[data-testid="stRadioOption"] div[data-testid="stMarkdownContainer"] {{
  flex: 1 1 auto; color: {P['texto']};
}}

/* ---- pills (certeza/causa) no conteúdo principal ---- */
[data-testid="stMainBlockContainer"] div[data-testid="stButtonGroup"] {{
  gap: 6px !important; flex-wrap: wrap;
}}
[data-testid="stMainBlockContainer"] div[data-testid="stButtonGroup"] > div {{
  border: 1px solid {P['borda']} !important; border-radius: 999px !important;
  color: {P['primaria']}; font-size: 12px; font-weight: 600; padding: 4px 14px;
}}
[data-testid="stMainBlockContainer"] div[data-testid="stButtonGroup"] > div[class*="selected"],
[data-testid="stMainBlockContainer"] div[data-testid="stButtonGroup"] > div[aria-checked="true"],
[data-testid="stMainBlockContainer"] div[data-testid="stButtonGroup"] > div[data-selected] {{
  background: {P['primaria']} !important; border-color: {P['primaria']} !important;
  color: #fff !important;
}}

/* ---- Modo (sidebar): menu alinhado à esquerda, sem cercar os botões ---- */
[data-testid="stSidebar"] div[data-testid="stButtonGroup"] {{
  display: flex; flex-direction: column; gap: 4px !important; flex-wrap: nowrap;
}}
[data-testid="stSidebar"] div[data-testid="stButtonGroup"] > div {{
  width: 100%; text-align: left; justify-content: flex-start !important;
  border: 1px solid {P['borda']} !important; border-radius: 8px !important;
  color: {P['texto']}; font-size: 13px; font-weight: 500; padding: 7px 14px;
  background: {P['branco']} !important;
}}
[data-testid="stSidebar"] div[data-testid="stButtonGroup"] > div[class*="selected"],
[data-testid="stSidebar"] div[data-testid="stButtonGroup"] > div[aria-checked="true"],
[data-testid="stSidebar"] div[data-testid="stButtonGroup"] > div[data-selected] {{
  background: {P['fundo_prim']} !important;
  border-color: {P['primaria']} !important;
  color: {P['primaria']} !important; font-weight: 700;
  box-shadow: inset 3px 0 0 {P['primaria']};
}}

/* ---- banda de feedback ---- */
.q-band {{
  display: flex; align-items: center; gap: 10px;
  border-radius: 10px; padding: 13px 16px; margin: 14px 0 4px;
  font-size: 13.5px; font-weight: 600; line-height: 1.45;
}}
.q-band .q-gab {{ font-weight: 400; opacity: .95; }}
.q-band.ok   {{ background: {P['certa_bg']};   color: {P['certa']}; }}
.q-band.no   {{ background: {P['errada_bg']};  color: {P['errada']}; }}
.q-band.nula {{ background: {P['anulada_bg']}; color: {P['anulada']}; }}

/* ---- botões ---- */
[data-testid="stBaseButton-primary"] {{
  background: {P['primaria']} !important; border: none !important;
  box-shadow: 0 2px 6px rgba(108,43,217,.25) !important;
}}
[data-testid="stBaseButton-primary"]:hover {{
  background: {P['primaria_hover']} !important;
}}
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-tertiary"] {{
  border: 1px solid {P['borda']} !important; color: {P['primaria']} !important;
  background: {P['branco']} !important;
}}

/* ---- sidebar ---- */
[data-testid="stSidebar"] {{ background: {P['fundo']} !important; border-right: 1px solid {P['borda']}; }}
[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] {{
  color: {P['primaria']}; font-weight: 700; font-size: 12px;
  text-transform: uppercase; letter-spacing: .4px;
}}

/* ---- headers / métricas / expansores ---- */
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {{ color: {P['texto']}; }}
[data-testid="stExpander"] details {{ border: 1px solid {P['borda']} !important; border-radius: 12px !important; background: {P['branco']}; }}
[data-testid="stMetric"] {{
  background: {P['branco']}; border: 1px solid {P['borda']}; border-radius: 12px; padding: 8px 14px;
}}
[data-testid="stDataFrame"] {{ border: 1px solid {P['borda']}; border-radius: 12px; overflow: hidden; }}
</style>
"""


def paleta():
    """Paleta atual (chave pública) — usada para estilizar o viewer de página."""
    return _paleta()


def injetar():
    """Aplica o tema adaptado ao modo light/dark; chamar no início do app."""
    st.markdown(_css(), unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False).replace("\n", "<br>")


def breadcrumb_html(partes: list[str]) -> str:
    partes_limpas = [p for p in partes if p]
    itens = []
    for i, p in enumerate(partes_limpas):
        if i:
            itens.append('<span class="q-sep">›</span>')
        itens.append(f'<span class="clink">{esc(p)}</span>')
    return "".join(itens)


def header_html(numero: int | str, breadcrumb: list[str]) -> str:
    return (
        '<div class="q-question-header">'
        f'<div class="q-qid">Q{esc(numero)}<small>vestibulares</small></div>'
        f'<div class="q-breadcrumb">{breadcrumb_html(breadcrumb)}</div>'
        "</div>"
    )


def info_html(itens: list[tuple[str, str]]) -> str:
    spans = "".join(
        f"<span><strong>{esc(k)}:</strong> {esc(v)}</span>" for k, v in itens
    )
    return f'<div class="q-question-info">{spans}</div>'


def topbar_html(titulo: str, sub: str) -> str:
    return (
        '<div class="q-topbar">'
        '<div class="q-logo">Q</div>'
        f"<h1>{esc(titulo)}</h1>"
        f'<span class="q-sub">{esc(sub)}</span>'
        "</div>"
    )


def banda_html(correta: bool | None, gabarito: str | None) -> str:
    if correta is None:
        cls, msg, adicional = "nula", "Questão anulada / sem gabarito oficial.", ""
    elif correta:
        cls, msg = "ok", "Parabéns! Você acertou!"
        adicional = (
            f' · <span class="q-gab">Gabarito oficial: <b>{esc(gabarito.upper())}</b></span>'
            if gabarito
            else ""
        )
    else:
        cls, msg = "no", "Incorreta."
        adicional = (
            f' <span class="q-gab">Gabarito oficial: <b>{esc(gabarito.upper())}</b></span>'
            if gabarito
            else ""
        )
    return f'<div class="q-band {cls}"><span>{msg}</span>{adicional}</div>'