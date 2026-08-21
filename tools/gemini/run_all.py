import os
import subprocess
import sys
import time

LABELS = ["univesp_2017_2s", "univesp_2018_1s", "univesp_2018_2s", "univesp_2019_2",
          "univesp_2020", "univesp_2021", "univesp_2022", "univesp_2023", "univesp_2024"]


def main():
    only = sys.argv[1:] or LABELS
    for label in only:
        print(f"\n===== {label} =====", flush=True)
        rc = subprocess.call(["python", "extract.py", label])
        print(f"[runner] {label} rc={rc}", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()