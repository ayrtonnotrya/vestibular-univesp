"""Cliente do router OpenAI-compatível (OpenCode Go) para o módulo de redação.

Adaptado de `gpt_call`/`_post` de `tools/gemini/score_dificuldade.py`, com
`httpx` (dependência core) e apenas o essencial: POST /chat/completions com
`response_format` + fallback de variantes (`thinking: disabled`) e retry com
backoff em erro de rede/429/5xx.

Env: OPENCODE_BASE_URL, OPENCODE_API_KEY, OPENCODE_SESSION (rroteamento do
OpenCode Go exige o header `x-opencode-session`), MODEL_CORRECAO, MODEL_TUTOR,
REDA_TIMEOUT (segundos, default 300).
"""

import json
import os
import time

import httpx

BASE = os.environ.get("OPENCODE_BASE_URL", "http://100.90.193.17:18905").rstrip("/")
API_KEY = os.environ.get("OPENCODE_API_KEY", "")
SESSION = os.environ.get("OPENCODE_SESSION", "vestibular-redacao")
MODEL_CORRECAO = os.environ.get("MODEL_CORRECAO", "deepseek-v4-pro")
MODEL_TUTOR = os.environ.get("MODEL_TUTOR", "kimi-k3")
TIMEOUT = float(os.environ.get("REDA_TIMEOUT", "300"))


def _extract_json(text: str) -> dict:
    """Localiza o objeto JSON na resposta (mesma heurística do `score_dificuldade`)."""
    i = text.find("{")
    j = text.rfind("}")
    if i < 0 or j < i:
        raise ValueError(f"JSON não localizado em: {text[:200]}")
    return json.loads(text[i : j + 1])


def _content_de(texto_resposta: str) -> str:
    return json.loads(texto_resposta)["choices"][0]["message"].get("content") or ""


def chat(
    modelo: str,
    messages: list[dict],
    json_mode: bool = True,
    temperature: float = 0.2,
    timeout: float | None = None,
    max_try: int = 3,
) -> str:
    """Uma conversa com o router; devolve o texto da resposta (nunca vazio).

    Tenta variantes na ordem (json_object + thinking off → json_object →
    thinking off → nada), pois nem todo modelo aceita todas; 3 tentativas
    completas com backoff em erro de rede/429/5xx/response vazia.
    """
    headers = {"Content-Type": "application/json", "x-opencode-session": SESSION}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    base = {"model": modelo, "messages": messages, "temperature": temperature}
    variants: list[dict] = [{}]
    if json_mode:
        variants = [
            {"response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}},
            {"response_format": {"type": "json_object"}},
            {"thinking": {"type": "disabled"}},
            {},
        ]
    timeout = timeout or TIMEOUT
    last: object = None
    for i in range(max_try):
        for extra in variants:
            payload = dict(base)
            payload.update(extra)
            try:
                r = httpx.post(f"{BASE}/chat/completions", headers=headers, json=payload, timeout=timeout)
                sc, text = r.status_code, r.text
            except (httpx.TimeoutException, OSError) as e:
                last = str(e)[:120]
                continue
            if sc == 200:
                try:
                    content = _content_de(text)
                except Exception:
                    last = f"corpo inesperado: {text[:120]}"
                    continue
                if content.strip():
                    return content
                last = "resposta vazia"
            elif sc in (429, 500, 502, 503, 504):
                last = f"HTTP {sc}"
            else:
                raise RuntimeError(f"{modelo}: HTTP {sc}: {text[:200]}")
        time.sleep(2 + 4 * i)
    raise RuntimeError(f"{modelo} exauriu {max_try} tentativas (last={last})")
