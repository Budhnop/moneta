"""Format ouvert dans l'image de la nuit : chaque table d'une copie cohérente en CSV UTF-8, avec le schéma, lisible sans Moneta.

Repris de core/csv_export.py de Smith (spec 013, T064), sans la version tableur : ici, une copie exacte pour restaurer.
"""
from contextlib import closing
import csv
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile

from socle.database_backup import backup_database


def export_csv(db, folder):
    """Écrit <table>.csv, schema.sql et LISEZMOI.txt dans un nouveau dossier privé; rend {table: lignes}."""
    folder = Path(folder)
    folder.mkdir(parents=True, mode=0o700)
    counts = {}
    with tempfile.TemporaryDirectory(prefix="moneta-export-") as work:
        snapshot = backup_database(db, work)
        with closing(sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)) as con:
            schema = con.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
                                 "AND name NOT LIKE 'sqlite_%' ORDER BY type='table' DESC, name").fetchall()
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for table in tables:
                cursor = con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
                # utf-8-sig : Numbers et Excel lisent tous deux les accents.
                with open(folder / f"{table}.csv", "w", newline="", encoding="utf-8-sig", opener=private) as file:
                    writer = csv.writer(file)
                    writer.writerow([c[0] for c in cursor.description])
                    rows = 0
                    for row in cursor:
                        writer.writerow(row)
                        rows += 1
                counts[table] = rows
    with open(folder / "schema.sql", "w", encoding="utf-8", opener=private) as file:
        file.write("".join(f"{sql};\n\n" for _, sql in schema))
    with open(folder / "LISEZMOI.txt", "w", encoding="utf-8", opener=private) as file:
        file.write(f"Export Moneta du {datetime.now():%Y-%m-%d %H:%M}, depuis {Path(db).name}.\n"
                   "Un fichier CSV par table (UTF-8, virgule, première ligne = colonnes). "
                   "Une cellule vide signifie une valeur absente (NULL). schema.sql décrit les tables.\n"
                   "Copie exacte des valeurs, pour restaurer.\n\n"
                   + "".join(f"{table}.csv : {rows} ligne(s)\n" for table, rows in counts.items()))
    return counts


def private(path, flags):
    return os.open(path, flags, 0o600)
