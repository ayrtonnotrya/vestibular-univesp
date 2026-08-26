import json
import os
import re
import sys
import time

import requests
from google import genai

REST = "https://generativelanguage.googleapis.com/v1beta"
MODELFALL = [os.environ.get("MODEL", "gemini-3.5-flash-lite"), "gemini-2.5-flash", "gemini-2.5-flash-lite"]
DATA = "/work/data"
OUT = "/work/data/json"
CATALOG = os.path.join(DATA, "assuntos.json")


def _load_keys():
    env_keys = [k.strip() for k in os.environ.get("GEMINI_KEYS", "").split(",") if k.strip()]
    if not env_keys:
        try:
            for line in open("/work/.env", encoding="utf-8"):
                line = line.strip()
                if line.startswith("GEMINI_KEYS="):
                    env_keys = [k.strip() for k in line.split("=", 1)[1].split(",") if k.strip()]
                    break
        except OSError:
            pass
    if not env_keys:
        env_keys = [os.environ["API_KEY"] if os.environ.get("API_KEY") else ""]
    global KEYS
    KEYS = [k for k in env_keys if k]
    return KEYS


KEYS = _load_keys()
_key_idx = os.getpid() % len(KEYS) if KEYS else 0


def next_key():
    global _key_idx
    k = KEYS[_key_idx % len(KEYS)]
    _key_idx += 1
    return k


def key_idx_of(key):
    try:
        return KEYS.index(key) + 1
    except ValueError:
        return 0

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
    low = label.lower()
    if low.startswith("fuvest"):
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
    if low.startswith("enem"):
        dia = 2 if low.endswith("2dia") else 1
        return {
            "banca": "do ENEM (INEP)",
            "n_questoes": f"o caderno do {dia}º dia contém 45 questões objetivas, cada uma com 5 alternativas (a–e)",
            "redacao": "No caderno do 2º dia há também a proposta de redação (tema + coletânea): transcreva-a como questão com "
                        "\"tipo\": \"redacao\", SEM \"gabarito\" e SEM \"alternativas\" (deixe ambos ausentes do JSON), numerada logo após as objetivas." if dia == 2
                        else "Nesta prova não há redação; transcreva apenas as questões objetivas.",
            "versao": None,
            "colunas_gabarito": None,
        }
    if low.startswith("fatec"):
        return {
            "banca": "da FATEC (Centro Paula Souza)",
            "n_questoes": "o caderno contém 54 questões objetivas (30 de Conhecimentos Gerais com 5 alternativas a–e e as demais com 4 alternativas a–d) e uma proposta de redação; transcreva a redação como \"tipo\": \"redacao\".",
            "redacao": "Transcreva também a proposta de redação (tema + coletânea/instruções), com \"tipo\": \"redacao\", SEM \"gabarito\" e SEM \"alternativas\" (deixe ambos ausentes do JSON).",
            "versao": None,
            "colunas_gabarito": None,
        }
    if low.startswith("unesp"):
        return {
            "banca": "da UNESP (banca VUNESP)",
            "n_questoes": "o caderno contém 90 questões objetivas, cada uma com 5 alternativas (a–e), ou 45 em cada dia quando a prova é aplicada em dois dias (1dia/2dia)",
            "redacao": "A redação é aplicada apenas na 2ª fase (ou em dia específico); se o tema/coletânea aparecer nas páginas, transcreva-a como \"tipo\": \"redacao\", SEM \"gabarito\" e SEM \"alternativas\".",
            "versao": None,
            "colunas_gabarito": None,
        }
    return {
        "banca": "da UNIVESP (banca VUNESP)",
        "n_questoes": "",
        "redacao": "A redação (se o tema/coletânea aparecer nas páginas do intervalo) também deve ser transcrita, com \"tipo\": \"redacao\", SEM \"gabarito\" e SEM \"alternativas\".",
        "versao": None,
        "colunas_gabarito": None,
    }


def pdf_pages(path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return None


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


def pdf_path(label, kind):
    for base in (DATA, os.environ.get("PDF_DIR", ""), "/work/tmp/bkp_pdfs"):
        if not base:
            continue
        p = os.path.join(base, f"{label}_{kind}.pdf")
        if os.path.exists(p):
            return p
    return None


def upload(path, key):
    client = genai.Client(api_key=key)
    f = client.files.upload(file=path)
    return f.uri, f.name


def gen(model, parts, key, timeout=420):
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    last = None
    for i in range(7):
        try:
            r = requests.post(f"{REST}/models/{model}:generateContent",
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = r.status_code
            if r.status_code in (429, 500, 503):
                s = 5 + 5 * i
                print(f"      retry {i} [{model}] chave{key_idx_of(key)} after {s}s ({r.status_code})", flush=True)
                time.sleep(s)
            else:
                raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
        except requests.Timeout:
            last = "timeout"
            s = 5 + 5 * i
            print(f"      retry {i} [{model}] chave{key_idx_of(key)} after {s}s (timeout)", flush=True)
            time.sleep(s)
    raise RuntimeError(f"{model} exhausted retries (last={last})")


def call_with_fallback(parts, label, key):
    errs = []
    for model in MODELFALL:
        try:
            out = gen(model, parts, key)
            cand = out["candidates"][0]
            resp_parts = (cand.get("content") or {}).get("parts", []) or []
            text = "".join(p.get("text", "") for p in resp_parts)
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
- TEXTO COMUM A VÁRIAS QUESTÕES (regra CRÍTICA): quando um mesmo texto/figura/tabela/instrução/coletânea servir de base para DUAS OU MAIS questões (ex.: "Leia o texto a seguir para responder às questões de 1 a 3"), transcreva esse material INTEGRALMENTE no campo "textos_de_apoio" de CADA UMA das questões que dele dependem. O texto comum DEVE aparecer repetido em todas as questões do grupo — nunca omita, não divida entre as questões, não deixe só na primeira.
- Figuras/gráficos/tabelas/imagens/cartuns: registre em "midia" uma lista com descrição objetiva do que está representado (inclua dados extraíveis), cada item precedido de "Página N: ...", onde N é a página do PDF.
- Alternativas transcritas na ordem (a, b, c, d, e), sem alterar texto.
- Trecho ilegível → "[ilegivel]" no lugar exato e "extraida_parcialmente": true.
- {gab_rule}

CLASSIFICAÇÃO:
- Áreas: nomes canônicos do catálogo. Liste TODAS as áreas efetivamente cobradas para RESOLVER a questão (interdisciplinaridade), não áreas só citadas. Padrão: interpretação de texto sem outra área → "Língua Portuguesa e Literaturas"; questão de língua estrangeira → "Língua Inglesa".
- Assuntos: 1–3 por área, por proximidade SEMÂNTICA/temática, copiando o string EXATAMENTE do catálogo.

COORDENADAS DE FIGURAS (bbox da QUESTÃO INTEIRA):
- Para cada questão que contenha figura/gráfico/tabela/imagem/cartum, adicione UM item em "figuras_coordenadas" cujo bbox cubra o BLOCO INTEIRO da questão na página em que ela COMEÇA — do topo do enunciado (incluindo os textos de apoio comuns e a própria figura) até a base da última alternativa — gerado com FOLGA, NUNCA apenas a imagem.
- Se a questão se estender por mais de uma página, registre o bbox da página inicial cobrindo todo o conteúdo da questão exibido nela (do topo do texto até a última alternativa ou fim do bloco que pertença à questão).
- Formato do item: {{"pagina": <N>, "tipo": "figura|grafico|tabela|imagem|cartum", "elemento": "<curta descrição>", "bbox": [y0, x0, y1, x1]}}.
- Coordenadas em PERMIL (0–1000) da largura/altura da página, origem no canto SUPERIOR ESQUERDO: y0 = topo do bloco, x0 = borda esquerda, y1 = base do bloco, x1 = borda direita.
- Questão sem nenhuma imagem/figura → "figuras_coordenadas": [].

CATÁLOGO (strings exatas):
{catalog}

SCHEMA (responda SOMENTE com JSON válido, sem markdown):
{{
  "questoes": [
    {{
      "numero": 1,
      "tipo": "objetiva",
      "enunciado": "...",
      "textos_de_apoio": ["... (se o texto for comum a outras questões, repita-o INTEGRAL aqui em cada questão envolvida)"],
      "midia": ["Página N: descrição objetiva."],
      "alternativas": {{"a": "...", "b": "...", "c": "...", "d": "...", "e": "..."}},
      "gabarito": "c",
      "areas": [{{"area": "Física", "assuntos": ["...", "..."]}}],
      "extraida_parcialmente": false,
      "anulada": false,
      "figuras_coordenadas": [{{"pagina": 3, "tipo": "figura", "elemento": "...", "bbox": [y0, x0, y1, x1]}}]
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
    if label.endswith("_1s") or label.endswith("_1S") or label.endswith("_1dia"):
        sem = 1
    elif (label.endswith("_2s") or label.endswith("_2S") or label.endswith("_2dia")
          or (re.search(r"_\d$", label) and label.endswith("2"))):
        sem = 2
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


def extract_range(label, cat, ctx, qu, gu, lo, hi, total, key, depth=0):
    """Tenta transcrever as páginas lo..hi em UMA chamada; em falha/JSON vazio,
    divide o intervalo ao meio (até depth=2) e tenta as metades."""
    parts = [
        {"text": build_instruction(label, cat, lo, hi, total, ctx)},
        {"file_data": {"file_uri": qu, "mime_type": "application/pdf"}},
        {"file_data": {"file_uri": gu, "mime_type": "application/pdf"}},
    ]
    t0 = time.time()
    text = ""
    try:
        model, text = call_with_fallback(parts, label, key)
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        qs = data["questoes"] or []
        nums = sorted(q["numero"] for q in qs)
        print(f"[{label}] paginas {lo}-{hi} [{model} {time.time()-t0:.0f}s]: "
              f"{len(qs)} questoes {nums[0] if nums else '-'}..{nums[-1] if nums else '-'}", flush=True)
        if qs:
            return [data]
        print(f"[{label}] paginas {lo}-{hi}: SEM QUESTOES", flush=True)
    except Exception as e:
        print(f"[{label}] CHUNK {lo}-{hi} INVALIDO: {e} | head: {text[:200]}", flush=True)
    if depth >= 2 or hi <= lo:
        return []
    mid = (lo + hi) // 2
    out = extract_range(label, cat, ctx, qu, gu, lo, mid, total, key, depth + 1)
    out += extract_range(label, cat, ctx, qu, gu, mid + 1, hi, total, key, depth + 1)
    return out


def main():
    label = sys.argv[1]
    if label.endswith("_questoes"):
        label = label[:-len("_questoes")]
    ano, sem_label = parse_label(label)
    os.makedirs(OUT, exist_ok=True)
    cat = catalog_text()

    print(f"[{label}] uploading...", flush=True)
    qpath = pdf_path(label, "questoes")
    gpath = pdf_path(label, "gabarito")
    if not qpath or not gpath:
        raise SystemExit(f"[{label}] PDFs não encontrados: {qpath} / {gpath}")
    key = next_key()
    print(f"[{label}] chave{key_idx_of(key)}/{len(KEYS)}", flush=True)
    qu, _ = upload(qpath, key)
    gu, _ = upload(gpath, key)
    print(f"[{label}] uploaded.", flush=True)

    total_pages = pdf_pages(qpath) or page_count(label)
    step = int(os.environ.get("STEP", total_pages))
    ctx = vestibular_context(label)
    print(f"[{label}] modos: step={step}/{total_pages} paginas modelo={MODELFALL[0]} versao={ctx['versao']}", flush=True)
    chunks = []
    sem_node = None
    for lo in range(1, total_pages + 1, step):
        hi = min(lo + step - 1, total_pages)
        got = extract_range(label, cat, ctx, qu, gu, lo, hi, total_pages, key)
        chunks.extend(got)
    for data in chunks:
        if "semestre_no_cabecalho" in data and data["semestre_no_cabecalho"]:
            sem_node = data["semestre_no_cabecalho"] if sem_node is None else sem_node

    nums, ordered = merge_chunks(chunks, label, ano, sem_label)
    if not ordered:
        raise SystemExit(f"[{label}] nenhuma questão extraída")
    missing = [n for n in range(1, max(nums) + 1) if n not in nums]
    if missing:
        print(f"[{label}] AVISO: questão(ns) ausente(s): {missing}", flush=True)

    sem = sem_node if sem_node in (1, 2) else sem_label
    if sem_node not in (1, 2) and sem_node:
        print(f"[{label}] semestre_no_cabecalho ignorado: {sem_node!r}", flush=True)
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