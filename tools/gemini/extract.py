import json
import os
import re
import sys
import time

import requests
from google import genai

REST = "https://generativelanguage.googleapis.com/v1beta"
KEY = os.environ["API_KEY"]
MODELFALL = [os.environ.get("MODEL", "gemini-3.5-flash-lite"), "gemini-2.5-flash", "gemini-2.5-flash-lite"]
DATA = "/work/data"
OUT = "/work/data/json"
CATALOG = os.path.join(DATA, "assuntos.json")

PAGE_COUNTS = {
    "univesp_2017_2s": 24, "univesp_2018_1s": 28, "univesp_2018_2s": 24,
    "univesp_2019_2": 24, "univesp_2020": 18, "univesp_2021": 20,
    "univesp_2022": 20, "univesp_2023": 20, "univesp_2024": 24,
}

FUVEST_PAGES = {
    "fuvest_2010": 22, "fuvest_2011": 24, "fuvest_2012": 24, "fuvest_2013": 26,
    "fuvest_2014": 26, "fuvest_2015": 28, "fuvest_2016": 30, "fuvest_2017": 28,
    "fuvest_2018": 28, "fuvest_2019": 25, "fuvest_2020": 26, "fuvest_2021": 28,
    "fuvest_2022": 26, "fuvest_2023": 28, "fuvest_2024": 36, "fuvest_2025": 34,
    "fuvest_2026": 38,
}


def vestibular_context(label):
    if label.startswith("fuvest"):
        ano = int(re.search(r"(\d{4})", label).group(1))
        versao = "V1" if ano >= 2025 else "V"
        colunas = ("PROVA V1/PROVA V2/PROVA V3/PROVA V4" if ano >= 2025
                   else "PROVA V/PROVA K/PROVA Q/PROVA X/PROVA Z")
        return {
            "banca": "da FUVEST (USP) — Prova de Conhecimentos Gerais (1ª fase)",
            "n_questoes": "o caderno contém exatamente 90 questões objetivas, cada uma com 5 alternativas (a–e)",
            "redacao": "A redação é aplicada apenas na 2ª fase; nesta prova todas as questões são objetivas (não transcreva nenhuma 'redação').",
            "versao": versao,
            "colunas_gabarito": colunas,
        }
    return {
        "banca": "da UNIVESP (banca VUNESP)",
        "n_questoes": "",
        "redacao": "A redação (se o tema/coletânea aparecer nas páginas do intervalo) também deve ser transcrita, com \"tipo\": \"redacao\".",
        "versao": None,
        "colunas_gabarito": None,
    }


def page_count(label):
    if label.startswith("fuvest"):
        return FUVEST_PAGES.get(label, 24)
    return PAGE_COUNTS.get(label, 24)


def catalog_text():
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)
    lines = ["CATÁLOGO DE ÁREAS E ASSUNTOS (copie os strings EXATAMENTE):"]
    for d in cat["plano_de_estudos_vestibular"]["disciplinas"]:
        lines.append(f"- ÁREA: {d['area']}")
        for mod in d["modulos"]:
            for a in mod["assuntos"]:
                lines.append(f"    - {a}")
    return "\n".join(lines)


def upload(path):
    client = genai.Client(api_key=KEY)
    f = client.files.upload(file=path)
    return f.uri, f.name


def gen(model, parts, timeout=420):
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    last = None
    for i in range(7):
        try:
            r = requests.post(f"{REST}/models/{model}:generateContent",
                              headers={"x-goog-api-key": KEY, "Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = r.status_code
            if r.status_code in (429, 500, 503):
                s = 5 + 5 * i
                print(f"      retry {i} [{model}] after {s}s ({r.status_code})", flush=True)
                time.sleep(s)
            else:
                raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
        except requests.Timeout:
            last = "timeout"
            s = 5 + 5 * i
            print(f"      retry {i} [{model}] after {s}s (timeout)", flush=True)
            time.sleep(s)
    raise RuntimeError(f"{model} exhausted retries (last={last})")


def call_with_fallback(parts, label):
    errs = []
    for model in MODELFALL:
        try:
            out = gen(model, parts)
            cand = out["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
            return model, text
        except Exception as e:
            errs.append(str(e)[:120])
            print(f"[{label}] model {model} falhou: {str(e)[:120]}", flush=True)
    raise RuntimeError(f"[{label}] todas as tentativas falharam: {errs}")


def build_instruction(label, catalog, lo, hi, total, ctx):
    if ctx["versao"]:
        gab_rule = (f'Gabarito: o PDF de gabarito anexo contém as respostas das várias versões da prova em colunas separadas '
                    f'({ctx["colunas_gabarito"]}). USE SOMENTE a coluna correspondente à versão do caderno anexo: {ctx["versao"]}. '
                    f'Para cada questão objetiva, registre a letra (A–E) dessa coluna. A marca "*" na coluna indica questão ANULADA '
                    f'→ "gabarito": null e "anulada": true.')
    else:
        gab_rule = 'Gabarito: use o PDF de gabarito anexo para definir a alternativa correta (A–E) de cada questão objetiva por número. Questões anuladas: "gabarito": null e "anulada": true.'
    return f"""
Você é um engenheiro de dados especializado em provas {ctx['banca']}. Leia os DOIS PDFs anexos: caderno de questões ("{label}_questoes.pdf", {total} páginas) e gabarito oficial ("{label}_gabarito.pdf").

SCOPE: transcreva as questões do caderno localizadas nas PÁGINAS {lo} a {hi} do PDF de questões (contagem de páginas do arquivo, 1 = capa). Inclua integralmente toda questão que OCORRA nessas páginas, mesmo que continue na página seguinte. Não pegue questões que só aparecem em páginas fora desse intervalo. {ctx['redacao']}

REGRAS DE TRANSCRIÇÃO (rigorosas — NUNCA invente conteúdo):
- Transcrição INTEGRAL e fiel dos enunciados, sem resumir, corrigir ou adaptar. Fórmulas em unicode (x², √2, π, Δ, 10⁻³, frações a/b).
- Textos de apoio/motivadores/citações/coletâneas: transcreva-os ÍNTEGROS no campo "textos_de_apoio".
- Figuras/gráficos/tabelas/imagens/cartuns: registre em "midia" uma lista com descrição objetiva do que está representado (inclua dados extraíveis), cada item precedido de "Página N: ...", onde N é a página do PDF.
- Alternativas transcritas na ordem (a, b, c, d, e), sem alterar texto.
- Trecho ilegível → "[ilegivel]" no lugar exato e "extraida_parcialmente": true.
- {gab_rule}

CLASSIFICAÇÃO:
- Áreas: nomes canônicos do catálogo. Liste TODAS as áreas efetivamente cobradas para RESOLVER a questão (interdisciplinaridade), não áreas só citadas. Padrão: interpretação de texto sem outra área → "Língua Portuguesa e Literaturas"; questão de língua estrangeira → "Língua Inglesa".
- Assuntos: 1–3 por área, por proximidade SEMÂNTICA/temática, copiando o string EXATAMENTE do catálogo.

COORDENADAS DE FIGURAS:
- Para cada figura/gráfico/tabela/imagem/cartum da questão, adicione em "figuras_coordenadas": {{"pagina": <N>, "tipo": "figura|grafico|tabela|imagem|cartum", "elemento": "<curta descrição>", "bbox": [x0, y0, x1, y1]}} com coordenadas em percentual da largura/altura da página (0–100), origem canto superior esquerdo (aproxime pela inspeção visual).

CATÁLOGO (strings exatas):
{catalog}

SCHEMA (responda SOMENTE com JSON válido, sem markdown):
{{
  "questoes": [
    {{
      "numero": 1,
      "tipo": "objetiva",
      "enunciado": "...",
      "textos_de_apoio": ["..."],
      "midia": ["Página N: descrição objetiva."],
      "alternativas": {{"a": "...", "b": "...", "c": "...", "d": "...", "e": "..."}},
      "gabarito": "c",
      "areas": [{{"area": "Física", "assuntos": ["...", "..."]}}],
      "extraida_parcialmente": false,
      "anulada": false,
      "figuras_coordenadas": []
    }}
  ],
  "semestre_no_cabecalho": null
}}
- Não omita nenhuma questão do intervalo de páginas. Não duplique questões.
- "semestre_no_cabecalho": extraia do cabeçalho do caderno ou gabarito (1/2) se explícito para provas semestrais, senão null.
"""


def parse_label(label):
    ano = int(re.search(r"(\d{4})", label).group(1))
    sem = None
    if label.endswith("_2s") or (re.search(r"_\d$", label) and label.endswith("2")):
        sem = 2
    elif label.endswith("_1s"):
        sem = 1
    return ano, sem


def merge_chunks(chunks, label, ano, sem):
    seen = {}
    for chunk in chunks:
        for q in chunk["questoes"]:
            n = q["numero"]
            if n not in seen:
                seen[n] = q
            else:
                old, new = seen[n], q
                if len(str(new.get("enunciado", ""))) > len(str(old.get("enunciado", ""))):
                    seen[n] = new
                seen[n]["figuras_coordenadas"] = old.get("figuras_coordenadas", []) or new.get("figuras_coordenadas", [])
    nums = sorted(seen)
    return nums, [seen[n] for n in nums]


def main():
    label = sys.argv[1]
    if label.endswith("_questoes"):
        label = label[:-len("_questoes")]
    ano, sem_label = parse_label(label)
    os.makedirs(OUT, exist_ok=True)
    cat = catalog_text()

    print(f"[{label}] uploading...", flush=True)
    qu, _ = upload(f"{DATA}/{label}_questoes.pdf")
    gu, _ = upload(f"{DATA}/{label}_gabarito.pdf")
    print(f"[{label}] uploaded.", flush=True)

    total_pages = page_count(label)
    default_step = max(1, (total_pages + 1) // 2) if label.startswith("fuvest") else total_pages
    step = int(os.environ.get("STEP", default_step))
    ctx = vestibular_context(label)
    print(f"[{label}] modos: step={step} modelo={MODELFALL[0]} versao={ctx['versao']}", flush=True)
    chunks = []
    sem_node = None
    for lo in range(1, total_pages + 1, step):
        hi = min(lo + step - 1, total_pages)
        parts = [
            {"text": build_instruction(label, cat, lo, hi, total_pages, ctx)},
            {"file_data": {"file_uri": qu, "mime_type": "application/pdf"}},
            {"file_data": {"file_uri": gu, "mime_type": "application/pdf"}},
        ]
        t0 = time.time()
        model, text = call_with_fallback(parts, label)
        try:
            data = json.loads(text[text.index("{"): text.rindex("}") + 1])
            qs = data["questoes"]
            if "semestre_no_cabecalho" in data and data["semestre_no_cabecalho"]:
                sem_node = data["semestre_no_cabecalho"] if sem_node is None else sem_node
            if not qs:
                print(f"[{label}] pages {lo}-{hi}: SEM QUESTOES (model {model})", flush=True)
                continue
            nums = [q["numero"] for q in qs]
            print(f"[{label}] pages {lo}-{hi} [{model} {time.time()-t0:.0f}s]: {len(qs)} questões {min(nums)}..{max(nums)}", flush=True)
            chunks.append(data)
        except Exception as e:
            print(f"[{label}] CHUNK {lo}-{hi} INVALIDO ({model}): {e} | text head: {text[:200]}", flush=True)

    nums, ordered = merge_chunks(chunks, label, ano, sem_label)
    if not ordered:
        raise SystemExit(f"[{label}] nenhuma questão extraída")
    missing = [n for n in range(1, max(nums) + 1) if n not in nums]
    if missing:
        print(f"[{label}] AVISO: questão(ns) ausente(s): {missing}", flush=True)

    sem = sem_node or sem_label
    out = {
        "exame": f"{label}_questoes",
        "ano": ano,
        "semestre": sem,
        "fonte_questoes": f"data/{label}_questoes.pdf",
        "fonte_gabarito": f"data/{label}_gabarito.pdf",
        "total_questoes": len(ordered),
        "questoes": ordered,
    }
    figuras = {}
    for q in ordered:
        fc = q.pop("figuras_coordenadas", None)
        if fc:
            figuras[str(q["numero"])] = fc
        q.pop("pagina", None)
    outpath = os.path.join(OUT, f"{label}_questoes.json")
    with open(outpath, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    sidecar = os.path.join(OUT, f"{label}_imagens.json")
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump({"exame": label, "figuras_coordenadas": figuras}, fh, ensure_ascii=False, indent=2)
    print(f"[{label}] SAVED {outpath} — {len(ordered)} questões; figuras {len(figuras)}; semestre {sem}", flush=True)


if __name__ == "__main__":
    main()