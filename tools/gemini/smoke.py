import json
import os
import sys

from google import genai

KEY = os.environ["API_KEY"]
client = genai.Client(api_key=KEY)
MODEL = os.environ.get("MODEL", "gemini-3.7-flash")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/work/data/univesp_2021_gabarito.pdf"
    f = client.files.upload(file=path)
    print("uploaded:", f.name, f.state)
    resp = client.models.generate_content(
        model=MODEL,
        contents=[f, "Transcreva TODO o texto deste PDF na ordem, fielmente, sem resumir. Responda apenas com o texto."],
    )
    print("CHARS:", len(resp.text))
    print(resp.text[:1500])


if __name__ == "__main__":
    main()