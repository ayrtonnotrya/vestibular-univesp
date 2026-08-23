import subprocess
import sys
import time

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"]

FUVEST_LABELS = [f"fuvest_{ano}" for ano in range(2010, 2027)]

DEFAULT = FUVEST_LABELS


def main():
    only = sys.argv[1:] or DEFAULT
    for label in only:
        print(f"\n===== {label} =====", flush=True)
        rc = subprocess.call(["python", "extract.py", label])
        print(f"[runner] {label} extract rc={rc}", flush=True)
        time.sleep(15)
        subprocess.call(["python", "fix_paginas.py", label])
        print(f"[runner] {label} fix_paginas ok", flush=True)


if __name__ == "__main__":
    main()