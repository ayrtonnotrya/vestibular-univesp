"""App de estudo: responde questões com a página em pan/zoom.

Cinco modos operacionais:
- **Explorar**: seleção manual por exame/questão (comportamento original).
- **Estudar** (adaptativo): sorteio ponderado sobre o catálogo inteiro
  (frequência + fraqueza + exploração); resposta atualiza FSRS, habilidade por
  área e a dificuldade empírica (b).
- **Revisão**: fila dedicada dos temas vencidos pelo FSRS com questão já vista
  (pendências do caderno de erros primeiro; nunca inéditas).
- **Estatísticas**: dashboards SQL direto no `data/vestibular.db`.
- **Redação**: envia a redação para correção IA em fila assíncrona (worker no
  próprio processo; fechar o navegador não interrompe) — nota por critério +
  aula do tutor.

Roda no docker-compose:  docker compose up vestibular-app (porta 8501).
"""

import datetime as dt
import json
import re
from pathlib import Path

import estatisticas
import estilo
import pandas as pd
import streamlit as st
from panzoom import view_page

from vestibular.estudo import motiva
from vestibular.estudo.db import connect
from vestibular.estudo.fsrs_config import MIN_TENTATIVAS_REVISAO
from vestibular.estudo.fuso import hoje as fuso_hoje
from vestibular.redacao import criterios as red_criterios
from vestibular.redacao import servico as red_servico
from vestibular.redacao import worker as red_worker

DATA = Path("/app/data")
JSON_DIR = DATA / "json"
PAGES_DIR = DATA / "paginas"

_RANK_EXAMES = {"fuvest": 1, "univesp": 2, "enem": 3, "fatec": 4, "unesp": 5}


def _ordem_exame(label: str) -> tuple:
    partes = label.split("_")
    vest = partes[0]
    ano = next((int(p) for p in partes[1:] if p.isdigit()), 0)
    return (_RANK_EXAMES.get(vest, 9), -ano, label)


def _exames_disponiveis():
    return sorted(
        (p.name[: -len("_questoes.json")] for p in JSON_DIR.glob("*_questoes.json")),
        key=_ordem_exame,
    )


LABELS = _exames_disponiveis()

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


def _ano_do_label(label: str) -> str:
    """Ano extraído do label (fuvest_2024 → '2024')."""
    for p in label.split("_"):
        if p.isdigit() and len(p) == 4:
            return p
    return ""


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


def _restaurar_questao(prefix: str, usuario: str):
    """Restaura a questão em aberto (Estudar/Revisão).

    Preferência: a questão persistida em `sessoes` (sobrevive a refresh mesmo
    sem `?qid=`); senão o `?qid=` da URL."""
    with connect() as con:
        _, qid = motiva.sessao_atual(con, usuario)
        if qid is None:
            raw = _param("qid")
            try:
                qid = int(raw) if raw else None
            except ValueError:
                qid = None
        resto = motiva.questao_por_id(con, usuario, qid) if qid else None
    if resto:
        st.session_state[f"{prefix}_q"] = resto
        st.session_state["params_qid"] = str(qid)


def _restaurar_fb(prefix: str):
    """Restaura o feedback da última resposta (Estudar/Revisão) de `?fb=`."""
    raw = _param("fb") or ""
    st.session_state["params_fb"] = raw
    if ":" not in raw:
        return
    marc, gab = raw.split(":", 1)
    correta = {"a": None, "c": True, "e": False}.get(marc)
    if marc in ("a", "c", "e"):
        st.session_state[f"{prefix}_fb"] = (correta, gab)


_MODO_LABEL = {
    "estudar": "Estudar",
    "revisao": "Revisão",
    "explorar": "Explorar",
    "estatisticas": "Estatísticas",
    "redacao": "Redação",
}


def _fmt_data(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return dt.datetime.fromisoformat(iso).strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return iso[:16]


def _restaurar_do_url():
    """Na primeira execução após um load (ex.: refresh no celular), restaura os
    widgets a partir da URL e/ou da sessão persistida no banco: modo, usuário e
    a questão em aberto."""
    if st.session_state.get("_carregado"):
        return
    st.session_state.setdefault("usuario", _param("usuario") or "eu")
    modo = _param("modo")
    if modo is None:
        with connect() as con:
            modo, _ = motiva.sessao_atual(con, "eu")
        modo = _MODO_LABEL.get(modo or "") or "Estudar"
    if modo not in ("Estudar", "Revisão", "Explorar", "Estatísticas", "Redação"):
        modo = "Estudar"
    st.session_state["modo"] = modo
    if modo in ("Estudar", "Revisão"):
        prefix = "estudar" if modo == "Estudar" else "revisao"
        _restaurar_questao(prefix, "eu")
        _restaurar_fb(prefix)
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
    completa de temas da questão.
    """
    numero, enunciado = q["numero"], q["enunciado"]
    has_midia = bool(q.get("midia") or q.get("_midia"))
    midia = q.get("_midia", [])
    leituras, obs = _separar_observacoes(q.get("_textos_de_apoio", []))

    # identificação: vestibular, número, tipo e todos os temas
    nome = _nome_vestibular(label)
    ano = _ano_do_label(label)
    dados = _dados_temas(questao_id, usuario)
    if dados:
        temas = [f"{t['area']} → {t['tema']}" for t in dados]
    else:
        temas = [f"{a['area']} → {a['tema']}" for a in (q.get("areas") or [])]
    st.markdown(
        estilo.header_html(
            numero,
            [nome, f"{ano}" if ano else "" , *temas[:3]],
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        estilo.info_html(
            [
                ("Vestibular", nome),
                ("Ano", ano or "—"),
                ("Questão", str(numero)),
                ("Tipo", q["tipo"]),
                ("Figuras", str(len(midia)) if has_midia else "—"),
            ]
        ),
        unsafe_allow_html=True,
    )
    partes_apoio = "".join(f"<p>{estilo.esc(a)}</p>" for a in leituras)
    par_p = "<p>" + estilo.esc(enunciado).replace("\n", "</p><p>")
    st.markdown(
        f"<div class='q-enun'><div class='q-leituras'>{partes_apoio}</div>{par_p}</div>",
        unsafe_allow_html=True,
    )
    if obs:
        st.markdown("---")
        for a in obs:
            st.markdown(f"> {a}")

    alt = q.get("alternativas")
    if alt:
        key = f"resp_{key_suffix}"
        if key not in st.session_state:
            st.session_state[key] = None
        resp = st.session_state.get(key)
        letras = sorted(alt)
        choice = st.radio(
            "Responda:",
            letras,
            format_func=lambda k: alt[k],
            index=None,
            key=f"radio_{key_suffix}",
            disabled=resp is not None,
        )
        st.pills(
            "Grau de certeza:",
            list(motiva.GRAUS_CERTEZA),
            selection_mode="single",
            default="conviccao",
            key=f"certeza_{key_suffix}",
            format_func=motiva.GRAU_CERTEZA_LABEL.get,
            disabled=resp is not None,
            help="Sua autoconfiança antes de conferir o gabarito.",
        )
        if resp is None and st.button(
            "Responder", key=f"btn_{key_suffix}", type="primary"
        ):
            if choice is None:
                st.warning("Escolha uma alternativa.")
            else:
                letra = choice
                st.session_state[key] = letra
            st.session_state[f"certeza_usada_{key_suffix}"] = (
                str(st.session_state.get(f"certeza_{key_suffix}", "conviccao"))
            )
            if on_responder is not None:
                r = on_responder(
                    numero,
                    letra,
                    st.session_state[f"certeza_usada_{key_suffix}"],
                )
                if r is not None:
                    st.session_state[f"res_{key_suffix}"] = r
        if resp is not None and on_responder is None:
            gab = q.get("gabarito")
            correta = (
                None
                if not gab
                else resp.strip().lower() == gab.strip().lower()
            )
            st.markdown(estilo.banda_html(correta, gab), unsafe_allow_html=True)
    else:
        st.info(
            "Redação — dissertação."
            if q["tipo"] == "redacao"
            else "Sem alternativas."
        )

    if feedback is not None:
        feedback()

    _passo_b_caderno_erros(key_suffix, on_responder)

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


def _passo_b_caderno_erros(key_suffix: str, on_responder):
    """Caderno de erros (Passo B): bloco inline (sem popover) de causa do erro
    + síntese ativa, exibido assim que o gabarito é conferido quando a questão
    foi errada ou o usuário não estava convicto (dúvida/chute).

    Acertos convictos seguem o fluxo normal, sem interrupção. O botão de salvar
    só grava com texto preenchido; vazio não envia (e não avança)."""
    resp = st.session_state.get(f"resp_{key_suffix}")
    r = st.session_state.get(f"res_{key_suffix}")
    if resp is None or on_responder is None or r is None:
        return
    status = st.session_state.get(f"anotado_{key_suffix}")
    if status:
        if status == "salvo":
            st.success("✅ Causa + síntese salvas no caderno de erros.")
        return
    certeza = st.session_state.get(f"certeza_usada_{key_suffix}", "conviccao")
    if r.get("correta") is not False and certeza not in ("duvida", "chute"):
        return
    tid = r.get("tentativa_id")
    if not tid:
        st.info("Responda pelo modo Estudar/Revisão para registrar no caderno de erros.")
        return

    def salvar(causa, sintese):
        if not (sintese or "").strip():
            st.session_state[f"anotado_{key_suffix}"] = True
            st.toast("Sem síntese — anotação não foi salva.", icon="🗒️")
        else:
            with connect() as con:
                motiva.anotar_erro(con, tid, causa, sintese)
            st.session_state[f"anotado_{key_suffix}"] = "salvo"
            st.toast("✅ Caderno de erros atualizado.", icon="🗒️")

    def ignorar():
        st.session_state[f"anotado_{key_suffix}"] = True

    with st.container(border=True):
        st.markdown("**🗒️ Caderno de erros** — registre o que você aprendeu:")
        causa = st.radio(
            "Causa do erro/dúvida",
            list(motiva.CAUSAS_ERRO),
            horizontal=True,
            index=0,
            key=f"causa_{key_suffix}",
            format_func=motiva.CAUSA_ERRO_LABEL.get,
        )
        sintese = st.text_input(
            "Síntese ativa (1-2 frases)",
            placeholder="O que você aprendeu com este erro/dúvida?",
            key=f"sintese_{key_suffix}",
        )
        c1, c2 = st.columns(2)
        with c1:
            st.button(
                "💾 Salvar",
                type="primary",
                use_container_width=True,
                key=f"save_{key_suffix}",
            ) and salvar(causa, (sintese or "").strip())
        with c2:
            st.button(
                "Ignorar",
                use_container_width=True,
                key=f"ign_{key_suffix}",
                on_click=ignorar,
            )


def _anotar_pendente(prefix: str, usuario: str) -> None:
    """Salva a anotação já digitada antes de avançar, para nada se perder.

    Só escreve quando a questão em aberto foi respondida com erro/dúvida/chute,
    ainda não anotada/ignorada e a síntese ativa tem texto — campo vazio não
    envia (regra do usuário). Idempotente: o botão "Salvar" já marca
    `anotado_*`, então este passo não duplica. O botão Salvar nunca avança."""
    q = st.session_state.get(f"{prefix}_q")
    if not q:
        return
    key = f"{prefix}_{q['questao_id']}"
    resp = st.session_state.get(f"resp_{key}")
    r = st.session_state.get(f"res_{key}")
    if resp is None or r is None:
        return
    if st.session_state.get(f"anotado_{key}"):
        return
    certeza = st.session_state.get(f"certeza_usada_{key}", "conviccao")
    if r.get("correta") is not False and certeza not in ("duvida", "chute"):
        return
    tid = r.get("tentativa_id")
    if not tid:
        return
    sintese = (st.session_state.get(f"sintese_{key}") or "").strip()
    if not sintese:
        return
    causa = st.session_state.get(f"causa_{key}") or motiva.CAUSAS_ERRO[0]
    with connect() as con:
        motiva.anotar_erro(con, tid, causa, sintese)
    st.session_state[f"anotado_{key}"] = "salvo"


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
    usuario = "eu"
    label = sidebar.selectbox("Exame", LABELS, key="explo_label")
    jq = JSON_DIR / f"{label}_questoes.json"
    if not jq.exists():
        st.error(f"Sem {jq}")
        return
    questoes = load_questoes(str(jq))["questoes"]

    qs = [q["numero"] for q in questoes]
    if st.session_state.get("explo_proxima"):
        atual = st.session_state.get("explo_cur")
        try:
            idx = qs.index(atual)
        except ValueError:
            idx = len(qs)
        if idx + 1 < len(qs):
            st.session_state["explo_cur"] = qs[idx + 1]
        st.session_state.pop("explo_proxima", None)
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

    def on_responder(numero, resp, grau_certeza):
        if questao_id is not None:
            with connect() as con:
                r = motiva.responder(
                    con, usuario, questao_id, resp, grau_certeza=grau_certeza
                )
            correta, gabarito = r["correta"], r["gabarito"]
        else:
            gabarito = q.get("gabarito")
            correta = (
                None
                if not gabarito
                else resp.strip().lower() == gabarito.strip().lower()
            )
            r = {"correta": correta, "gabarito": gabarito, "tentativa_id": None}
        fb = {
            None: "anulada",
            True: "correta",
            False: "errada",
        }[correta]
        st.session_state[fb_key] = (fb, gabarito)
        return r

    def render_feedback():
        fb = st.session_state.get(fb_key)
        if fb:
            status, gabarito = fb
            st.markdown(
                estilo.banda_html(
                    {"correta": True, "errada": False, "anulada": None}[status],
                    gabarito,
                ),
                unsafe_allow_html=True,
            )

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
    for k in (
        "estudar_q",
        "estudar_fb",
        "estudar_aviso",
        "revisao_q",
        "revisao_fb",
        "revisao_aviso",
    ):
        st.session_state.pop(k, None)
    st.session_state.pop("params_qid", None)
    st.session_state.pop("params_fb", None)
    st.session_state["estudar_fsrs"] = []
    st.session_state["revisao_fsrs"] = []
    with connect() as con:
        motiva.apagar_sessao(con, "eu")


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

    with connect() as con:
        motiva.marcar_sessao(con, usuario, "estudar")

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

    def proxima():
        _anotar_pendente("estudar", usuario)
        with connect() as con:
            q2 = motiva.proxima_questao(
                con, usuario, area_id=area_id, tema_id=tema_id, fase=fase_id
            )
            motiva.marcar_sessao(
                con, usuario, "estudar", q2["questao_id"] if q2 else None
            )
        st.session_state["estudar_q"] = q2
        st.session_state["estudar_aviso"] = None
        st.session_state.pop("estudar_fb", None)
        st.session_state["params_qid"] = str(q2["questao_id"]) if q2 else ""
        st.session_state.pop("params_fb", None)
        if q2 is None:
            filtrado = area_id is not None or tema_id is not None or fase_id is not None
            st.session_state["estudar_aviso"] = (
                "Nada vencido para estudar no filtro selecionado."
                if filtrado
                else "Nada vencido para estudar. Responda antes de pedir outra."
            )

    if sidebar.button("▶ Próxima questão"):
        proxima()

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

        def on_responder(numero, resp, grau_certeza):
            with connect() as con:
                r = motiva.responder(
                    con, usuario, q["questao_id"], resp, grau_certeza=grau_certeza
                )
            st.session_state["estudar_fb"] = (r["correta"], r["gabarito"])
            st.session_state["params_fb"] = _fb_encode(r["correta"], r["gabarito"])
            for t in r["temas"]:
                if t["vencimento"] is not None:
                    linha = (
                        f"{q['tema_nome']} → vencimento "
                        f"{t['vencimento'].isoformat()[:10]} ({t['estado']})"
                    )
                else:
                    linha = (
                        f"{q['tema_nome']} → em exploração "
                        f"(agendamento em {MIN_TENTATIVAS_REVISAO} respostas)"
                    )
                st.session_state.setdefault("estudar_fsrs", []).append(linha)
            return r

        def render_feedback():
            fb = st.session_state.get("estudar_fb")
            if fb:
                correta, gabarito = fb
                st.markdown(
                    estilo.banda_html(correta, gabarito),
                    unsafe_allow_html=True,
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


def _pendencias_tema(usuario: str, tema_id: int):
    """Pendências (erro/dúvida/chute) do tema — última tentativa por questão."""
    with connect() as con:
        rows = con.execute(
            """SELECT questao_id, correta, grau_certeza, data, exame_label, numero
               FROM (
                 SELECT t.questao_id, t.correta, t.grau_certeza, t.data,
                        q.exame_label, q.numero,
                        ROW_NUMBER() OVER (
                          PARTITION BY t.questao_id ORDER BY t.data DESC, t.id DESC
                        ) AS rn
                 FROM tentativas t
                 JOIN classificacoes c ON c.questao_id = t.questao_id
                 JOIN questoes q ON q.id = t.questao_id
                 WHERE t.usuario = ? AND c.tema_id = ?
               )
               WHERE rn = 1 AND (correta = 0 OR grau_certeza IN ('duvida', 'chute'))
               ORDER BY data, questao_id""",
            (usuario, tema_id),
        ).fetchall()
    if not rows:
        st.caption("Sem pendências neste tema — só acertos recentes.")
        return
    for r in rows:
        marc = "❌" if r["correta"] == 0 else "⚠️"
        certeza = motiva.GRAU_CERTEZA_LABEL.get(r["grau_certeza"], "")
        st.markdown(
            f"{marc} **{_nome_vestibular(r['exame_label'])}** Q{r['numero']} · "
            f"{certeza} · {(r['data'] or '')[:10]}"
        )


def modo_revisao():
    """Fila de revisão: temas vencidos pelo FSRS (portão de contagem + cap por
    sessão) com questão JÁ vista — pendências (erro/dúvida/chute) primeiro,
    depois acertos antigos. Nunca questões inéditas."""
    sidebar = st.sidebar
    usuario = "eu"

    with connect() as con:
        motiva.marcar_sessao(con, usuario, "revisao")

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

    with connect() as con:
        resumo = motiva.resumo_revisao(
            con, usuario, area_id=area_id, tema_id=tema_id, fase=fase_id
        )
    st.caption(
        f"**{resumo['vencidos']} temas vencidos** no agendamento · "
        f"**{resumo['pendencias']} pendências** no caderno de erros do escopo."
    )

    def proxima():
        _anotar_pendente("revisao", usuario)
        with connect() as con:
            q2 = motiva.proxima_revisao(
                con, usuario, area_id=area_id, tema_id=tema_id, fase=fase_id
            )
            motiva.marcar_sessao(
                con, usuario, "revisao", q2["questao_id"] if q2 else None
            )
        st.session_state["revisao_q"] = q2
        st.session_state["revisao_aviso"] = None
        st.session_state.pop("revisao_fb", None)
        st.session_state["params_qid"] = str(q2["questao_id"]) if q2 else ""
        st.session_state.pop("params_fb", None)
        if q2 is None:
            filtrado = area_id is not None or tema_id is not None or fase_id is not None
            if resumo["vencidos"]:
                st.session_state["revisao_aviso"] = (
                    "Temas vencidos, mas sem questões disponíveis: sem "
                    "pendências no caderno de erros e sem inéditas no tema."
                    if not filtrado
                    else "Temas vencidos no filtro, mas sem questões disponíveis."
                )
            else:
                st.session_state["revisao_aviso"] = (
                    "Nada vencido para revisar no filtro selecionado."
                    if filtrado
                    else "Nada vencido para revisar. Temas entram na fila após "
                    f"{MIN_TENTATIVAS_REVISAO} respostas."
                )

    if sidebar.button("▶ Próxima (revisão)"):
        proxima()

    aviso = st.session_state.get("revisao_aviso")
    if aviso:
        st.info(aviso)

    q = st.session_state.get("revisao_q")
    if q is None:
        st.write("Clique em **Próxima (revisão)** para começar a fila de revisão.")
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

        def on_responder(numero, resp, grau_certeza):
            with connect() as con:
                r = motiva.responder(
                    con, usuario, q["questao_id"], resp, grau_certeza=grau_certeza
                )
            st.session_state["revisao_fb"] = (r["correta"], r["gabarito"])
            st.session_state["params_fb"] = _fb_encode(r["correta"], r["gabarito"])
            for t in r["temas"]:
                if t["vencimento"] is not None:
                    linha = (
                        f"{q['tema_nome']} → vencimento "
                        f"{t['vencimento'].isoformat()[:10]} ({t['estado']})"
                    )
                else:
                    linha = (
                        f"{q['tema_nome']} → em exploração "
                        f"(agendamento em {MIN_TENTATIVAS_REVISAO} respostas)"
                    )
                st.session_state.setdefault("revisao_fsrs", []).append(linha)
            return r

        def render_feedback():
            fb = st.session_state.get("revisao_fb")
            if fb:
                correta, gabarito = fb
                st.markdown(
                    estilo.banda_html(correta, gabarito),
                    unsafe_allow_html=True,
                )

        _render_questao(
            full,
            q["exame_label"],
            f"revisao_{q['questao_id']}",
            on_responder=on_responder,
            feedback=render_feedback,
            questao_id=q["questao_id"],
            usuario=usuario,
        )

        with st.expander(f"🗒️ Pendências de {q['tema_nome']}"):
            _pendencias_tema(usuario, q["tema_id"])

        with st.expander("Próximos vencimentos"):
            for linha in st.session_state.get("revisao_fsrs", [])[-20:]:
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


def _ordem_dia(df: pd.DataFrame) -> list[str]:
    """Rótulos `dia` (dd/mm) na ordem cronológica original (ISO em `data`).

    `pivot`/índices de strings reordenam alfabeticamente ("01/09" antes de
    "31/08"); aqui restaura-se a sequência do ISO sem mudar o rótulo."""
    rotulo = {data: dia for data, dia in zip(df["data"], df["dia"])}
    return [rotulo[o] for o in dict.fromkeys(df["data"])]


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
            piv = piv.loc[[d for d in _ordem_dia(df_ev) if d in piv.index]]
            st.line_chart(piv, y_label="Aproveitamento (%, suavizado)")
            st.dataframe(
                df_ev[["dia", "Fase", "acertos", "tentativas", "pct", "pct_bruto"]],
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
    if "dia" in df_dias.columns:
        df_dias["dia"] = pd.Categorical(
            df_dias["dia"], categories=[d["dia"] for d in dias], ordered=True
        )
    col_a, col_b = st.columns([2, 1])
    col_a.line_chart(
        df_dias.sort_values("dia").set_index("dia")["pct"],
        y_label="Aproveitamento (%, suavizado)",
    )
    col_b.dataframe(
        df_dias[["dia", "tentativas", "acertos", "pct", "pct_bruto"]],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "Aproveitamento suavizado por amostragem (Beta-Binomial: κ=4 "
        "tentativas fictícias a 50%, mesmo κ do b do Rasch). Dias com poucas "
        "questões tendem a 50% em vez de 0/100%; `pct_bruto` ao lado para "
        "conferência."
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
            df_rev["vencimento"], format="ISO8601", errors="coerce"
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
        hist_cols = [
            "data",
            "exame",
            "questao",
            "resposta",
            "gabarito",
            "resultado",
            "certeza",
            "causa_erro",
            "sintese_ativa",
        ]
        if sel == "Todas as áreas":
            hist_cols += ["areas", "temas"]
        else:
            hist_cols += ["temas"]
        st.dataframe(
            df_hist[[c for c in hist_cols if c in df_hist.columns]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "sintese_ativa": st.column_config.TextColumn(
                    "Síntese ativa", width="large"
                ),
            },
        )


@st.cache_resource(show_spinner=False)
def _iniciar_worker_redacao():
    """Singleton da daemon-thread da fila de redação (1× por processo/container)."""
    return red_worker.guardar()


_ETAPAS = ["fila", "corrigindo", "aulando", "concluido"]
_ETAPA_LABEL = {
    "fila": "🕐 fila",
    "corrigindo": "✍️ corrigindo",
    "aulando": "🎓 aulando",
    "concluido": "✅ concluído",
    "erro": "⚠️ erro",
    "cancelado": "🚫 cancelado",
}


def _pipeline_html(status: str) -> str:
    if status == "erro":
        return "🕐 fila → ✍️ corrigindo → 🎓 aulando → <b>⚠️ erro</b>"
    if status == "cancelado":
        return "🕐 <b>fila → 🚫 cancelado</b>"
    atual = _ETAPAS.index(status)
    partes = []
    for i, et in enumerate(_ETAPAS):
        chip = _ETAPA_LABEL[et]
        if i < atual:
            partes.append(chip)
        elif i == atual:
            partes.append(f"<b>{chip}</b>")
        else:
            partes.append(f"<span style='opacity:.35'>{chip}</span>")
    return " → ".join(partes)


def _render_resultado(job: dict):
    """Render 100% do banco: notas por critério, anulação, comentário e aula."""
    corr = job.get("correcao")
    aula = job.get("aula")
    if corr:
        if corr.get("anulado"):
            st.error(
                "🚫 **REDAÇÃO ANULADA — nota 0.** Regra: "
                + str(corr.get("motivo_anulacao") or "não informada")
                + (" — " + corr["justificativa_anulacao"] if corr.get("justificativa_anulacao") else "")
            )
        m1, m2 = st.columns(2)
        nota = job.get("nota_total")
        m1.metric("Nota total", f"{nota:g} / 100" if nota is not None else "—")
        m2.metric("Extensão", f"{job['palavras']} palavras")
        for comp in corr["competencias"]:
            fr = 0.0 if not comp["maximo"] else max(0.0, min(1.0, comp["nota"] / comp["maximo"]))
            st.progress(fr, text=f"Critério {comp['id']} · {comp['nome']}: {comp['nota']}/{comp['maximo']}")
            if comp.get("resumo"):
                st.caption(comp["resumo"][:400])
            if comp.get("evidencias") or comp.get("fragilidades"):
                with st.expander(f"🔎 Evidências e fragilidades — critério {comp['id']}"):
                    for e in comp.get("evidencias") or []:
                        st.markdown(f"- “{e}”")
                    for f_ in comp.get("fragilidades") or []:
                        st.markdown(f"- ⚠️ {f_}")
        if corr.get("comentario_geral"):
            with st.expander("💬 Comentário geral da banca"):
                st.markdown(corr["comentario_geral"])
        st.caption(
            "Modelos: "
            + " + ".join(x for x in (job.get("modelo_correcao"), job.get("modelo_tutor")) if x)
        )
    if aula:
        st.divider()
        st.markdown("#### 🎓 Aula do tutor")
        st.markdown(aula.get("aula_md") or "_(aula vazia)_")
        if aula.get("conceitos_estudar"):
            st.markdown("**Conceitos para estudar:**")
            st.markdown(" · ".join(f"`{c}`" for c in aula["conceitos_estudar"]))
        if aula.get("reescritura_sugerida"):
            with st.expander("✏️ Exemplos de reescrita"):
                for par in aula["reescritura_sugerida"]:
                    st.markdown(par)


def _render_envio(job: dict):
    """Painel de um envio: datas + pipeline + controles de fila + resultado (DB only)."""
    tema = job.get("tema") or {}
    st.caption(
        f"**{job['status']}** · {_nome_vestibular(tema.get('exame_label', ''))} · "
        f"Redação Q{tema.get('numero', '—')} · enviado {_fmt_data(job['criado_em'])} · "
        f"atualizado {_fmt_data(job['atualizado_em'])}"
    )
    st.markdown(_pipeline_html(job["status"]), unsafe_allow_html=True)
    status = job["status"]
    if status == "fila":
        st.info("⏳ Na fila — **pode fechar a página** quando quiser: o processamento continua no servidor.")
        if st.button("🚫 Cancelar envio", key=f"red_cancel_{job['id']}"):
            with connect() as con:
                ok = red_servico.cancelar(con, job["id"])
            st.toast(
                "Cancelado." if ok else "O worker já começou este job — não dá mais para cancelar.",
                icon="🚫" if ok else "⏱️",
            )
            st.rerun(scope="app")
    elif status == "corrigindo":
        st.warning("✍️ Rodada 1: a banca está corrigindo seu texto (até ~2 min). Pode fechar a página.")
    elif status == "erro":
        st.error(
            f"⚠️ Falhou na fase **{job['fase_erro'] or 'preparação'}** "
            f"(tentativa {job['tentativas']}): {job['erro'] or 'sem mensagem'}"
        )
        if st.button("🔁 Tentar de novo", key=f"red_retry_{job['id']}", type="primary"):
            with connect() as con:
                red_servico.tentar(con, job["id"])
            st.rerun(scope="app")
    if job.get("correcao"):
        if status == "aulando" and not job.get("aula"):
            st.info("📝 Notas já disponíveis! 🎓 O tutor ainda está escrevendo a aula — pode fechar e voltar depois.")
        _render_resultado(job)
    elif status == "erro" and not job.get("correcao"):
        st.caption("A correção ainda não foi concluída nenhuma vez para este envio.")


def _painel_redacao(usuario: str, envio_id: int | None):
    if envio_id is None:
        st.caption("Nenhum envio em andamento. Escreva uma redação e clique em **Enviar** para acompanhar aqui.")
        return
    with connect() as con:
        job = red_servico.detalhe(con, envio_id)
    if job is None:
        st.caption("Envio não encontrado.")
        return
    _render_envio(job)


@st.fragment(run_every=5)
def _painel_redacao_ativo(usuario: str, envio_id: int):
    """Auto-refresh (5 s) só enquanto há job não-terminal em exibição."""
    with connect() as con:
        job = red_servico.detalhe(con, envio_id)
    if job is None or job["status"] not in red_servico.STATUS_ATIVOS:
        st.rerun(scope="app")
        return
    _render_envio(job)


def modo_redacao():
    _iniciar_worker_redacao()
    usuario = st.session_state.get("usuario", "eu")
    try:
        criterios = red_criterios.carregar()
    except Exception as e:
        st.error(f"⚠️ Critérios de correção indisponíveis — {e}")
        return

    with connect() as con:
        temas = red_servico.listar_temas(con)
        ativos = red_servico.jobs_ativos(con, usuario)
    if not temas:
        st.warning("Nenhuma questão de redação no banco (rode a importação de questões).")
        return

    na_fila = [a for a in ativos if a["status"] == "fila"]
    if len(na_fila) >= 2:
        st.warning(f"⚠️ {len(na_fila)} envios na fila — serão processados em sequência (um por vez).")

    col_esq, col_dir = st.columns([1, 2], gap="large")

    with col_esq:
        st.subheader("1 · Escolha o tema")
        st.caption(
            "Correção com os critérios **UNIVESP/VUNESP 2026** (Manual do Candidato revisado; "
            "nota máxima 100). Temas de outros exames também podem ser corrigidos com esta rubrica — "
            "decisão sua."
        )
        sel = st.selectbox(
            "Tema",
            range(len(temas)),
            format_func=lambda i: (
                f"{_nome_vestibular(temas[i]['exame_label'])} · Q{temas[i]['numero']} — "
                + (temas[i]["enunciado"] or "")[:60].replace("\n", " ")
            ),
            key="red_tema",
            on_change=lambda: st.session_state.pop("red_painel_id", None),
        )
        tema = temas[sel]
        with connect() as con:
            ficha = red_servico.montar_tema(con, tema["id"])
        if ficha is None:
            st.error("Tema sumiu do banco.")
            return

        with st.container(border=True):
            if ficha["temas"]:
                for t in ficha["temas"]:
                    st.caption(f"🏷️ {t['area']} → {t['tema']}")
            for par in (ficha["enunciado"] or "").splitlines():
                if par.strip():
                    st.markdown(estilo.esc(par))
            if ficha["textos_de_apoio"]:
                with st.expander(f"📚 Textos de apoio (coletânea — {len(ficha['textos_de_apoio'])})"):
                    for i, ap in enumerate(ficha["textos_de_apoio"], 1):
                        st.markdown(f"**[{i}]** " + estilo.esc(ap))
                    for md in ficha["midia"] or []:
                        st.caption(f"Figura: {md}")
            pagina, bbox = _page_info(ficha["exame_label"], ficha["numero"])
            if (PAGES_DIR / ficha["exame_label"]).exists():
                with st.expander("📄 Página da prova (pan/zoom)"):
                    st.caption("Arraste para mover · roda/2 cliques para zoom · botões para enquadrar.")
                    view_page(ficha["exame_label"], pagina, bbox or [0, 0, 1000, 1000], height=520)

        st.subheader("2 · Escreva e envie")
        texto = st.text_area(
            "Sua redação",
            key=f"red_texto_{tema['id']}",
            height=340,
            placeholder="Escreva/cole aqui seu texto dissertativo-argumentativo (mínimo de 8 linhas autorais)…",
        )
        palavras = len((texto or "").split())
        linhas = max(0, (palavras + 7) // 8)
        cont = f"📝 **{palavras} palavra(s)** ≈ **{linhas} linhas** de folha oficial (~8 palavras/linha)."
        if palavras and linhas <= 7:
            cont += "  ⚠️ Menos de 8 linhas **anula** (regra H do manual)."
        st.markdown(cont)
        with st.popover("⚖️ Regras que zeram a redação (UNIVESP 2026)"):
            st.markdown("\n".join(f"- {r}" for r in criterios["regras_anulacao"]))

        fila_mesmo_tema = any(a["questao_id"] == tema["id"] for a in na_fila)
        pode_enviar = bool((texto or "").strip()) and not fila_mesmo_tema
        if st.button(
            "📨 Enviar para correção",
            type="primary",
            disabled=not pode_enviar,
            use_container_width=True,
            help="Pode fechar a página quando quiser: a correção roda no servidor e o resultado fica no histórico.",
        ):
            with connect() as con:
                envio_id = red_servico.enqueue(con, usuario, tema["id"], texto.strip())
            st.session_state["red_painel_id"] = envio_id
            st.toast("Na fila! Acompanhe no painel ao lado.", icon="📨")
            st.rerun(scope="app")
        if fila_mesmo_tema:
            st.caption("Já existe um envio deste tema na fila — aguarde ou cancele-o no painel.")

    with col_dir:
        st.subheader("3 · Status e resultado")
        alvo_id = st.session_state.get("red_painel_id")
        if alvo_id is None and ativos:
            alvo_id = ativos[0]["id"]
            st.session_state["red_painel_id"] = alvo_id
        alvo_ativo = alvo_id in {a["id"] for a in ativos} if alvo_id else False
        if alvo_ativo:
            _painel_redacao_ativo(usuario, alvo_id)
        else:
            _painel_redacao(usuario, alvo_id)

        with st.expander("🗂️ Histórico de envios"):
            with connect() as con:
                hist = red_servico.historico(con, usuario)
            if not hist:
                st.write("Nenhum envio ainda.")
            else:
                def _rotulo(h: dict) -> str:
                    nota = "—" if h["nota_total"] is None else f"{h['nota_total']:g}"
                    flag = " 🚫" if h["anulado"] else ""
                    return (
                        f"{_fmt_data(h['criado_em'])} · {_nome_vestibular(h['exame_label'])} Q{h['numero']}"
                        f" · {h['status']}{flag} · nota {nota} · id #{h['id']}"
                    )

                if "red_hist" in st.session_state and st.session_state["red_hist"] >= len(hist):
                    st.session_state["red_hist"] = 0
                esq = st.selectbox("Envio", range(len(hist)), format_func=lambda i: _rotulo(hist[i]), key="red_hist")
                reg = hist[esq]
                with connect() as con:
                    job = red_servico.detalhe(con, reg["id"])
                if job:
                    st.text_area(
                        "Texto enviado", value=job["texto"], height=160, key=f"red_hist_txt_{reg['id']}", disabled=True
                    )
                    b1, b2 = st.columns(2)
                    if b1.button("👁️ Abrir no painel", key=f"red_hist_ver_{reg['id']}"):
                        st.session_state["red_painel_id"] = reg["id"]
                        st.rerun(scope="app")

                    def _usar_base(eid: int):
                        """Callback (executa antes do rerun — pode escrever chaves
                        de widgets já instanciados nesta página)."""
                        with connect() as con:
                            jb = red_servico.detalhe(con, eid)
                        if not jb:
                            return
                        st.session_state[f"red_texto_{jb['questao_id']}"] = jb["texto"]
                        for i, t in enumerate(temas):
                            if t["id"] == jb["questao_id"]:
                                st.session_state["red_tema"] = i
                        st.session_state.pop("red_painel_id", None)
                        st.toast("Texto copiado para o editor.", icon="✏️")

                    b2.button(
                        "✏️ Usar como base",
                        key=f"red_hist_usar_{reg['id']}",
                        on_click=_usar_base,
                        args=(reg["id"],),
                    )


def _vencidos_hoje(usuario: str) -> list[dict]:
    """Temas do FSRS do usuário vencidos hoje ou atrasados (contagem >= portão)."""
    with connect() as con:
        rows = con.execute(
            """SELECT a.nome AS area, t.nome AS tema, f.vencimento AS venc
               FROM fsrs_estados f
               JOIN temas t ON t.id = f.tema_id
               JOIN areas a ON a.id = t.area_id
               JOIN niveis_usuarios n
                 ON n.tema_id = f.tema_id AND n.usuario = f.usuario
               WHERE f.usuario = ? AND date(f.vencimento) <= date(?)
                 AND n.contagem >= ?
               ORDER BY f.vencimento, a.nome, t.nome""",
            (usuario, fuso_hoje().isoformat(), MIN_TENTATIVAS_REVISAO),
        ).fetchall()
    return [dict(r) for r in rows]


def _aviso_vencidos(usuario: str):
    """Aviso no topo de todas as páginas quando há temas FSRS vencidos para hoje."""
    venc = _vencidos_hoje(usuario)
    if not venc:
        return
    hoje = fuso_hoje().isoformat()
    n_atras = sum(1 for v in venc if (v["venc"] or "")[:10] < hoje)
    n_hoje = len(venc) - n_atras
    partes = [f"{n_hoje} para hoje"]
    if n_atras:
        partes.append(f"{n_atras} atrasado(s)")
    lista = " · ".join(f"{v['area']} → {v['tema']}" for v in venc)
    st.warning(
        f"🔔 **Revisão FSRS:** {len(venc)} tema(s) vencido(s) ({', '.join(partes)}). "
        f"{lista}."
    )
    if st.button("Ir para a Revisão", key="btn_ir_revisao"):
        st.session_state["modo"] = "Revisão"
        st.rerun()


def main():
    estilo.injetar()
    _iniciar_worker_redacao()
    st.markdown(
        estilo.topbar_html(
            "Estudo Vestibular", "FUVEST · UNIVESP · ENEM · FATEC · UNESP"
        ),
        unsafe_allow_html=True,
    )
    _restaurar_do_url()
    _aviso_vencidos(st.session_state.get("usuario", "eu"))
    kwargs = {}
    if "modo" not in st.session_state:
        kwargs["default"] = "Estudar"
    modo = st.sidebar.pills(
        "Modo",
        ["Estudar", "Revisão", "Explorar", "Estatísticas", "Redação"],
        selection_mode="single",
        key="modo",
        **kwargs,
    )
    params = {
        "modo": modo,
        "usuario": st.session_state.get("usuario", "eu"),
        "label": "",
        "numero": "",
        "qid": "",
        "fb": "",
    }
    if modo in ("Estudar", "Revisão"):
        if modo == "Estudar":
            modo_estudar()
        else:
            modo_revisao()
        params["qid"] = st.session_state.get("params_qid", "")
        params["fb"] = st.session_state.get("params_fb", "")
    elif modo == "Explorar":
        modo_explorar()
        params["label"] = st.session_state.get("params_label", "")
        params["numero"] = st.session_state.get("params_numero", "")
    elif modo == "Redação":
        modo_redacao()
    else:
        modo_estatisticas()
    _sync_params(**params)


if __name__ == "__main__":
    main()
