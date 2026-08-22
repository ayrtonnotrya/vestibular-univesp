"""App de estudo: responde questões (UNIVESP e FUVEST) com a página em pan/zoom.

Dois modos:
- **Explorar**: seleção manual por exame/questão (comportamento original).
- **Estudar** (adaptativo): FSRS agenda temas; seletor Rasch escolhe a questão;
  resposta atualiza FSRS, habilidade por área e a dificuldade empírica (b).

Roda no docker-compose:  docker compose up vestibular-app (porta 8501).
"""

import json
from pathlib import Path

import streamlit as st
from panzoom import question_page, view_page

from vestibular.estudo import motiva
from vestibular.estudo.db import connect

DATA = Path("/app/data")
JSON_DIR = DATA / "json"

LABELS = [
    *[f"fuvest_{ano}" for ano in range(2026, 2009, -1)],
    "univesp_2024",
    "univesp_2023",
    "univesp_2022",
    "univesp_2021",
    "univesp_2020",
    "univesp_2019_2",
    "univesp_2018_2s",
    "univesp_2018_1s",
    "univesp_2017_2s",
]

st.set_page_config(page_title="Estudo UNIVESP", page_icon="🎓", layout="wide")


@st.cache_data(show_spinner=False)
def load_questoes(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def load_imagens(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _page_info(label: str, numero: int, enunciado: str):
    """Devolve (pagina, bbox) da questão, usando bbox de figuras ou busca de texto."""
    ji = JSON_DIR / f"{label}_imagens.json"
    imagens = load_imagens(str(ji))["figuras_coordenadas"] if ji.exists() else {}
    figs = imagens.get(str(numero), [])
    bbox = (
        figs[0].get("bbox") if figs and isinstance(figs[0].get("bbox"), list) else None
    )
    pagina = figs[0]["pagina"] if bbox is not None else question_page(label, enunciado)
    return pagina, bbox


def _render_questao(q: dict, label: str, key_suffix: str, on_responder=None):
    """Renderiza questão (em cima) + página pan/zoom (embaixo)."""
    numero, enunciado = q["numero"], q["enunciado"]
    has_midia = bool(q.get("midia") or q.get("_midia"))
    midia = q.get("_midia", [])
    textos = q.get("_textos_de_apoio", [])
    st.caption(
        f"Questão {numero} · {q['tipo']}"
        + (f" · {len(midia)} figura(s)" if has_midia else " · sem mídia")
    )

    with st.container(border=True):
        st.markdown(f"**{enunciado}**")
        for apoio in textos:
            st.markdown(f"> {apoio}")
        alt = q.get("alternativas")
        if alt:
            key = f"resp_{key_suffix}"
            if key not in st.session_state:
                st.session_state[key] = None
            opcoes = {f"{k.upper()}) {v}": k for k, v in alt.items()}
            picked = st.radio(
                "Responda:", list(opcoes), index=None, key=f"radio_{key_suffix}"
            )
            if st.button("Responder", key=f"btn_{key_suffix}"):
                if picked is None:
                    st.warning("Escolha uma alternativa.")
                else:
                    st.session_state[key] = opcoes[picked]
            resp = st.session_state.get(key)
            if resp is not None:
                if on_responder is not None:
                    on_responder(numero, resp)
                else:
                    gab = q.get("gabarito")
                    st.success(
                        f"Você marcou {resp.upper()}."
                        + (f" Gabarito: {gab.upper()}." if gab else "")
                    )
        else:
            st.info(
                "Redação — dissertação."
                if q["tipo"] == "redacao"
                else "Sem alternativas."
            )

    pagina, bbox = _page_info(label, numero, enunciado)
    with st.container(border=True):
        st.subheader(f"📄 Página {pagina}")
        if bbox is not None:
            st.caption(
                "Enquadrado na figura · arraste para mover · roda ou 2 cliques para zoom · "
                "botões: página inteira / enquadrar questão"
            )
            view_page(label, pagina, bbox, height=680)
        else:
            st.caption(
                "Caso o texto/alternativas estejam com problema, confira a página original."
            )
            view_page(label, pagina, [0, 0, 1000, 1000], height=680)


def _questao_json(label: str, numero: int) -> dict | None:
    jq = JSON_DIR / f"{label}_questoes.json"
    if not jq.exists():
        return None
    questoes = load_questoes(str(jq))["questoes"]
    return next((x for x in questoes if x["numero"] == numero), None)


def modo_explorar():
    sidebar = st.sidebar
    label = sidebar.selectbox("Exame", LABELS)
    jq = JSON_DIR / f"{label}_questoes.json"
    if not jq.exists():
        st.error(f"Sem {jq}")
        return
    questoes = load_questoes(str(jq))["questoes"]

    qs = [q["numero"] for q in questoes]
    cur = sidebar.selectbox("Questão", qs, format_func=lambda n: f"Questão {n}")
    q = next((x for x in questoes if x["numero"] == cur), None)
    if q is None:
        st.warning("Questão não encontrada.")
        return
    q["_midia"] = q.get("midia", [])
    q["_textos_de_apoio"] = q.get("textos_de_apoio", [])
    with st.expander("Ver informações"):
        st.json(
            {
                "gabarito": q.get("gabarito"),
                "anulada": q.get("anulada"),
                "areas": q.get("areas"),
            }
        )
    _render_questao(q, label, f"explo_{label}_{cur}")


def modo_estudar():
    sidebar = st.sidebar
    usuario = sidebar.text_input("Usuário", value="eu") or "eu"
    if sidebar.button("▶ Próxima questão"):
        with connect() as con:
            q = motiva.proxima_questao(con, usuario)
        st.session_state["estudar_q"] = q
        st.session_state["estudar_aviso"] = None
        if q is None:
            st.session_state["estudar_aviso"] = (
                "Nada vencido para estudar. Responda antes de pedir outra."
            )

    aviso = st.session_state.get("estudar_aviso")
    if aviso:
        st.info(aviso)

    q = st.session_state.get("estudar_q")
    if q is None:
        st.write("Clique em **Próxima questão** para começar a sessão adaptativa.")
    else:
        if q.get("nivel_base") == "tema":
            st.caption(
                f"Tema: **{q['tema_nome']}** · nível por tema {round(q['nivel_tema'], 2)} "
                f"({q['nivel_contagem']} tentativas)"
            )
        else:
            st.caption(
                f"Tema: **{q['tema_nome']}** · θ da área {round(q.get('theta', 0.0), 2)}"
            )
        extra = _questao_json(q["exame_label"], q["numero"]) or {}
        full = {
            "numero": q["numero"],
            "tipo": "objetiva",
            "enunciado": q["enunciado"],
            "alternativas": q["alternativas"],
            "gabarito": q["gabarito"],
            "midia": extra.get("midia", []),
            "_midia": extra.get("midia", []),
            "_textos_de_apoio": extra.get("textos_de_apoio", []),
        }

        def on_responder(numero, resp):
            with connect() as con:
                r = motiva.responder(con, usuario, q["questao_id"], resp)
            fb = {
                None: "⚠️ Anulada/sem gabarito oficial",
                True: "✅ Correta!",
                False: "❌ Errada.",
            }[r["correta"]]
            st.session_state["estudar_fb"] = (fb, r)
            for t in r["temas"]:
                st.session_state.setdefault("estudar_fsrs", []).append(
                    f"{q['tema_nome']} → vencimento {t['vencimento'].isoformat()} ({t['estado']})"
                )

        _render_questao(
            full,
            q["exame_label"],
            f"estudar_{q['questao_id']}",
            on_responder=on_responder,
        )

        fb = st.session_state.get("estudar_fb")
        if fb:
            texto, r = fb
            st.markdown(f"### {texto}")
            st.caption(f"Gabarito oficial: {r['gabarito']}" if r["gabarito"] else "")
        with st.expander("Próximos vencimentos"):
            for linha in st.session_state.get("estudar_fsrs", [])[-20:]:
                st.write(linha)

    with st.expander("📊 Progresso por área"):
        with connect() as con:
            rows = motiva.progresso(con, usuario)
        if not rows:
            st.write("Sem dados ainda. Responda algumas questões.")
            return
        dados = [
            {
                "Área": r["area"],
                "θ (habilidade)": round(r["theta"], 2) if r["n_obs"] else None,
                "n_obs": r["n_obs"] or 0,
                "Temas vencidos": r["temas_vencidos"] or 0,
            }
            for r in rows
        ]
        st.dataframe(dados, hide_index=True, use_container_width=True)
        with connect() as con:
            nts = motiva.niveis_por_tema(con, usuario)
        if nts:
            st.markdown("#### Nível por tema")
            dados_temas = [
                {
                    "Área": r["area"],
                    "Tema": r["tema"],
                    "Score": round(r["score"], 2),
                    "Racha": r["racha"],
                    "Tentativas": r["contagem"],
                }
                for r in nts
            ]
            st.dataframe(dados_temas, hide_index=True, use_container_width=True)


def main():
    st.title("🎓 Estudo Vestibular")
    modo = st.sidebar.radio("Modo", ["Estudar", "Explorar"])
    if modo == "Estudar":
        modo_estudar()
    else:
        modo_explorar()


if __name__ == "__main__":
    main()
