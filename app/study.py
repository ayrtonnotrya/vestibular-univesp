"""App de estudo: responde questões (UNIVESP e FUVEST) com a página em pan/zoom.

Dois modos:
- **Explorar**: seleção manual por exame/questão (comportamento original).
- **Estudar** (adaptativo): FSRS agenda temas; seletor Rasch escolhe a questão;
  resposta atualiza FSRS, habilidade por área e a dificuldade empírica (b).

Roda no docker-compose:  docker compose up vestibular-app (porta 8501).
"""

import json
import re
from pathlib import Path

import estatisticas
import pandas as pd
import streamlit as st
from panzoom import view_page

from vestibular.estudo import motiva
from vestibular.estudo.db import connect

DATA = Path("/app/data")
JSON_DIR = DATA / "json"
PAGES_DIR = DATA / "paginas"

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


def _midia_pagina(q: dict) -> int | None:
    """Página informada pelo modelo nas descrições de mídia ("Página N: ...")."""
    for m in q.get("midia") or []:
        mh = re.search(r"[Pp]ágina\s*(\d+)", m)
        if mh:
            return int(mh.group(1))
    return None


def _interp_pagina(numero: int, imagens: dict) -> int | None:
    """Interpola a página da questão a partir das páginas conhecidas (figuras),
    preservando a ordem crescente das questões."""
    known = sorted(
        (int(str(n)), recs[0]["pagina"])
        for n, recs in imagens.items()
        if recs and recs[0].get("pagina") and recs[0]["pagina"] > 1
    )
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


def _page_info(label: str, numero: int):
    """Devolve (pagina, bbox) da questão a partir dos JSONs (sem PDF).

    Ordem das fontes: campo `pagina` gravado no JSON da questão → página da
    figura quando há `bbox` → "Página N:" na descrição de mídia → interpolação
    pelas páginas conhecidas do exame.
    """
    ji = JSON_DIR / f"{label}_imagens.json"
    imagens = load_imagens(str(ji))["figuras_coordenadas"] if ji.exists() else {}
    q = _questao_json(label, numero) or {}
    figs = imagens.get(str(numero), [])
    bbox = (
        figs[0].get("bbox") if figs and isinstance(figs[0].get("bbox"), list) else None
    )
    bboxq = q.get("bbox_questao")
    if isinstance(bboxq, list) and len(bboxq) == 4:
        bbox = bboxq
    pagina = q.get("pagina")
    if not isinstance(pagina, int) or pagina <= 1:
        pagina = figs[0]["pagina"] if bbox is not None else None
    if not isinstance(pagina, int) or pagina <= 1:
        pagina = _midia_pagina(q) or _interp_pagina(numero, imagens) or 1
    return pagina, bbox


def _nome_vestibular(label: str) -> str:
    """Nome legível do vestibular a partir do label (fuvest_2024, univesp_2019_2s)."""
    partes = label.split("_")
    vest = partes[0]
    ano = sem = ""
    for p in partes[1:]:
        if p.isdigit():
            ano = p
        elif p.endswith("s"):
            sem = p
    nome = {
        "fuvest": "FUVEST",
        "univesp": "UNIVESP",
        "unesp": "UNESP",
        "unicamp": "UNICAMP",
    }.get(vest, vest.upper())
    return nome + (f" {ano}" if ano else "") + (f"/{sem}" if sem else "")


@st.cache_data(show_spinner=False)
def _max_pagina(label: str) -> int:
    """Maior número de página renderizada (JPEG) disponível para o exame."""
    d = PAGES_DIR / label
    if not d.exists():
        return 1
    return max((int(p.stem[1:]) for p in d.glob("p*.jpg")), default=1)


def _dados_temas(questao_id: int | None, usuario: str) -> list[dict]:
    """Todos os temas da questão com θ da área e score/nível por tema do usuário."""
    if not questao_id:
        return []
    with connect() as con:
        rows = con.execute(
            """SELECT c.area_id, a.nome AS area, t.id AS tema_id, t.nome AS tema
               FROM classificacoes c
               JOIN temas t ON t.id = c.tema_id
               JOIN areas a ON a.id = c.area_id
               WHERE c.questao_id = ?
               ORDER BY a.nome, t.nome""",
            (questao_id,),
        ).fetchall()
        out = []
        for r in rows:
            nu = con.execute(
                "SELECT score, contagem FROM niveis_usuarios WHERE usuario=? AND tema_id=?",
                (usuario, r["tema_id"]),
            ).fetchone()
            hab = con.execute(
                "SELECT theta, n_obs FROM habilidades WHERE usuario=? AND area_id=?",
                (usuario, r["area_id"]),
            ).fetchone()
            out.append(
                {
                    "area": r["area"],
                    "tema": r["tema"],
                    "score": nu["score"] if nu else None,
                    "contagem": nu["contagem"] if nu else 0,
                    "theta": hab["theta"] if hab else None,
                    "n_obs": hab["n_obs"] if hab else 0,
                }
            )
    return out


def _param(chave: str) -> str | None:
    """Valor (string única) de um query param, ou None."""
    v = st.query_params.get(chave)
    return v if isinstance(v, str) and v else None


def _sync_params(**params):
    """Persiste os valores na URL em uma única atualização, só se mudou."""
    alvo = {k: (str(v) if v is not None else "") for k, v in params.items()}
    diff = {k: v for k, v in alvo.items() if st.query_params.get(k) != v}
    if diff:
        st.query_params.update(diff)


def _fb_encode(correta, gabarito) -> str:
    marc = {None: "a", True: "c", False: "e"}.get(correta, "a")
    return f"{marc}:{gabarito or ''}"


def _restaurar_estudar(usuario: str):
    """Restaura a questão em aberto (modo Estudar) a partir de `?qid=`."""
    qid = _param("qid")
    if not qid:
        return
    try:
        qid = int(qid)
    except ValueError:
        return
    with connect() as con:
        resto = motiva.questao_por_id(con, usuario, qid)
    if resto:
        st.session_state["estudar_q"] = resto
        st.session_state["params_qid"] = str(qid)


def _restaurar_fb():
    """Restaura o feedback da última resposta (modo Estudar) a partir de `?fb=`."""
    raw = _param("fb") or ""
    st.session_state["params_fb"] = raw
    if ":" not in raw:
        return
    marc, gab = raw.split(":", 1)
    texto = {
        "a": "⚠️ Anulada/sem gabarito oficial",
        "c": "✅ Correta!",
        "e": "❌ Errada.",
    }.get(marc)
    if texto:
        st.session_state["estudar_fb"] = (texto, {"gabarito": gab})


def _restaurar_do_url():
    """Na primeira execução após um load (ex.: refresh no celular), restaura os
    widgets a partir da URL: modo, usuário e a questão em aberto."""
    if st.session_state.get("_carregado"):
        return
    st.session_state.setdefault("usuario", _param("usuario") or "eu")
    modo = _param("modo") or "Estudar"
    if modo not in ("Estudar", "Explorar", "Estatísticas"):
        modo = "Estudar"
    st.session_state["modo"] = modo
    if modo == "Estudar":
        _restaurar_estudar("eu")
        _restaurar_fb()
    elif modo == "Explorar":
        if _param("label") in LABELS:
            st.session_state["explo_label"] = _param("label")
        try:
            st.session_state["explo_cur"] = int(_param("numero"))
        except (TypeError, ValueError):
            pass
    st.session_state["_carregado"] = True


_OBS_PREFIXOS = (
    "note e adote",
    "nota e adote",
    "obs.:",
    "obs:",
    "observação",
    "glossário",
    "dados:",
    "dado:",
)


def _separar_observacoes(apoios: list[str]) -> tuple[list[str], list[str]]:
    """Separa parágrafos de leitura (contexto do enunciado) de blocos de
    observação (Note e adote, Dados:, Glossário: etc.)."""
    leituras, obs = [], []
    for a in apoios:
        inicio = a.strip().lower()
        if inicio.startswith(_OBS_PREFIXOS):
            obs.append(a)
        else:
            leituras.append(a)
    return leituras, obs


def _render_questao(
    q: dict,
    label: str,
    key_suffix: str,
    on_responder=None,
    feedback=None,
    questao_id: int | None = None,
    usuario: str = "eu",
):
    """Renderiza questão (em cima) + página pan/zoom (embaixo).

    `feedback` (callable ou None) renderiza o resultado da resposta entre a
    questão e a página, se fornecido. `questao_id` (opcional) habilita a lista
    completa de temas da questão."""
    numero, enunciado = q["numero"], q["enunciado"]
    has_midia = bool(q.get("midia") or q.get("_midia"))
    midia = q.get("_midia", [])
    leituras, obs = _separar_observacoes(q.get("_textos_de_apoio", []))

    # identificação: vestibular, número, tipo e todos os temas
    info = f"**{_nome_vestibular(label)}** · Questão {numero} · {q['tipo']}"
    info += (
        f" · {len(midia)} figura(s)" if has_midia else " · sem mídia"
    )
    st.markdown(info)
    dados = _dados_temas(questao_id, usuario)
    if dados:
        st.markdown("**Temas:**")
        for t in dados:
            sc = f"{t['score']:.2f}" if t["score"] is not None else "—"
            th = f"{t['theta']:.2f}" if t["theta"] is not None else "—"
            st.markdown(
                f"- **{t['area']} → {t['tema']}** · "
                f"θ área: {th} · score: {sc} ({t['contagem']} tentativa(s))"
            )
    elif q.get("areas"):
        st.markdown("**Áreas:**")
        st.markdown("\n".join(f"- **{a['area']}**" for a in q["areas"]))

    with st.container(border=True):
        for apoio in leituras:
            st.markdown(apoio)
        st.markdown(f"**{enunciado}**")
        if obs:
            st.markdown("---")
            for a in obs:
                st.markdown(f"> {a}")
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
                    if on_responder is not None:
                        on_responder(numero, opcoes[picked])
            resp = st.session_state.get(key)
            if resp is not None and on_responder is None:
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

    if feedback is not None:
        feedback()

    pagina_padrao, bbox_padrao = _page_info(label, numero)
    pkey = f"pag_{key_suffix}"
    if pkey not in st.session_state:
        st.session_state[pkey] = pagina_padrao
    pagina = st.session_state[pkey]
    max_pag = _max_pagina(label)

    def _ir_pagina(delta: int):
        st.session_state[pkey] = max(1, min(max_pag, st.session_state[pkey] + delta))

    nav1, nav2, nav3 = st.columns([1, 1, 3])
    with nav1:
        st.button(
            "⬅ Anterior",
            key=f"ant_{key_suffix}",
            disabled=pagina <= 1,
            on_click=_ir_pagina,
            args=(-1,),
        )
    with nav2:
        st.button(
            "Próxima ➡",
            key=f"prox_{key_suffix}",
            disabled=pagina >= max_pag,
            on_click=_ir_pagina,
            args=(1,),
        )
    with nav3:
        nav_msg = f"Página **{pagina}** de {max_pag}"
        if pagina != pagina_padrao:
            nav_msg += " · navegando (enquadramento da questão desativado)"
        st.caption(nav_msg)

    bbox = bbox_padrao if pagina == pagina_padrao else None
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


def _questao_db_id(label: str, numero: int) -> int | None:
    with connect() as con:
        row = con.execute(
            "SELECT id FROM questoes WHERE exame_label = ? AND numero = ?",
            (label, numero),
        ).fetchone()
    return row["id"] if row else None


def modo_explorar():
    sidebar = st.sidebar
    usuario = sidebar.text_input("Usuário", key="usuario") or "eu"
    label = sidebar.selectbox("Exame", LABELS, key="explo_label")
    jq = JSON_DIR / f"{label}_questoes.json"
    if not jq.exists():
        st.error(f"Sem {jq}")
        return
    questoes = load_questoes(str(jq))["questoes"]

    qs = [q["numero"] for q in questoes]
    if st.session_state.get("explo_cur") not in qs:
        st.session_state.pop("explo_cur", None)
    cur = sidebar.selectbox(
        "Questão", qs, format_func=lambda n: f"Questão {n}", key="explo_cur"
    )
    st.session_state["params_label"] = label
    st.session_state["params_numero"] = str(cur)
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

    questao_id = _questao_db_id(label, q["numero"])
    if questao_id is None:
        st.caption(
            "Questão ainda não importada no banco — resposta não é computada nos índices."
        )
    fb_key = f"explo_fb_{label}_{cur}"

    def on_responder(numero, resp):
        if questao_id is not None:
            with connect() as con:
                r = motiva.responder(con, usuario, questao_id, resp)
            correta, gabarito = r["correta"], r["gabarito"]
        else:
            gabarito = q.get("gabarito")
            correta = (
                None
                if not gabarito
                else resp.strip().lower() == gabarito.strip().lower()
            )
        fb = {
            None: "⚠️ Anulada/sem gabarito oficial",
            True: "✅ Correta!",
            False: "❌ Errada.",
        }[correta]
        st.session_state[fb_key] = (fb, gabarito)

    def render_feedback():
        fb = st.session_state.get(fb_key)
        if fb:
            texto, gabarito = fb
            st.markdown(f"### {texto}")
            if gabarito:
                st.caption(f"Gabarito oficial: {gabarito.upper()}")

    _render_questao(
        q,
        label,
        f"explo_{label}_{cur}",
        on_responder=on_responder,
        feedback=render_feedback,
        questao_id=questao_id,
        usuario=usuario,
    )

    _mostrar_progresso(usuario)


@st.cache_data(ttl=300, show_spinner=False)
def _catalogo():
    """Áreas e temas do catálogo (IDs + nomes + fase) para os filtros opcionais."""
    with connect() as con:
        areas = con.execute("SELECT id, nome FROM areas ORDER BY nome").fetchall()
        temas = con.execute(
            "SELECT id, area_id, nome, fase FROM temas ORDER BY area_id, nome"
        ).fetchall()
    return [(a["id"], a["nome"]) for a in areas], [
        (t["id"], t["area_id"], t["nome"], t["fase"]) for t in temas
    ]


@st.cache_data(ttl=300, show_spinner=False)
def _fases_catalogo():
    """Fases/módulos do catálogo por área: {area: {ordem: nome_da_fase}}."""
    path = DATA / "assuntos.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        cat = json.load(f)["plano_de_estudos_vestibular"]
    return {
        disc["area"]: {m["ordem"]: m["fase"] for m in disc.get("modulos", [])}
        for disc in cat["disciplinas"]
    }


def _reset_filtro():
    st.session_state.pop("estudar_q", None)
    st.session_state.pop("estudar_fb", None)
    st.session_state.pop("estudar_aviso", None)
    st.session_state.pop("params_qid", None)
    st.session_state.pop("params_fb", None)
    st.session_state["estudar_fsrs"] = []


def _muda_area():
    st.session_state["filtro_fase"] = 0
    st.session_state["filtro_tema"] = 0
    _reset_filtro()


def _muda_fase():
    st.session_state["filtro_tema"] = 0
    _reset_filtro()


def _mostrar_progresso(usuario: str):
    """Resumo de progresso por área e nível por tema (comum aos dois modos)."""
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


def modo_estudar():
    sidebar = st.sidebar
    usuario = "eu"

    areas, temas = _catalogo()
    objs_area = {nome: id_ for id_, nome in areas}
    objs_tema = {nome: (id_, area_id) for id_, area_id, nome, _ in temas}
    area_labels = ["Todas as áreas", *[nome for _, nome in areas]]
    area = sidebar.selectbox(
        "Área (opcional)",
        area_labels,
        index=0,
        key="filtro_area",
        on_change=_muda_area,
    )
    area_id = objs_area.get(area)

    fases_area = sorted(_fases_catalogo().get(area, {}).items())
    fase_map = {f"Fase {o} · {n}": o for o, n in fases_area}
    fase = sidebar.selectbox(
        "Fase (opcional)",
        ["Todas as fases", *fase_map],
        index=0,
        key="filtro_fase",
        disabled=area_id is None or not fases_area,
        on_change=_muda_fase,
    )
    fase_id = fase_map.get(fase)

    temas_area = [
        nome
        for id_, aid, nome, tf in temas
        if (area_id is None or aid == area_id) and (fase_id is None or tf == fase_id)
    ]
    tema = sidebar.selectbox(
        "Tema (opcional)",
        ["Todos os temas", *temas_area],
        index=0,
        key="filtro_tema",
        on_change=_reset_filtro,
    )
    tema_id = objs_tema.get(tema, (None, None))[0]

    if sidebar.button("▶ Próxima questão"):
        with connect() as con:
            q = motiva.proxima_questao(
                con, usuario, area_id=area_id, tema_id=tema_id, fase=fase_id
            )
        st.session_state["estudar_q"] = q
        st.session_state["estudar_aviso"] = None
        st.session_state.pop("estudar_fb", None)
        st.session_state["params_qid"] = str(q["questao_id"]) if q else ""
        st.session_state.pop("params_fb", None)
        if q is None:
            filtrado = area_id is not None or tema_id is not None or fase_id is not None
            st.session_state["estudar_aviso"] = (
                "Nada vencido para estudar no filtro selecionado."
                if filtrado
                else "Nada vencido para estudar. Responda antes de pedir outra."
            )

    aviso = st.session_state.get("estudar_aviso")
    if aviso:
        st.info(aviso)

    q = st.session_state.get("estudar_q")
    if q is None:
        st.write("Clique em **Próxima questão** para começar a sessão adaptativa.")
    else:
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
            "questao_id": q["questao_id"],
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
            st.session_state["params_fb"] = _fb_encode(r["correta"], r["gabarito"])
            for t in r["temas"]:
                st.session_state.setdefault("estudar_fsrs", []).append(
                    f"{q['tema_nome']} → vencimento {t['vencimento'].isoformat()} ({t['estado']})"
                )

        def render_feedback():
            fb = st.session_state.get("estudar_fb")
            if fb:
                texto, r = fb
                st.markdown(f"### {texto}")
                st.caption(
                    f"Gabarito oficial: {r['gabarito']}" if r["gabarito"] else ""
                )

        _render_questao(
            full,
            q["exame_label"],
            f"estudar_{q['questao_id']}",
            on_responder=on_responder,
            feedback=render_feedback,
            questao_id=q["questao_id"],
            usuario=usuario,
        )

        with st.expander("Próximos vencimentos"):
            for linha in st.session_state.get("estudar_fsrs", [])[-20:]:
                st.write(linha)

    _mostrar_progresso(usuario)


def _fase_label(area: str, fase: int | None) -> str:
    if fase is None:
        return "Sem fase"
    nome = _fases_catalogo().get(area, {}).get(fase)
    return f"Fase {fase}" + (f" · {nome}" if nome else "")


def _df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    return df if not df.empty else pd.DataFrame([{"aviso": "Sem dados"}])


def modo_estatisticas():
    """Painel de estatísticas: visão geral, evolução, áreas, temas, exames,
    fila de revisão FSRS e histórico. Com área escolhida, tudo é filtrado ao
    contexto da área (desempenho/evolução por fase, temas, exames, revisões e
    histórico sem referências a outras áreas)."""
    usuario = st.session_state.get("usuario", "eu")

    with connect() as con:
        r = estatisticas.resumo(con, usuario)
        if r["total"] == 0:
            st.info(
                "Sem dados de tentativas para este usuário. Responda questões no "
                "modo **Estudar** ou **Explorar** para alimentar as estatísticas."
            )
            return
        areas = estatisticas.por_area(con, usuario)

    sel = st.sidebar.selectbox(
        "Área (estatísticas)",
        ["Todas as áreas", *(a["area"] for a in areas)],
        index=0,
        key="stats_area",
    )
    area_id = next((a["area_id"] for a in areas if a["area"] == sel), None)

    with connect() as con:
        dias = estatisticas.por_dia(con, usuario, area_id)
        temas = estatisticas.por_tema(con, usuario)
        exames = estatisticas.por_exame(con, usuario, area_id)
        rev = estatisticas.revisoes(con, usuario, area_id=area_id)
        hist = estatisticas.historico(con, usuario, area_id)
        fases = estatisticas.por_fase(con, usuario, area_id)
        evol = estatisticas.evolucao_por_fase(con, usuario, area_id)
        vest = estatisticas.por_vestibular(con, usuario, area_id)
        cob = estatisticas.cobertura_fase(con, usuario, area_id)
        gaps = estatisticas.gaps(con, usuario, area_id)
        reta = estatisticas.retencao(con, usuario, area_id=area_id)
        bvt = estatisticas.b_vs_theta(con, usuario, area_id)

    st.header("📊 Estatísticas")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Tentativas", r["total"])
    c2.metric("Acertos", r["acertos"])
    c3.metric("Aproveitamento", f"{r['pct']:.1f}%")
    c4.metric("Questões distintas", r["distintas"])
    c5.metric(
        "Dificuldade média (b)",
        f"{r['b_medio']:+.2f}" if r["b_medio"] is not None else "—",
    )
    c6.metric("Temas vencidos", r["temas_vencidos"])
    st.caption(
        f"Período: {r['primeira']} → {r['ultima']} · usuário `{usuario}` · "
        "b = dificuldade em logit (0 ≈ mediana do acervo; θ da área usa a mesma escala)"
    )

    if sel != "Todas as áreas":
        reg = next((a for a in areas if a["area"] == sel), {})
        reg_bvt = next((b for b in bvt if b["area"] == sel), {})
        st.subheader(f"Área: {sel}")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(
            "θ (habilidade)",
            f"{reg['theta']:+.2f}" if reg.get("theta") is not None else "—",
        )
        m2.metric("Tentativas", reg.get("tentativas", 0))
        m3.metric("Acertos", reg.get("acertos", 0))
        m4.metric(
            "Aproveitamento",
            f"{reg['pct']:.1f}%" if reg.get("pct") is not None else "—",
        )
        m5.metric(
            "Δ (θ−b)",
            f"{reg_bvt['delta']:+.2f}" if reg_bvt.get("delta") is not None else "—",
        )
        if reg_bvt.get("delta") is not None:
            st.caption(
                "Δ = θ da área − b médio das questões tentadas. "
                "Positivo → questões abaixo do seu nível; negativo → acima."
            )

        st.markdown("#### Desempenho por fase")
        df_fases = _df(
            [
                {
                    "Fase": _fase_label(fa["area"], fa["fase"]),
                    "Temas": fa["n_temas"],
                    "Score médio": fa["score_medio"],
                    "Tentativas": fa["tentativas"],
                    "pct": fa["pct"],
                }
                for fa in fases
            ]
        )
        if "aviso" not in df_fases.columns:
            cfa, cfb = st.columns([2, 1])
            cfa.bar_chart(
                df_fases.set_index("Fase")["pct"], y_label="Aproveitamento (%)"
            )
            cfb.dataframe(df_fases, hide_index=True, use_container_width=True)
        else:
            st.write("Sem dados por fase ainda nesta área.")

        st.markdown("#### Evolução por fase")
        df_ev = _df(
            [
                {**_e, "Fase": _fase_label(_e["area"], _e["fase"])}
                for _e in evol
            ]
        )
        if "aviso" not in df_ev.columns:
            piv = df_ev.pivot(index="dia", columns="Fase", values="pct").fillna(0)
            st.line_chart(piv, y_label="Aproveitamento (%)")
            st.dataframe(
                df_ev[["dia", "Fase", "acertos", "tentativas", "pct"]],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.write("Sem tentativas por fase ainda nesta área.")

        st.markdown("#### Desempenho por vestibular")
        df_vest = _df(vest)
        if "aviso" not in df_vest.columns:
            cv1, cv2 = st.columns([2, 1])
            cv1.bar_chart(
                df_vest.set_index("vestibular")["pct"], y_label="Aproveitamento (%)"
            )
            cv2.dataframe(df_vest, hide_index=True, use_container_width=True)
        else:
            st.write("Sem tentativas por vestibular ainda.")

        st.markdown("#### Cobertura por fase (provas UNIVESP)")
        df_cob = _df(
            [
                {
                    "Fase": _fase_label(c["area"], c["fase"]),
                    "Questões da banca": c["questoes_banca"],
                    "Tentadas (distintas)": c["tentadas"],
                    "Cobertura": c["cobertura"],
                }
                for c in cob
            ]
        )
        if "aviso" not in df_cob.columns:
            st.dataframe(
                df_cob,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Cobertura": st.column_config.ProgressColumn(
                        "Cobertura",
                        min_value=0.0,
                        max_value=100.0,
                        format="%.1f%%",
                    ),
                },
            )
        else:
            st.write("Sem dados de cobertura ainda (banco UNIVESP).")

        st.markdown("#### Temas cobrados não iniciados (UNIVESP)")
        df_gaps = _df(
            [
                {
                    "Fase": _fase_label(g["area"], g["fase"]),
                    "Tema": g["tema"],
                    "Questões na banca": g["questoes_banca"],
                }
                for g in gaps[:15]
            ]
        )
        if "aviso" not in df_gaps.columns:
            st.dataframe(df_gaps, hide_index=True, use_container_width=True)
            st.caption(
                "Temas com maior recorrência nas provas UNIVESP que ainda não "
                "foram iniciados — boa pista para a próxima fase do estudo."
            )
        else:
            st.write("Todos os temas desta área já foram iniciados.")

        st.markdown("#### Retenção (R FSRS)")
        df_ret = _df(
            [
                {
                    "Fase": _fase_label(rr["area"], rr["fase"]),
                    "Tema": rr["tema"],
                    "R": rr["r"],
                    "Estado": rr["estado"],
                    "Revisões": rr["repos"],
                    "Lapses": rr["lapses"],
                    "Revisão": rr["vencimento"],
                }
                for rr in reta
            ]
        )
        if "aviso" not in df_ret.columns:
            st.dataframe(df_ret, hide_index=True, use_container_width=True)
            st.caption(
                "R = retrievability do FSRS (prob. de lembrar hoje), do menor "
                "para o maior. R baixo indica esquecimento próximo."
            )
        else:
            st.write("Sem cartões FSRS ainda nesta área.")

    st.subheader("Evolução")
    df_dias = _df(dias)
    col_a, col_b = st.columns([2, 1])
    col_a.line_chart(df_dias.set_index("dia")["pct"], y_label="Aproveitamento (%)")
    col_b.dataframe(
        df_dias[["dia", "tentativas", "acertos", "pct"]],
        hide_index=True,
        use_container_width=True,
    )

    if sel == "Todas as áreas":
        st.subheader("Por área")
        df_areas = _df(areas)
        df_bvt = _df(bvt)
        if "aviso" not in df_bvt.columns:
            df_areas = df_areas.merge(
                df_bvt[["area", "b_medio", "delta"]], on="area", how="left"
            )
        col1, col2 = st.columns(2)
        theta_df = df_areas[df_areas["theta"].notna()].set_index("area")
        pct_df = df_areas[df_areas["pct"].notna()].set_index("area")
        if not theta_df.empty:
            col1.bar_chart(theta_df["theta"], y_label="θ (habilidade)")
        if not pct_df.empty:
            col2.bar_chart(pct_df["pct"], y_label="Aproveitamento (%)")
        cols_areas = ["area", "theta", "n_obs", "acertos", "tentativas", "pct"]
        if "b_medio" in df_areas.columns:
            cols_areas = [
                "area", "theta", "b_medio", "delta",
                "n_obs", "acertos", "tentativas", "pct",
            ]
        st.dataframe(
            df_areas[cols_areas],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "`b_medio` = dificuldade média (logit) das questões tentadas; "
            "`delta` = θ − b (positivo → estudando abaixo do nível)."
        )

    st.subheader("Por tema")
    df_temas = _df(temas)
    if "fase" in df_temas.columns:
        df_temas["Fase"] = df_temas.apply(
            lambda row: _fase_label(row["area"], row["fase"]), axis=1
        )
    if sel != "Todas as áreas":
        df_temas = df_temas[df_temas["area"] == sel]
    st.dataframe(
        df_temas[
            [
                "area",
                "tema",
                *(["Fase"] if "Fase" in df_temas.columns else []),
                "score",
                "racha",
                "tentativas",
                "lapses",
                "estado",
                "vencimento",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "score": st.column_config.ProgressColumn(
                "Score", min_value=0.0, max_value=1.0, format="%.2f"
            ),
        },
    )
    st.caption(
        "Ordenado pelo score (mais fraco primeiro). `lapses` = esquecimentos FSRS."
    )

    st.subheader("Por exame")
    df_exames = _df(exames)
    col3, col4 = st.columns([1, 2])
    col3.dataframe(
        df_exames[["exame", "tentativas", "acertos", "pct"]],
        hide_index=True,
        use_container_width=True,
    )
    if not df_exames.empty:
        col4.bar_chart(
            df_exames.set_index("exame")["pct"], y_label="Aproveitamento (%)"
        )

    st.subheader("Fila de revisão (FSRS)")
    if not rev:
        st.write("Sem revisões agendadas ainda.")
    else:
        df_rev = _df(rev)
        df_rev["venc"] = pd.to_datetime(
            df_rev["vencimento"], errors="coerce"
        ).dt.strftime("%d/%m %H:%M")
        badge = {"atrasada": "🟠", "hoje": "🟡", "próxima": "🟢"}
        df_rev["status"] = (
            df_rev["status"].map(badge).fillna("") + " " + df_rev["status"]
        )
        for s in ("atrasada", "hoje", "próxima"):
            sub = df_rev[df_rev["status"].str.endswith(s)]
            if not sub.empty:
                with st.expander(
                    f"{badge[s]} {s.capitalize()} ({len(sub)})", expanded=s != "próxima"
                ):
                    st.dataframe(
                        sub[
                            [
                                "area",
                                "tema",
                                "estado",
                                "repos",
                                "lapses",
                                "venc",
                                "status",
                            ]
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )

    with st.expander("Histórico detalhado"):
        df_hist = _df(hist)
        hist_cols = ["data", "exame", "questao", "resposta", "gabarito", "resultado"]
        if sel == "Todas as áreas":
            hist_cols += ["areas", "temas"]
        else:
            hist_cols += ["temas"]
        st.dataframe(
            df_hist[[c for c in hist_cols if c in df_hist.columns]],
            hide_index=True,
            use_container_width=True,
        )


def main():
    st.title("🎓 Estudo Vestibular")
    _restaurar_do_url()
    modo = st.sidebar.radio("Modo", ["Estudar", "Explorar", "Estatísticas"], key="modo")
    params = {
        "modo": modo,
        "usuario": st.session_state.get("usuario", "eu"),
        "label": "",
        "numero": "",
        "qid": "",
        "fb": "",
    }
    if modo == "Estudar":
        modo_estudar()
        params["qid"] = st.session_state.get("params_qid", "")
        params["fb"] = st.session_state.get("params_fb", "")
    elif modo == "Explorar":
        modo_explorar()
        params["label"] = st.session_state.get("params_label", "")
        params["numero"] = st.session_state.get("params_numero", "")
    else:
        modo_estatisticas()
    _sync_params(**params)


if __name__ == "__main__":
    main()
