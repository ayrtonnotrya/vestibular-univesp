"""Relatório de estudo em Markdown: visão geral, evolução, caderno de erros,
fila FSRS, lacunas e recomendações (análise de `tmp/analise_caderno.py`)."""

import datetime as dt
import sqlite3
from collections import Counter, defaultdict

import estatisticas as est
from vestibular.estudo.fuso import agora as fuso_agora


def gerar_markdown(
    con: sqlite3.Connection, usuario: str, agora: dt.datetime | None = None
) -> str:
    """Gera o relatório consolidado do usuário em Markdown."""
    agora = agora or fuso_agora()
    L = []

    def w(linha=""):
        L.append(linha)

    w("# Relatório de Estudo — Vestibulares UNIVESP")
    w()
    w(f"_Gerado em {agora.astimezone().strftime('%d/%m/%Y %H:%M')} (horário de São Paulo)._")
    w()

    # ---------------- resumo ----------------
    res = est.resumo(con, usuario, agora)
    w("## 1. Visão geral")
    w()
    w("| Métrica | Valor |")
    w("|---|---|")
    w(f"| Tentativas totais | {res['total']} |")
    w(f"| Questões distintas | {res['distintas']} |")
    w(f"| Acertos | {res['acertos']} ({(100*res['acertos']/res['total']) if res['total'] else 0:.1f}%) |")
    w(f"| Erros | {res['erros']} |")
    w(f"| Anuladas | {res['anuladas']} |")
    w(f"| Aproveitamento (acertos/tentativas) | {res['pct']}% |")
    w(f"| Dificuldade média das questões tentadas (b) | {res['b_medio']} |")
    w(f"| Período | {res['primeira']} → {res['ultima']} |")
    w()

    # sessão atual
    sess = con.execute("SELECT * FROM sessoes WHERE usuario=?", (usuario,)).fetchone()
    if sess:
        w(f"Sessão atual: modo **{sess['modo']}** (última atualização {sess['atualizado_em'][:16]}).")
        w()

    # ---------------- por dia ----------------
    w("## 2. Evolução por dia")
    w()
    por_dia = est.por_dia(con, usuario)
    w("| Dia | Tentativas | Acertos | % |")
    w("|---|---|---|---|")
    tot_d, ac_d = 0, 0
    for e in por_dia:
        tot_d += e["tentativas"]
        ac_d += e["acertos"]
        w(f"| {e['dia']} | {e['tentativas']} | {e['acertos']} | {e['pct']}% |")
    w()
    dias = [e["dia"] for e in por_dia]
    if len(dias) >= 2:
        w(f"Última sessão: **{dias[-1]}** com {por_dia[-1]['tentativas']} tentativas ({por_dia[-1]['pct']}%).")
        w()

    # ---------------- por vestibular / exame ----------------
    w("## 3. Desempenho por vestibular e exame")
    w()
    por_vest = est.por_vestibular(con, usuario)
    w("| Vestibular | Tentativas | Acertos | % |")
    w("|---|---|---|---|")
    for e in por_vest:
        w(f"| {e['vestibular']} | {e['tentativas']} | {e['acertos']} | {e['pct']}% |")
    w()
    por_exame = est.por_exame(con, usuario)
    w("| Exame | Tentativas | Acertos | % |")
    w("|---|---|---|---|")
    for e in por_exame:
        w(f"| {e['exame']} | {e['tentativas']} | {e['acertos']} | {e['pct']}% |")
    w()

    # ---------------- por área ----------------
    w("## 4. Habilidade por área (Rasch θ)")
    w()
    por_area = est.por_area(con, usuario)
    w("| Área | θ | Observações | Acertos reais | % |")
    w("|---|---|---|---|---|")
    for e in por_area:
        if e["tentativas"]:
            w(f"| {e['area']} | {e['theta']} | {e['n_obs']} | {e['acertos']}/{e['tentativas']} | {e['pct']}% |")
    w()

    # ---------------- por tema ----------------
    w("## 5. Níveis por tema (score, racha, estado FSRS)")
    w()
    por_tema = est.por_tema(con, usuario, agora)
    w("| Área | Tema | Fase | Score | Racha | Tent. | Estado | Lapses | Revisão |")
    w("|---|---|---|---|---|---|---|---|---|")
    for e in por_tema:
        w(f"| {e['area']} | {e['tema']} | {e['fase'] or '—'} | {e['score']} | {e['racha']} | {e['tentativas']} | {e['estado']} | {e['lapses']} | {e['vencimento']} |")
    w()

    # temas mais fracos (score baixo com >=2 tentativas)
    fracos = [e for e in por_tema if e["tentativas"] >= 2]
    fracos.sort(key=lambda x: (x["score"], -x["tentativas"]))
    if fracos:
        w("**Temas mais fracos** (pior score, ≥2 tentativas):")
        w()
        for e in fracos[:10]:
            w(f"- **{e['tema']}** ({e['area']}{', fase ' + str(e['fase']) if e['fase'] else ''}): score {e['score']}, {e['tentativas']} tent., racha {e['racha']}, lapses {e['lapses']}")
        w()

    # ---------------- b vs theta ----------------
    w("## 6. Dificuldade da prova vs habilidade (desafio)")
    w()
    bv = est.b_vs_theta(con, usuario)
    w("| Área | b médio | θ | Δ (θ−b) | N |")
    w("|---|---|---|---|---|")
    for e in bv:
        w(f"| {e['area']} | {e['b_medio']} | {e['theta']} | {e['delta']} | {e['n']} |")
    w()
    w("Δ positivo = questões abaixo do seu nível (zona de conforto/JIT); Δ negativo = questões acima do nível (desafio saudável).")
    w()

    # ---------------- caderno de erros ----------------
    w("## 7. Caderno de erros (análise)")

    rows = con.execute(
        """SELECT t.id, t.data, t.questao_id, q.exame_label, q.numero, t.resposta,
                  q.gabarito, t.grau_certeza, t.causa_erro, t.sintese_ativa, q.anulada
           FROM tentativas t JOIN questoes q ON q.id = t.questao_id
           WHERE t.usuario=? AND t.correta=0
           ORDER BY t.data DESC""",
        (usuario,),
    ).fetchall()

    temas_q = defaultdict(list)
    for r in con.execute(
        """SELECT c.questao_id, a.nome AS area, t.nome AS tema
           FROM classificacoes c
           JOIN temas t ON t.id = c.tema_id
           JOIN areas a ON a.id = t.area_id"""
    ):
        temas_q[r["questao_id"]].append((r["area"], r["tema"]))

    q_ids = {r["id"]: r for r in con.execute("SELECT id, exame_label, numero FROM questoes")}

    erros = list(rows)
    w()
    w(f"Total de erros registrados: **{len(erros)}**.")
    w()

    # erros repetidos (mesma questão errada mais de uma vez)
    rep = Counter((t["questao_id"]) for t in erros)
    reps = {q: n for q, n in rep.items() if n > 1}
    if reps:
        w("**Questões erradas mais de uma vez** (sinal de lacuna não consolidada):")
        w()
        for qid, n in sorted(reps.items(), key=lambda x: -x[1]):
            info = temas_q.get(qid, [("?", "?")])
            q = q_ids.get(qid)
            rot = f"{q['exame_label']} Q{q['numero']}" if q else f"qid {qid}"
            w(f"- {n}x — **{rot}** — {', '.join(f'{a}/{t}' for a, t in info)}")
        w()

    # causa de erro
    w("**Distribuição por causa de erro:**")
    w()
    cc = Counter(t["causa_erro"] or "(sem registro)" for t in erros)
    for causa, n in cc.most_common():
        w(f"- {causa}: {n}")
    w()

    # grau de certeza
    w()
    w("**Distribuição por grau de certeza (nos erros):**")
    w()
    gc = Counter(t["grau_certeza"] or "(sem registro)" for t in erros)
    for g, n in gc.most_common():
        w(f"- {g}: {n}")
    w()

    # erro com convicção (ergo pegadinha/falta técnica silenciosa)
    convictos = [t for t in erros if t["grau_certeza"] == "conviccao"]
    w(f"Erros com **convicção** (achou que sabia): **{len(convictos)}** — os mais perigosos.")
    if convictos:
        for t in convictos[:8]:
            info = " / ".join(f"{a}:{tm}" for a, tm in temas_q.get(t["questao_id"], []))
            w(f"  - {t['exame_label']} Q{t['numero']} (respondeu {t['resposta']}, gab. {t['gabarito']}) — {info}")
    w()

    # erros por área/tema
    w()
    w("**Erros por área:**")
    w()
    ea = Counter()
    for t in erros:
        for a, tm in temas_q.get(t["questao_id"], []):
            ea[a] += 1
    for a, n in ea.most_common():
        w(f"- {a}: {n}")
    w()
    w("**Erros por tema (top 10):**")
    w()
    et = Counter()
    for t in erros:
        for a, tm in temas_q.get(t["questao_id"], []):
            et[tm] += 1
    for tm, n in et.most_common(10):
        w(f"- {tm}: {n}")
    w()

    # sintese ativa presentes?
    sint = [t for t in erros if t["sintese_ativa"]]
    w(f"**Síntese ativa** registrada em {len(sint)} dos {len(erros)} erros ({100*len(sint)/len(erros) if erros else 0:.0f}%).")
    w()

    # detalhe dos últimos erros
    w("**Últimos 15 erros na ordem cronológica:**")
    w()
    w("| Data | Exame | Q | Resposta | Gabarito | Certeza | Causa | Área/Tema |")
    w("|---|---|---|---|---|---|---|---|")
    for t in erros[:15]:
        info = " / ".join(f"{a}·{tm}" for a, tm in temas_q.get(t["questao_id"], [])) or "—"
        gab = (t["gabarito"] or "—").upper()
        resp = (t["resposta"] or "—").upper()
        w(f"| {t['data'][:10]} | {t['exame_label']} | {t['numero']} | {resp} | {gab} | {t['grau_certeza'] or '—'} | {t['causa_erro'] or '—'} | {info} |")
    w()

    # ---------------- FSRS / revisão ----------------
    w("## 8. Fila de revisão FSRS e retenção")
    w()
    rev = est.revisoes(con, usuario, agora)
    vencidos = [x for x in rev if x["status"] in ("atrasada", "hoje")]
    w(f"Temas com revisão devida (vencidos + hoje): **{len(vencidos)}**")
    w()
    if vencidos:
        w("| Área | Tema | Estado | Repos | Lapses | Vencimento |")
        w("|---|---|---|---|---|---|")
        for x in vencidos:
            w(f"| {x['area']} | {x['tema']} | {x['estado']} | {x['repos']} | {x['lapses']} | {x['vencimento'][:10]} |")
    w()
    prilig = [x for x in rev if x["status"] == "próxima"]
    w(f"Próximas revisões: {len(prilig)}")
    w()
    ret = est.retencao(con, usuario, agora)
    if ret:
        w("**Temas com menor retenção (R)** — mais próximos do esquecimento:")
        w()
        w("| Área | Tema | R | Estado | Lapses |")
        w("|---|---|---|---|---|")
        for x in ret[:8]:
            w(f"| {x['area']} | {x['tema']} | {x['r']} | {x['estado']} | {x['lapses']} |")
        w()

    # ---------------- gaps ----------------
    w("## 9. Lacunas — temas UNIVESP ainda não iniciados")
    w()
    gaps = est.gaps(con, usuario)
    tot_gap = len(gaps)
    w(f"Temas cobrados na UNIVESP sem nenhuma tentativa sua: **{tot_gap}**")
    w()
    w("**Mais cobrados primeiro (top 15):**")
    w()
    w("| Área | Tema | Fase | Questões na banca |")
    w("|---|---|---|---|")
    for g in gaps[:15]:
        w(f"| {g['area']} | {g['tema']} | {g['fase'] or '—'} | {g['questoes_banca']} |")
    w()

    # ---------------- cobertura fase ----------------
    w("## 10. Cobertura da UNIVESP por fase")
    w()
    cov = est.cobertura_fase(con, usuario)
    w("| Área | Fase | Temas | Questões banca | Tentadas | Cobertura |")
    w("|---|---|---|---|---|---|")
    for e in cov:
        w(f"| {e['area']} | {e['fase']} | {e['n_temas']} | {e['questoes_banca']} | {e['tentadas']} | {e['cobertura']}% |")
    w()

    # ---------------- conclusões ----------------
    w("## 11. Síntese e recomendações")
    w()

    def _curto(texto, tam=52):
        return texto if len(texto) <= tam else texto[:tam].rsplit(" ", 1)[0] + "…"

    def _parse_dia(d):
        dd, mm = (int(x) for x in d.split("/"))
        fim = dt.date.fromisoformat(res["ultima"][:10])
        dia = dt.date(fim.year, mm, dd)
        if dia > fim:
            dia = dt.date(fim.year - 1, mm, dd)
        return dia

    # ritmo: streak corrente (dias consecutivos até a última sessão)
    datas = sorted(_parse_dia(e["dia"]) for e in por_dia)
    streak = 1 if len(datas) > 1 else len(datas)
    for novo, ant in zip(reversed(datas), reversed(datas[:-1])):
        if (novo - ant).days == 1:
            streak += 1
        else:
            break
    melhor_dia = max((e for e in por_dia if e["tentativas"] >= 3), key=lambda e: (e["pct"], e["tentativas"]), default=None)

    w("### Pontos fortes")
    w()

    def _ddmmaa(iso):
        return f"{iso[8:10]}/{iso[5:7]}"

    if len(datas) >= 2:
        ritmo_seq = f"{streak} dia{'s' if streak > 1 else ''} seguid{'os' if streak > 1 else 'o'}" if streak > 1 else f"{len(por_dia)} dias alternados"
        linha_ritmo = (
            f"- **Ritmo**: {ritmo_seq} de estudo (última sessão {dias[-1]}); "
            f"{len(por_dia)} dias ativos de {_ddmmaa(res['primeira'])} → {_ddmmaa(res['ultima'])}, "
            f"média de {res['total']/len(por_dia):.0f} tentativas/dia"
        )
        if melhor_dia:
            linha_ritmo += (
                f"; melhor dia {melhor_dia['dia']} com {melhor_dia['pct']}% "
                f"({melhor_dia['acertos']}/{melhor_dia['tentativas']})"
            )
        w(linha_ritmo + ".")
        fortes = sorted(
            (e for e in por_area if e["n_obs"] >= 5 and e["pct"] >= 70),
            key=lambda e: -e["theta"],
        )[:2]
        if fortes:
            w("- **Áreas em alta**: " + ", ".join(
                f"{e['area']} (θ {e['theta']}, {e['pct']}% em {e['tentativas']} tentativas)" for e in fortes
            ) + ".")
        pos_delta = [e for e in bv if e["delta"] > 0]
        if pos_delta:
            w(f"- **Dificuldade no alcance**: Δ = θ−b positivo em {len(pos_delta)} de {len(bv)} áreas.")
    elif res["total"]:
        w(f"- Sessões registradas: {len(por_dia)} dias, {res['total']} tentativas.")

    w(f"- **Aproveitamento geral**: {res['pct']}% ({res['acertos']} acertos em {res['total']} tentativas).")

    # clusters de erro por área/tema
    et2 = Counter()
    for t in erros:
        for a, tm in temas_q.get(t["questao_id"], []):
            et2[(a, tm)] += 1
    tscore = {(e["area"], e["tema"]): e for e in por_tema}
    clusters = [(a, n) for a, n in ea.most_common(3) if a != "Redação"]

    w()
    w("### Pontos fracos (prioridade 1)")
    w()
    ORDERAIS = ["o maior", "o 2º", "o 3º"]
    for ci, (area_erro, n_erro) in enumerate(clusters):
        temas_erro = sorted(
            ((tm, n) for (a, tm), n in et2.items() if a == area_erro),
            key=lambda x: -x[1],
        )[:3]
        if temas_erro:
            w(f"- **{area_erro} = {ORDERAIS[ci]} cluster de erros**: {n_erro} erros concentrados em "
              + ", ".join(f"{_curto(tm, 40)} ({n})" for tm, n in temas_erro) + ".")
            partes = []
            for tm, n in temas_erro:
                st = tscore.get((area_erro, tm))
                if st:
                    partes.append(
                        f"{_curto(tm, 36)} — score {st['score']}, {st['tentativas']} tent., {st['lapses']} lapses"
                    )
            if partes:
                w("  - " + "; ".join(partes) + ".")
        else:
            w(f"- **{area_erro}**: {n_erro} erros.")
    if reps:
        rot_reps = []
        for qid, n in sorted(reps.items(), key=lambda x: -x[1])[:5]:
            q = q_ids.get(qid)
            if q:
                rot_reps.append(f"{q['exame_label']} Q{q['numero']} ({n}x)")
        w(f"- **Lacunas não consolidadas**: {len(reps)} questões erradas mais de uma vez — {', '.join(rot_reps)}.")
    n_chute = gc.get("chute", 0)
    if erros and n_chute:
        w(f"- **Chute nos erros**: {n_chute} de {len(erros)} erros ({100*n_chute/len(erros):.0f}%) foram `chute`"
          + (f" e apenas {gc.get('duvida', 0)} `duvida`" if gc.get("duvida") is not None else "")
          + " — ao chutar, a resposta raramente estava na teoria dominada.")
    if convictos:
        rot_conv = ", ".join(
            f"{t['exame_label']} Q{t['numero']} ({_curto((temas_q.get(t['questao_id']) or [('?', '?')])[0][1], 36)})"
            for t in convictos[:6]
        )
        w(f"- **{len(convictos)} erros com convicção** (pareciam dominados): {rot_conv} — ferimentos de 'pegadinha': "
          "as alternativas diferem por detalhe técnico.")
    uni = next((e for e in por_vest if e["vestibular"] == "UNIVESP"), None)
    rasas = [e for e in cov if e["cobertura"] < 20 and e["questoes_banca"] >= 10]
    if uni is not None and gaps:
        w(f"- **Cobertura da UNIVESP ainda rasa**: o vestibular-alvo tem {uni['tentativas']} tentativas; "
          f"{tot_gap} temas da banca ainda sem nenhuma tentativa"
          + (f" e {len(rasas)} pares área/fase com cobertura <20% e ≥10 questões no banco" if rasas else "") + ".")

    w()
    w("### Fila de revisão imediata")
    w()
    if vencidos:
        for x in sorted(vencidos, key=lambda e: -e["lapses"]):
            w(f"- **{_curto(x['tema'])}** ({x['area']}) — {x['status']}, {x['lapses']} lapses, estado `{x['estado']}`")
        atras = [x for x in vencidos if x["status"] == "atrasada"]
        if atras:
            w(f"- {len(atras)} tema(s) `atrasado(s)` → prioridade máxima na fila do modo Revisão.")
    else:
        w("- Nenhum tema vencido hoje; mantenha o modo Revisão quando a fila acusar pendências.")
    rel = [x for x in rev if x["estado"] == "relearning"]
    if rel:
        rot = ", ".join(_curto(x["tema"], 40) + f" ({x['area']})" for x in rel)
        w(f"- Em `relearning`: {rot}.")

    if res["total"] and (clusters or gaps):
        w()
        w("### Sugestão de próximos passos")
        w()
        passos = []
        if clusters:
            area0 = clusters[0][0]
            t0 = [tm for (a, tm), n in et2.most_common() if a == area0][:3]
            msg = f"**Fechar {area0}** antes de ampliar"
            if t0:
                msg += f": atacar {', '.join(_curto(tm, 36) for tm in t0)}"
            if reps:
                msg += f" e refazer as {len(reps)} questões erradas mais de uma vez"
            passos.append(msg + ".")
        if len(clusters) > 1:
            area1 = clusters[1][0]
            t1 = [_curto(tm, 36) for (a, tm), n in et2.most_common() if a == area1][:3]
            if t1:
                passos.append(f"**{area1}**: {', '.join(t1)} — prioridade na distribuição de temas do modo Estudar.")
        if gaps:
            passo_uni = (
                f"**Migrar volume para a UNIVESP** (alvo principal): atacar os temas mais cobrados entre os "
                f"{tot_gap} ainda não iniciados — "
                + ", ".join(_curto(g["tema"], 36) for g in gaps[:5])
                + "."
            )
            passos.append(passo_uni)
        red_gaps = [g for g in gaps if g["area"] == "Redação"]
        if red_gaps:
            passos.append(
                f"**Redação**: {len(red_gaps)} temas da banca sem nenhuma tentativa "
                f"({sum(g['questoes_banca'] for g in red_gaps)} questões cobradas) — use a aba Redação do app."
            )
        sem_causa = cc.get("(sem registro)", 0)
        if erros and sem_causa:
            causas = [(c, n) for c, n in cc.most_common() if c != "(sem registro)"]
            msg = (
                f"**Causa de erro**: {sem_causa} dos {len(erros)} erros sem causa registrada — anotar "
                "`teoria|pegadinha|atencao` em cada erro melhora o diagnóstico"
            )
            if causas:
                msg += (
                    f"; hoje `{causas[0][0]}` domina ({causas[0][1]}), sugerindo estudo conceitual além de "
                    "resolver questões"
                )
            passos.append(msg + ".")
        for i, p in enumerate(passos, 1):
            w(f"{i}. {p}")

    w()
    w("_Relatório gerado automaticamente com base em `data/vestibular.db`._")
    w()

    return "\n".join(L)
