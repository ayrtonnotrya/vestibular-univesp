"""Score de dificuldade das questões via IA, em duas passadas.

Passada 1 (`passada1`) — vision do router OpenCode Go (`deepseek-v4-flash-
vision-exp`, env `MODEL_PAGE`; `BACKEND=gemini` volta ao Gemini): para cada
página que contém questões com imagem, envia a imagem da página
(`data/paginas/<label>/pNNN.jpg`, renderizada por render_pages.py); o modelo
resolve as questões da página e devolve o bbox da QUESTÃO INTEIRA (enunciado +
figuras + alternativas).

Passada 2 (`passada2`) — router OpenAI-compatible (OpenCode Go / DeepSeek):
resolve as questões de **texto puro** (flash), N=TENTATIVAS por questão.
Questões com imagem são IGNORADAS por padrão (os crops via `bbox_questao`
sairam pouco confiáveis); para pontuá-las use `--com-imagem` (re-resolve via
crop do bbox verificado, vision-exp).

NÃO usa PDF: a página/crop vem sempre das imagens pré-renderizadas em
`data/paginas/` (mesma fonte que o app).

Persistência: toda resposta da IA é gravada nos JSONs versionados
(`data/json/*_questoes.json`) nas chaves `respostas_ia`, `score_ia` e
`bbox_questao` — o DB não é versionado; num deploy do zero, rodo `seed` a partir
dos JSONs sem gastar chamadas novas.

Uso (imagem vestibular-app, com $PWD montado em /work e --network host):
  python score_dificuldade.py status [labels...]
  python score_dificuldade.py passada1 [labels...]   # router vision + páginas
  python score_dificuldade.py passada2 [labels...]   # router (texto + figuras)
  python score_dificuldade.py seed [labels...]       # JSON -> SQLite (sem API)
  python score_dificuldade.py smoke [n]              # teste do router

Env: API_KEY, OPENCODE_API_KEY, OPENCODE_BASE_URL, MODEL_GEMINI,
MODEL_TEXT, MODEL_VISION, MAX_TENTATIVAS, CONCURRENCY.
"""

import argparse
import base64
import datetime as dt
import io
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None
try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None
try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

DATA = os.environ.get("SCORE_DATA", "/work/data")
JK = os.path.join(DATA, "json")
PAGE_DIR = os.path.join(DATA, "paginas")
GEMINI_REST = "https://generativelanguage.googleapis.com/v1beta"
BASE = os.environ.get("OPENCODE_BASE_URL", "http://100.90.193.17:18905").rstrip("/")
GEMINI_KEY = os.environ.get("API_KEY", "")
OPENCODE_KEY = os.environ.get("OPENCODE_API_KEY", "")
MODEL_GEMINI = os.environ.get("MODEL_GEMINI", "gemini-3.5-flash-lite")
MODEL_TEXT = os.environ.get("MODEL_TEXT", "hy3,mimo-v2.5")
MODELS = [m.strip() for m in MODEL_TEXT.split(",") if m.strip()]
MODEL_VISION = os.environ.get("MODEL_VISION", "deepseek-v4-flash-vision-exp")
MODEL_PAGE = os.environ.get("MODEL_PAGE", MODEL_VISION)
BACKEND = os.environ.get("BACKEND", "router")
MAX_TENT = int(os.environ.get("MAX_TENTATIVAS", "2"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
GPT_TIMEOUT = int(os.environ.get("GPT_TIMEOUT", "120"))
CHECKPOINT = int(os.environ.get("CHECKPOINT", "25"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))


def _post(url, headers, payload, timeout=300):
    if _requests is not None:
        r = _requests.post(url, headers=headers, json=payload, timeout=timeout)
        return r.status_code, r.text
    if _httpx is not None:
        r = _httpx.post(url, headers=headers, json=payload, timeout=timeout)
        return r.status_code, r.text
    import urllib.request

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _extract_json(text):
    i = text.find("{")
    j = text.rfind("}")
    if i < 0 or j < i:
        raise ValueError(f"JSON não localizado em: {text[:200]}")
    return json.loads(text[i : j + 1])


def _ago():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_pair(label):
    jq = Path(JK) / f"{label}_questoes.json"
    ji = Path(JK) / f"{label}_imagens.json"
    questoes = json.loads(jq.read_text(encoding="utf-8"))
    imagens = (
        json.loads(Path(ji).read_text(encoding="utf-8")) if ji.exists() else {}
    )
    return questoes, imagens


def save_pair(label, questoes, imagens=None):
    jq = Path(JK) / f"{label}_questoes.json"
    jq.write_text(json.dumps(questoes, ensure_ascii=False, indent=2), encoding="utf-8")
    if imagens is not None:
        ji = Path(JK) / f"{label}_imagens.json"
        ji.write_text(
            json.dumps(imagens, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def labels_all():
    found = sorted(p.name[: -len("_questoes.json")] for p in Path(JK).glob("*_questoes.json"))
    return found


def obj_questions(questoes):
    return [q for q in questoes["questoes"] if q.get("tipo") == "objetiva"]


def has_midia(q):
    return bool(q.get("midia"))


def attempts(q):
    return [a for a in q.get("respostas_ia", []) if isinstance(a, dict)]


def tried(q):
    return len(attempts(q))


def score_of(q):
    gab = q.get("gabarito")
    if not gab or q.get("anulada"):
        return None
    ac = sum(1 for a in attempts(q) if a.get("alternativa") == gab)
    n = len(attempts(q))
    return {"acertos": ac, "tentativas": n, "score": round(ac / n, 4)} if n else None


def gemini_call(parts, timeout=420):
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    last = None
    for i in range(7):
        try:
            sc, text = _post(
                f"{GEMINI_REST}/models/{MODEL_GEMINI}:generateContent",
                headers, payload, timeout,
            )
            if sc == 200:
                out = json.loads(text)
                cand = out["candidates"][0]
                return "".join(p.get("text", "") for p in cand["content"]["parts"])
            last = sc
            if sc in (429, 500, 503):
                s = 5 + 5 * i
                print(f"      gemini retry {i} ({sc}) em {s}s", flush=True)
                time.sleep(s)
            else:
                raise RuntimeError(f"gemini HTTP {sc}: {text[:200]}")
        except (TimeoutError, OSError) as e:
            last = str(e)[:80]
            s = 5 + 5 * i
            print(f"      gemini retry {i} ({last}) em {s}s", flush=True)
            time.sleep(s)
    raise RuntimeError(f"gemini exauriu retries (last={last})")


def gpt_call(model, messages, with_image=False, max_try=3):
    headers = {"Content-Type": "application/json"}
    if OPENCODE_KEY:
        headers["Authorization"] = f"Bearer {OPENCODE_KEY}"
    base = {"model": model, "messages": messages, "temperature": TEMPERATURE}
    variants = [
        {"response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}},
        {"response_format": {"type": "json_object"}},
        {"thinking": {"type": "disabled"}},
        {},
    ]
    last = None
    for i in range(max_try):
        for extra in variants:
            payload = dict(base)
            payload.update(extra)
            try:
                sc, text = _post(
                    f"{BASE}/chat/completions", headers, payload, timeout=GPT_TIMEOUT
                )
            except (TimeoutError, OSError) as e:
                last = str(e)[:80]
                continue
            if sc == 200:
                try:
                    msg = json.loads(text)["choices"][0]["message"]
                except Exception:
                    continue
                content = msg.get("content") or ""
                if content.strip():
                    return content, bool(msg.get("reasoning_content"))
                last = "resposta vazia"
            elif sc in (429, 500, 502, 503, 504):
                last = sc
            else:
                last = f"HTTP {sc}: {text[:120]}"
        time.sleep(2 + 2 * i)
    raise RuntimeError(f"{model} exauriu retries (last={last})")


def page_jpeg(label, pagina):
    if not (isinstance(pagina, int) and pagina >= 1):
        return None
    p = Path(PAGE_DIR) / label / f"p{pagina:03d}.jpg"
    return str(p) if p.exists() else None


def image_meta(path):
    if Image is None:
        raise RuntimeError("Pillow ausente — rode na imagem vestibular-app")
    if path is None:
        return None
    with Image.open(path) as im:
        return path, im.size


def render_page(label, pagina):
    meta = image_meta(page_jpeg(label, pagina))
    if meta is None:
        return None, None
    path, dims = meta
    return Path(path).read_bytes(), dims


def render_crop(label, pagina, bbox_permil, pad=0.02):
    if Image is None:
        raise RuntimeError("Pillow ausente — rode na imagem vestibular-app")
    if not (isinstance(pagina, int) and pagina >= 1):
        return None
    if not (isinstance(bbox_permil, list) and len(bbox_permil) == 4):
        return None
    path, dims = image_meta(page_jpeg(label, pagina))
    if path is None:
        return None
    W, H = dims
    y0, x0, y1, x1 = (bbox_permil[i] / 1000 for i in range(4))
    pad_x, pad_y = (x1 - x0) * pad, (y1 - y0) * pad
    box = (
        max(0, int((x0 - pad_x) * W)), max(0, int((y0 - pad_y) * H)),
        min(W, int((x1 + pad_x) * W)), min(H, int((y1 + pad_y) * H)),
    )
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return None
    with Image.open(path) as im:
        im = im.crop(box)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
        return buf.getvalue()


def pixel_to_permil(bbox, dims):
    if not (isinstance(bbox, list) and len(bbox) == 4) or not dims:
        return None
    W, H = dims
    y0, x0, y1, x1 = bbox
    if y0 > y1:
        y0, y1 = y1, y0
    if x0 > x1:
        x0, x1 = x1, x0
    def cl(v):
        return max(0, min(1000, round(v)))
    return [cl(y0 / H * 1000), cl(x0 / W * 1000), cl(y1 / H * 1000), cl(x1 / W * 1000)]


MIN_EXTENT = int(os.environ.get("MIN_EXTENT", "30"))


def bbox_ok(bbox):
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return False
    y0, x0, y1, x1 = bbox
    if not all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in bbox):
        return False
    return (y1 - y0) >= MIN_EXTENT and (x1 - x0) >= MIN_EXTENT


def page_map(questoes):
    pag = {}
    for q in obj_questions(questoes):
        if not has_midia(q):
            continue
        p = q.get("pagina")
        if isinstance(p, int) and p >= 2:
            pag.setdefault(p, []).append(q)
    return pag


def _solve_page(label, pagina, pend, fonte, quiet=False):
    img, dims = render_page(label, pagina)
    if img is None:
        if not quiet:
            print(f"[{label}] página {pagina}: imagem ausente — pulado", flush=True)
        return set()
    nums = sorted(q["numero"] for q in pend)
    prompt = passada1_prompt(label, pagina, nums, dims)
    t0 = time.time()
    if BACKEND == "gemini":
        text = gemini_call([
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}},
        ])
    else:
        text, thinking = gpt_call(MODEL_PAGE, page_messages(prompt, img))
        if thinking:
            print(f"      pág {pagina}: AVISO thinking ligado", flush=True)
    try:
        data = _extract_json(text)
    except Exception as e:
        print(f"[{label}] pág {pagina}: JSON inválido ({e}) — {text[:120]}", flush=True)
        return set()
    resp = {r["numero"]: r for r in data.get("respostas", []) if isinstance(r, dict)}
    nao = data.get("nao_encontradas", []) or []
    for q in pend:
        r = resp.get(q["numero"])
        if not r:
            continue
        alt = str(r.get("alternativa", "")).strip().lower()
        bbox = pixel_to_permil(r.get("bbox_questao"), dims)
        if alt:
            q.setdefault("respostas_ia", []).append(
                {"fonte": fonte, "alternativa": alt, "data": _ago()}
            )
        if bbox:
            q["bbox_questao"] = bbox
    print(
        f"[{label}] pág {pagina} [{fonte} {time.time()-t0:.0f}s]: "
        f"respostas={len(resp)}{f' nao_encontradas={nao}' if nao else ''}",
        flush=True,
    )
    if BACKEND == "gemini" and not quiet:
        time.sleep(float(os.environ.get("SLEEP_GEMINI", "3")))
    return set(resp)


def passada1_label(label, force=False):
    questoes, imagens = load_pair(label)
    fonte = MODEL_PAGE if BACKEND == "router" else MODEL_GEMINI
    pag = page_map(questoes)
    if not pag:
        print(f"[{label}] nenhuma página com questões de imagem", flush=True)
        return
    for pagina, qs in sorted(pag.items()):
        pend = [q for q in qs if force or tried(q) == 0 or not q.get("bbox_questao")]
        if pend:
            _solve_page(label, pagina, pend, fonte)
    pendentes = [
        q for q in obj_questions(questoes)
        if has_midia(q) and (tried(q) == 0 or not q.get("bbox_questao"))
        and isinstance(q.get("pagina"), int) and q["pagina"] >= 2
    ]
    for q in pendentes:
        base = q["pagina"]
        for delta in (-1, 1):
            p = base + delta
            if p < 2:
                continue
            if tried(q) > 0 and q.get("bbox_questao"):
                break
            _solve_page(label, p, [q], fonte)
            if tried(q) > 0 and q.get("bbox_questao"):
                break
    save_pair(label, questoes, imagens)
    qs = obj_questions(questoes)
    bb = sum(1 for q in qs if q.get("bbox_questao"))
    tr = sum(1 for q in qs if tried(q))
    sus = [
        q["numero"] for q in qs
        if has_midia(q) and q.get("bbox_questao") and not bbox_ok(q["bbox_questao"])
    ]
    sem = [q["numero"] for q in qs if has_midia(q) and tried(q) == 0]
    print(f"[{label}] SAVED: questões com bbox_questao={bb}; com resposta IA={tr}; "
          f"midia sem resposta={sem}; bbox suspeitos={sus}", flush=True)


def passada1_prompt(label, pagina, nums, dims):
    W, H = dims
    return (
        f'Você é um corretor de prova de vestibular. A imagem anexa é a página {pagina} '
        f'do caderno "{label}" (dimensões {W}x{H} pixels). '
        f"Estas questões objetivas (com alternativas a–e impressas na página) devem estar nela: {nums}. "
        "Para cada uma:\n"
        "1. RESOLVA a questão e informe a alternativa correta.\n"
        "2. Informe o bounding box da QUESTÃO INTEIRA (enunciado + figuras/tabelas + alternativas) "
        "em PIXELS da imagem anexa, formato [y0, x0, y1, x1], origem no CANTO SUPERIOR ESQUERDO "
        "(y cresce para baixo). Se a questão estiver dividida em duas colunas, a caixa deve englobar tudo. "
        "A caixa deve fechar bem, sem cortar texto nem incluir a questão vizinha.\n"
        'Se alguma questão da lista NÃO estiver na página, liste-a em "nao_encontradas". '
        'Responda SOMENTE JSON: {"respostas": [{"numero": N, "alternativa": "a", '
        '"bbox_questao": [y0, x0, y1, x1]}], "nao_encontradas": [N]}.'
    )


def text_messages(q):
    tex = [t for t in (q.get("textos_de_apoio") or []) if isinstance(t, str)]
    alt = q.get("alternativas") or {}
    alt_txt = "\n".join(f"{k}) {v}" for k, v in sorted(alt.items()))
    apoio = f"\nTEXTOS DE APOIO:\n{chr(10).join(tex)}" if tex else ""
    user = (
        f"Resolva a questão objetiva abaixo e responda SOMENTE com JSON "
        '{"alternativa": "x"} (x = letra a–e).\n\n'
        f"QUESTÃO {q['numero']}:\n{q.get('enunciado', '')}{apoio}\n\n"
        f"ALTERNATIVAS:\n{alt_txt}"
    )
    return [{"role": "user", "content": user}]


def _img_url(img):
    return f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"


def vision_messages(q, img):
    alt = q.get("alternativas") or {}
    alt_txt = "\n".join(f"{k}) {v}" for k, v in sorted(alt.items()))
    user = (
        f"Resolva a questão objetiva abaixo e responda SOMENTE com JSON "
        '{"alternativa": "x"} (x = letra a–e). A imagem anexa contém a figura/tabela da questão.\n\n'
        f"QUESTÃO {q['numero']}:\n{q.get('enunciado', '')}\n\n"
        f"ALTERNATIVAS:\n{alt_txt}"
    )
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": _img_url(img), "detail": "high"}},
        ],
    }]


def page_messages(prompt, img):
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _img_url(img), "detail": "high"}},
        ],
    }]


def _solvetext(q, model):
    n = q["numero"]
    text, thinking = gpt_call(model, text_messages(q))
    alt = _extract_json(text).get("alternativa")
    return n, alt, thinking, model


def _solvevision(label, q):
    n = q["numero"]
    if not bbox_ok(q.get("bbox_questao")):
        return n, None, False, "bbox-invalido"
    img = render_crop(label, q.get("pagina"), q.get("bbox_questao"))
    if img is None:
        return n, None, False, "sem-imagem"
    text, thinking = gpt_call(MODEL_VISION, vision_messages(q, img), with_image=True)
    alt = _extract_json(text).get("alternativa")
    return n, alt, thinking, MODEL_VISION


def _resolve_text(q, model):
    """Tenta o modelo primário e cai para os demais da rotação se falhar."""
    ordem = [model] + [m for m in MODELS if m != model]
    last = None
    for m in ordem:
        try:
            return _solvetext(q, m)
        except Exception as e:
            last = str(e)[:100]
            continue
    return q["numero"], None, False, f"ERRO {last}"


def passada2_label(label, max_tent, force=False, com_imagem=False):
    questoes, imagens = load_pair(label)
    fila, sem_bbox, ignoradas = [], 0, 0
    for q in obj_questions(questoes):
        if force:
            q["respostas_ia"] = []
            q.pop("score_ia", None)
        need = max_tent if force else max_tent - tried(q)
        if need <= 0:
            continue
        if has_midia(q):
            if not com_imagem:
                ignoradas += 1
                continue
            if not q.get("bbox_questao"):
                sem_bbox += 1
                continue
            for _ in range(need):
                fila.append(("v", q, MODEL_VISION))
        else:
            for k in range(need):
                model = MODELS[k % len(MODELS)]
                fila.append(("t", q, model))
    if not fila and not sem_bbox and not ignoradas:
        print(f"[{label}] tudo já pontuado (>= {max_tent})", flush=True)
        return
    print(f"[{label}] fila: {len(fila)} chamadas | ignoradas (com imagem): {ignoradas}"
          f"{f' | sem bbox (aguardam passada1): {sem_bbox}' if sem_bbox else ''}",
          flush=True)

    def run(item):
        kind, q, model = item
        try:
            return _solvevision(label, q) if kind == "v" else _resolve_text(q, model)
        except Exception as e:
            return q["numero"], None, False, f"ERRO {str(e)[:100]}"

    ok = thinking_n = 0
    avisos = 0
    try:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            fut = {ex.submit(run, it): it for it in fila}
            for i, f in enumerate(as_completed(fut), 1):
                n, alt, thinking, fonte = f.result()
                q = fut[f][1]
                if alt:
                    q.setdefault("respostas_ia", []).append(
                        {"fonte": fonte, "alternativa": str(alt).strip().lower(),
                         "data": _ago()}
                    )
                    ok += 1
                else:
                    print(f"      q{n}: sem alternativa ({fonte})", flush=True)
                if thinking:
                    thinking_n += 1
                    if avisos < 3:
                        print(f"      q{n}: AVISO thinking ligado (latência alta)", flush=True)
                        avisos += 1
                if i % CHECKPOINT == 0:
                    save_pair(label, questoes, imagens)
                    print(f"[{label}] checkpoint {i}/{len(fila)} ({ok} respostas)",
                          flush=True)
    finally:
        save_pair(label, questoes, imagens)
    print(f"[{label}] passada2: {ok}/{len(fila)} respostas registradas | "
          f"ignoradas com imagem: {ignoradas}"
          + (f" | {thinking_n} chamadas com thinking ligado" if thinking_n else ""),
          flush=True)


def seed_label(label):
    questoes, imagens = load_pair(label)
    n_score = n_bbox = n_resp = 0
    for q in obj_questions(questoes):
        if tried(q):
            n_resp += 1
        s = score_of(q)
        if s:
            q["score_ia"] = s
            n_score += 1
        else:
            q["score_ia"] = None
        n_bbox += 1 if q.get("bbox_questao") else 0
    save_pair(label, questoes, imagens)
    print(f"[{label}] questões com resposta IA={n_resp}; com score_ia={n_score}; "
          f"com bbox_questao={n_bbox}", flush=True)
    return n_resp, n_score


def logit(p):
    p = min(max(p, 0.05), 0.95)
    return math.log(p / (1 - p))


KAPPA = 4.0


def _bshrunk(s):
    """Dificuldade do item: −logit(score_ia), encolhida por κ e já com sinal certo."""
    return (s["tentativas"] * (-logit(s["score"]))) / (KAPPA + s["tentativas"])


def area_index(labels=None):
    """(aread, baseline) — mapa (label, numero)->set(áreas) e baseline por área.

    baseline[a] = média do b (encolhido) das questões da área a; usado para
    centrar o b dentro da área (média 0), removendo o viés de competência do LLM."""
    from collections import defaultdict as _dd
    aread = _dd(set)
    try:
        from vestibular.estudo.db import connect
        con = connect()
        for ex, nm, ar in con.execute(
            "SELECT q.exame_label, q.numero, a.nome FROM questoes q "
            "JOIN classificacoes c ON c.questao_id = q.id "
            "JOIN temas t ON t.id = c.tema_id "
            "JOIN areas a ON a.id = t.area_id"
        ):
            aread[(ex, int(nm))].add(ar)
        con.close()
    except Exception:
        pass
    acc = _dd(list)
    for label in (labels or labels_all()):
        for q in obj_questions(load_pair(label)[0]):
            s = score_of(q)
            if not s:
                continue
            areas = aread.get((label, q["numero"]))
            if not areas:
                continue
            br = _bshrunk(s)
            for a in areas:
                acc[a].append(br)
    baseline = {a: sum(v) / len(v) for a, v in acc.items() if v}
    return aread, baseline


def seed_db(label, aread, baseline):
    try:
        from vestibular.estudo.db import connect
    except Exception as e:
        print(f"[{label}] vestibular não importável — DB não atualizado ({e})", flush=True)
        return
    con = connect()
    try:
        for q in obj_questions(load_pair(label)[0]):
            qid = con.execute(
                "SELECT id FROM questoes WHERE exame_label = ? AND numero = ?",
                (label, q["numero"]),
            ).fetchone()
            if not qid:
                continue
            s = score_of(q)
            if not s or s["tentativas"] == 0:
                continue
            con.execute(
                """INSERT INTO dificuldades (questao_id, score, tentativas_ia, modelo)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(questao_id) DO UPDATE SET
                     score=excluded.score, tentativas_ia=excluded.tentativas_ia,
                     modelo=excluded.modelo""",
                (qid[0], s["score"], s["tentativas"], (attempts(q)[-1].get("fonte") or "ia")),
            )
            b_shrunk = _bshrunk(s)
            areas = aread.get((label, q["numero"])) or set()
            centers = [baseline[a] for a in areas if a in baseline]
            center = sum(centers) / len(centers) if centers else None
            b = b_shrunk - center if center is not None else b_shrunk
            con.execute(
                "UPDATE item_params SET b = ?, n_obs = ? WHERE questao_id = ? AND n_obs = 0",
                (b, s["tentativas"], qid[0]),
            )
        con.commit()
        print(f"[{label}] DB atualizado (dificuldades + item_params.b normalizado por área)",
              flush=True)
    finally:
        con.close()


def cmd_status(labels):
    for label in labels:
        questoes, _ = load_pair(label)
        qs = obj_questions(questoes)
        mid = [q for q in qs if has_midia(q)]
        tr = [q for q in qs if tried(q)]
        sc = [q for q in qs if score_of(q)]
        bb = [q for q in mid if q.get("bbox_questao")]
        print(
            f"{label:16} obj={len(qs):4} midia={len(mid):4} "
            f"com_resposta={len(tr):4} com_score={len(sc):4} bbox_questao={len(bb):3}"
        )


def cmd_passada1(labels, force):
    for label in labels:
        print(f"\n=== passada1 {label} ===", flush=True)
        passada1_label(label, force)


def cmd_passada2(labels, force, com_imagem=False):
    for label in labels:
        print(f"\n=== passada2 {label} ===", flush=True)
        passada2_label(label, MAX_TENT, force, com_imagem)


def bbox_figura(label, numero):
    _, imagens = load_pair(label)
    figs = imagens.get("figuras_coordenadas", {}).get(str(numero), [])
    b = figs[0].get("bbox") if figs else None
    return b if isinstance(b, list) and len(b) == 4 else None


def _area(bb):
    return max(0, bb[2] - bb[0]) * max(0, bb[3] - bb[1])


def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = _area((x0, y0, x1, y1)) if x0 < x1 and y0 < y1 else 0
    uni = _area(a) + _area(b) - inter
    return inter / uni if uni else 0


def _contem(outer, inner):
    x0 = max(outer[0], inner[0])
    y0 = max(outer[1], inner[1])
    x1 = min(outer[2], inner[2])
    y1 = min(outer[3], inner[3])
    inter = _area((x0, y0, x1, y1)) if x0 < x1 and y0 < y1 else 0
    return inter / _area(inner) if _area(inner) else 0


def qa_label(label, montagem=False, amostra=60):
    questoes, _ = load_pair(label)
    qs = obj_questions(questoes)
    mid = [q for q in qs if has_midia(q)]
    com_bbox = [q for q in mid if isinstance(q.get("bbox_questao"), list)]
    invalidos = [q["numero"] for q in com_bbox if not bbox_ok(q["bbox_questao"])]

    por_pag = {}
    for q in com_bbox:
        p = q.get("pagina")
        if isinstance(p, int):
            por_pag.setdefault(p, []).append(q)

    sus_over = []
    for p, lst in por_pag.items():
        for a, b in combinations(lst, 2):
            iou = _iou(a["bbox_questao"], b["bbox_questao"])
            if iou > 0.35:
                sus_over.append((a["numero"], b["numero"], p, round(iou, 2)))

    invert = []
    for p, lst in por_pag.items():
        if len(lst) < 2:
            continue
        lst = sorted(lst, key=lambda q: q["bbox_questao"][0])
        nums = [q["numero"] for q in lst]
        inv = sum(1 for i in range(len(nums)) for j in range(i + 1, len(nums))
                  if nums[i] > nums[j])
        ratio = 2 * inv / (len(nums) * (len(nums) - 1))
        if ratio > 0.3:
            invert.append((p, nums, round(ratio, 2)))

    cont = []
    for q in com_bbox:
        fb = bbox_figura(label, q["numero"])
        if fb:
            cont.append(_contem(q["bbox_questao"], fb))
    cmed = sorted(cont)[len(cont) // 2] if cont else None

    print(f"[{label}] midia={len(mid)} com_bbox={len(com_bbox)} "
          f"invalidos={invalidos}", flush=True)
    if com_bbox:
        areas = sorted(_area(q["bbox_questao"]) for q in com_bbox)
        print(f"        área permil²: p10={areas[len(areas)//10]:,} "
              f"p50={areas[len(areas)//2]:,} p90={areas[9*len(areas)//10]:,}",
              flush=True)
    print(f"        sobreposições>35%: {sus_over or '—'}", flush=True)
    print(f"        ordem invertida por página: {invert or '—'}", flush=True)
    print(f"        bbox da figura contido no da questão (mediana): "
          f"{round(cmed, 2) if cmed is not None else 'n/a'}", flush=True)

    if montagem and com_bbox:
        rows = com_bbox if len(com_bbox) <= amostra else com_bbox[:: max(1, len(com_bbox) // amostra)][:amostra]
        exports = montagem_label(label, rows)
        if exports:
            print(f"        montagem visual: {exports}", flush=True)
    return len(com_bbox), len(invalidos)


QA_DIR = os.environ.get("QA_DIR", "/work/tmp/qa")


def montagem_label(label, rows):
    if Image is None:
        print("        Pillow ausente — monte na imagem vestibular-app", flush=True)
        return None
    os.makedirs(QA_DIR, exist_ok=True)
    imgs = []
    for q in rows:
        img = render_crop(label, q.get("pagina"), q.get("bbox_questao"))
        if img is None:
            continue
        im = Image.open(io.BytesIO(img)).convert("RGB")
        im.thumbnail((640, 640))
        from PIL import ImageDraw

        d = ImageDraw.Draw(im)
        d.text((6, 4), f"q{q['numero']} p{q.get('pagina')}", fill=(255, 0, 0))
        imgs.append(im)
    if not imgs:
        return None
    cols = 3
    rows_n = (len(imgs) + cols - 1) // cols
    tw = max(im.width for im in imgs)
    th = max(im.height for im in imgs)
    sheet = Image.new("RGB", (cols * tw, rows_n * th), (255, 255, 255))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
    out = os.path.join(QA_DIR, f"{label}.jpg")
    sheet.save(out, quality=80)
    return out


def cmd_qa(labels, montagem=False, amostra=60):
    for label in labels:
        qa_label(label, montagem, amostra)


def cmd_smoke(n):
    n = max(1, n or 3)
    tv = tt = None
    for label in labels_all():
        for x in obj_questions(load_pair(label)[0]):
            if has_midia(x) and tv is None and x.get("pagina"):
                tv = (label, x)
            if not has_midia(x) and tt is None:
                tt = (label, x)
            if tv and tt:
                break
        if tv and tt:
            break
    if not tv and not tt:
        raise SystemExit("Sem questões para testar")
    for model in MODELS:
        for i in range(n):
            label, q = tt if tt else tv
            t0 = time.time()
            text, thinking = gpt_call(model, text_messages(q))
            alt = _extract_json(text).get("alternativa")
            print(f"[{model}  {label} q{q['numero']}] {time.time()-t0:.1f}s "
                  f"alt={alt} thinking={thinking} gab={q.get('gabarito')}")
    if tv:
        label, q = tv
        bbox = q.get("bbox_questao") or bbox_figura(label, q["numero"])
        for i in range(n):
            img = render_crop(label, q.get("pagina"), bbox)
            if img is None:
                print(f"[visão  {label} q{q['numero']}] crop indisponível (página {q.get('pagina')})")
                break
            t0 = time.time()
            text, thinking = gpt_call(
                MODEL_VISION, vision_messages(q, img), with_image=True
            )
            alt = _extract_json(text).get("alternativa")
            print(f"[visão  {label} q{q['numero']}] {time.time()-t0:.1f}s "
                  f"alt={alt} thinking={thinking} gab={q.get('gabarito')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["status", "qa", "passada1", "passada2", "seed", "smoke"])
    ap.add_argument("labels", nargs="*", default=None)
    ap.add_argument("--force", action="store_true", help="reprocessa mesmo já pontuado")
    ap.add_argument("--smoke-n", type=int, default=3, help="chamadas do smoke")
    ap.add_argument("--montagem", action="store_true",
                    help="qa: exporta contato visual dos crops (QA_DIR)")
    ap.add_argument("--amostra", type=int, default=60,
                    help="qa: máx de crops na montagem")
    ap.add_argument("--com-imagem", action="store_true",
                    help="passada2: inclui questões com imagem (crop do bbox_questao)")
    args, extra = ap.parse_known_args()

    labels = (list(args.labels or []) + extra) or labels_all()
    labels = [l.removesuffix("_questoes") for l in labels]

    if args.cmd == "status":
        cmd_status(labels)
    elif args.cmd == "qa":
        cmd_qa(labels, args.montagem, args.amostra)
    elif args.cmd == "passada1":
        cmd_passada1(labels, args.force)
    elif args.cmd == "passada2":
        cmd_passada2(labels, args.force, args.com_imagem)
    elif args.cmd == "seed":
        aread, baseline = area_index(labels)
        if baseline:
            print("baseline por área: " +
                  ", ".join(f"{a}={v:+.2f}" for a, v in sorted(baseline.items())),
                  flush=True)
        for label in labels:
            print(f"\n=== seed {label} ===", flush=True)
            seed_label(label)
            seed_db(label, aread, baseline)
    elif args.cmd == "smoke":
        cmd_smoke(args.smoke_n)


if __name__ == "__main__":
    main()