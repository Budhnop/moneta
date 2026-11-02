"""Statut ou application des migrations numérotées de finances.db (socle.migrations : sauvegarde vérifiée avant toute application)."""
import argparse
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.chemins import PAQUET, backups, finances_db  # noqa: E402
from socle.migrations import changed, migrate, pending  # noqa: E402

MIGRATIONS = PAQUET / "migrations" / "finances"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrations numérotées de la base de Moneta.")
    parser.add_argument("--appliquer", action="store_true", help="Appliquer les migrations en attente (sinon : statut seulement).")
    args = parser.parse_args(argv)
    db = finances_db()
    try:
        todo = pending(db, MIGRATIONS)
        if not args.appliquer:
            state = "À jour." if not todo else "En attente : " + ", ".join(f"{v:04d} {n}" for v, n, _ in todo)
            print(f"finances ({db}) : {state}" + ("" if db.is_file() else " Base absente : elle sera créée."))
            drift = changed(db, MIGRATIONS)
            if drift:
                print("ATTENTION : finances — migration modifiée depuis son application (elle ne sera pas rejouée) : "
                      + ", ".join(f"{v:04d} {n}" for v, n in drift))
            return 0
        done, backup = migrate(db, backups() / "database" / "moneta", MIGRATIONS)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"ERREUR : finances — migration non effectuée — {exc}")
        return 1
    if not done:
        print("OK : finances déjà à jour.")
    for version, migration, effect in done:
        print(f"OK : finances {version:04d} {migration} — {effect}")
    if backup:
        print(f"Sauvegarde : {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
