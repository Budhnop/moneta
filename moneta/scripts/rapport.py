"""Rapport financier annuel imprimable (HTML dans exports/), à partir des bases réelles en lecture."""
import argparse
from datetime import date
import json
import sys

from core.executor import execute_safe
from core.finances_report import write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rapport financier annuel imprimable.")
    parser.add_argument("annee", nargs="?", type=int, default=date.today().year)
    args = parser.parse_args(argv)
    reply = execute_safe("finances_rapport", [str(args.annee)], timeout=30)
    if not reply["ok"]:
        print(f"ERREUR : rapport impossible — {reply['error']}")
        return 1
    print(f"OK : {write_report(json.loads(reply['result']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
