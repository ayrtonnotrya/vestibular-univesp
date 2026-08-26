import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

UNIVESP_LABELS = [f"univesp_{y}" for y in ("2017_2s", "2018_1s", "2018_2s", "2019_2")]
UNIVESP_LABELS += [f"univesp_{ano}" for ano in range(2020, 2027)]

FUVEST_LABELS = [f"fuvest_{ano}" for ano in range(2010, 2027)]

ENEM_LABELS = ["enem_2011_2dia"]
ENEM_LABELS += [f"enem_{ano}_{dia}" for ano in range(2012, 2026) for dia in ("1dia", "2dia")]

FATEC_LABELS = []
for ano, dias in (("2010", "1S 2S"), ("2011", "2S"), ("2012", "1S 2S"),
                  ("2013", "1S 2S"), ("2014", "1S 2S"), ("2015", "1S 2S"),
                  ("2016", "1S 2S"), ("2017", "1S 2S"), ("2018", "1S 2S"),
                  ("2019", "1S 2S"), ("2020", "1S"), ("2023", "1S 2S")):
    for d in dias.split():
        FATEC_LABELS.append(f"fatec_{ano}_{d}")

UNESP_LABELS = [f"unesp_{ano}" for ano in range(2010, 2021)]
UNESP_LABELS += [f"unesp_{ano}_{dia}" for ano in (2021, 2022) for dia in ("1dia", "2dia")]
UNESP_LABELS += [f"unesp_{ano}" for ano in range(2023, 2027)]

# Novos exames (nunca extraídos): única chamada por exame via prompt corrigido.
DEFAULT = ENEM_LABELS + FATEC_LABELS + UNESP_LABELS + [f"univesp_{ano}" for ano in (2025, 2026)]

ALL = UNIVESP_LABELS + FUVEST_LABELS + ENEM_LABELS + FATEC_LABELS + UNESP_LABELS

CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))


def process_label(label):
    label = label.removesuffix("_questoes")
    print(f"\n===== {label} =====", flush=True)
    t0 = time.time()
    rc = subprocess.call(["python", "extract.py", label])
    print(f"[runner] {label} extract rc={rc} ({time.time()-t0:.0f}s)", flush=True)
    time.sleep(10)
    subprocess.call(["python", "fix_paginas.py", label])
    print(f"[runner] {label} fix_paginas ok ({time.time()-t0:.0f}s)", flush=True)
    return label, rc


def main():
    only = sys.argv[1:] or DEFAULT
    if CONCURRENCY <= 1:
        for label in only:
            process_label(label)
        return
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(process_label, label): label for label in only}
        for fut in as_completed(futs):
            label, rc = fut.result()
            print(f"[runner] DONE {label} rc={rc}", flush=True)


if __name__ == "__main__":
    main()