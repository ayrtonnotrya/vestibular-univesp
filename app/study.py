"""App de estudo: responde questões UNIVESP com a página em pan/zoom.

Para cada questão que tem figura, mostra a página (renderizada na hora a partir
do PDF via PyMuPDF) num viewer pan/zoom (arrastar + zoom in/out) já enquadrada
no bbox da figura, ao lado do enunciado e alternativas.

Roda no docker-compose:  docker compose up vestibular-app (porta 8501).
"""
import json
from pathlib import Path

import streamlit as st

from panzoom import question_page, view_page

DATA = Path("/app/data")
JSON_DIR = DATA / "json"

LABELS = ["univesp_2024", "univesp_2023", "univesp_2022", "univesp_2021",
          "univesp_2020", "univesp_2019_2", "univesp_2018_2s", "univesp_2018_1s",
          "univesp_2017_2s"]

st.set_page_config(page_title="Estudo UNIVESP", page_icon="🎓", layout="wide")


@st.cache_data(show_spinner=False)
def load_questoes(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def load_imagens(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    st.title("🎓 Estudo UNIVESP")

    sidebar = st.sidebar
    label = sidebar.selectbox("Exame", LABELS)
    jq = JSON_DIR / f"{label}_questoes.json"
    ji = JSON_DIR / f"{label}_imagens.json"
    if not jq.exists():
        st.error(f"Sem {jq}")
        return
    questoes = load_questoes(str(jq))["questoes"]
    imagens = load_imagens(str(ji))["figuras_coordenadas"] if ji.exists() else {}

    qs = [q["numero"] for q in questoes]
    cur = sidebar.selectbox("Questão", qs,
                            format_func=lambda n: f"Questão {n}")

    q = next((x for x in questoes if x["numero"] == cur), None)
    if q is None:
        st.warning("Questão não encontrada.")
        return

    has_midia = bool(q.get("midia"))
    figs = imagens.get(str(cur), [])
    bbox = figs[0].get("bbox") if figs and isinstance(figs[0].get("bbox"), list) else None
    page_num = figs[0]["pagina"] if bbox is not None else question_page(label, q["enunciado"])

    st.caption(f"Questão {q['numero']} · {q['tipo']}"
               + (f" · {len(figs)} figura(s)" if has_midia else " · sem mídia"))

    # ===================== QUESTÃO (em cima) =====================
    with st.container(border=True):
        st.markdown(f"**{q['enunciado']}**")
        for apoio in q.get("textos_de_apoio", []):
            st.markdown(f"> {apoio}")

        alt = q.get("alternativas")
        if alt:
            if "resp" not in st.session_state:
                st.session_state["resp"] = None
            opcoes = {f"{k.upper()}) {v}": k for k, v in alt.items()}
            picked = st.radio("Responda:", list(opcoes), index=None)
            if st.button("Responder"):
                if picked is None:
                    st.warning("Escolha uma alternativa.")
                else:
                    st.session_state["resp"] = opcoes[picked]
            resp = st.session_state.get("resp")
            if resp:
                gab = q.get("gabarito")
                st.success(f"Você marcou {resp.upper()}." + (f" Gabarito: {gab.upper()}." if gab else ""))
        elif q["tipo"] == "redacao":
            st.info("Redação — dissertação.")
        else:
            st.info("Sem alternativas.")

        with st.expander("Ver informações"):
            st.json({"gabarito": q.get("gabarito"), "anulada": q.get("anulada"),
                     "areas": q.get("areas")})

    # ===================== PÁGINA (embaixo) =====================
    with st.container(border=True):
        st.subheader(f"📄 Página {page_num}")
        if bbox is not None:
            st.caption("Enquadrado na figura · arraste para mover · roda ou 2 cliques para zoom · "
                       "botões: página inteira / enquadrar questão")
            view_page(label, page_num, bbox, height=680)
        else:
            st.caption("Caso o texto/alternativas estejam com problema, confira a página original.")
            view_page(label, page_num, [0, 0, 1000, 1000], height=680)


if __name__ == "__main__":
    main()
