"""Numbered schema migrations per base: <dossier>/NNNN_nom.py, applied in order, each in its own transaction (spec 013 : venu de Smith).

Each file defines deja_appliquee(con) and appliquer(con). A base older than this mechanism is adopted:
a migration whose effect is already present is recorded without touching the schema, but only when the base holds all
of the schema the migrations build (tables, columns, foreign keys, indexes, reference rows); a base holding part of it
is refused. Each migration is recorded with the fingerprint of its file: a file changed after it ran is reported.
An applied migration is logged in the base's audit_log when it has one.
"""
from contextlib import closing
import hashlib
import importlib.util
from pathlib import Path
import re
import sqlite3

from socle.database_backup import backup_database

NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.py$")


def available(folder):
    found = []
    for path in sorted(Path(folder).glob("*.py")):
        match = NAME.match(path.name)
        if not match:
            continue
        spec = importlib.util.spec_from_file_location(f"migration_{match[1]}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found.append((int(match[1]), match[2], module))
    # A gap or duplicate means a lost or misnamed file; never guess the order.
    if [version for version, _, _ in found] != list(range(1, len(found) + 1)):
        raise RuntimeError("Migrations non consécutives : " + ", ".join(f"{v:04d}" for v, _, _ in found))
    return found


def recorded(con):
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone() is None:
        return set()
    return {row[0] for row in con.execute("SELECT version FROM schema_migrations")}


def fingerprint(module):
    return hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()


def changed(db, folder):
    """Migrations whose file changed since they ran (recorded before fingerprints existed: not known, not reported)."""
    db = Path(db)
    if not db.is_file():
        return []
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
        if not any(r[1] == "empreinte" for r in con.execute("PRAGMA table_info(schema_migrations)")):
            return []
        saved = dict(con.execute("SELECT version, empreinte FROM schema_migrations WHERE empreinte IS NOT NULL"))
    return [(v, n) for v, n, module in available(folder) if v in saved and saved[v] != fingerprint(module)]


def shape(con):
    """Tables (columns, foreign keys, row count) and index names of a base."""
    tables = {}
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                               "AND name != 'schema_migrations'").fetchall():
        columns = {(r[1], (r[2] or "").upper(), r[3], r[5]) for r in con.execute(f'PRAGMA table_info("{name}")')}
        keys = {(r[2], r[3], r[4]) for r in con.execute(f'PRAGMA foreign_key_list("{name}")')}
        tables[name] = (columns, keys, con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
    indexes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
    return tables, indexes


def gaps(con, version, migrations):
    """What the base lacks of the schema that migrations 1 to version build on an empty base."""
    with closing(sqlite3.connect(":memory:")) as model:
        for number, _, module in migrations:
            if number <= version:
                module.appliquer(model)
        want, want_indexes = shape(model)
    have, have_indexes = shape(con)
    out = []
    for name, (columns, keys, rows) in sorted(want.items()):
        if name not in have:
            out.append(f"table {name}")
            continue
        out += [f"colonne {name}.{c[0]}" for c in sorted(columns - have[name][0])]
        out += [f"clé étrangère {name}.{k[1]}" for k in sorted(keys - have[name][1])]
        if have[name][2] < rows:
            out.append(f"données de référence de {name}")
    return out + [f"index {i}" for i in sorted(want_indexes - have_indexes)]


def pending(db, folder):
    db = Path(db)
    if not db.is_file():
        return available(folder)
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
        done = recorded(con)
    return [m for m in available(folder) if m[0] not in done]


def migrate(db, backups, folder):
    """Apply pending migrations after a verified backup of an existing base.

    Returns (done, backup): done lists (version, nom, 'appliquée'|'adoptée'); backup is None for a new base.
    """
    db = Path(db)
    todo = pending(db, folder)
    if not todo:
        return [], None
    backup = backup_database(db, backups) if db.is_file() else None
    if backup is None:
        # A new base holds private data from its first row: owner-only, like the others.
        db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db.touch(mode=0o600)
    con = sqlite3.connect(db, isolation_level=None)
    done = []
    every = available(folder)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        for version, name, module in todo:
            # A migration that rebuilds a table other tables reference (SQLite cannot change a CHECK in place) says so:
            # foreign keys are off for it alone (the pragma has no effect inside a transaction), and the foreign key check
            # below still refuses any broken reference before the commit.
            off = getattr(module, "CLES_ETRANGERES_COUPEES", False) is True
            if off:
                con.execute("PRAGMA foreign_keys=OFF")
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                            "nom TEXT NOT NULL, appliquee_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, effet TEXT NOT NULL, empreinte TEXT)")
                if not any(r[1] == "empreinte" for r in con.execute("PRAGMA table_info(schema_migrations)")):
                    con.execute("ALTER TABLE schema_migrations ADD COLUMN empreinte TEXT")
                effect = "adoptée" if module.deja_appliquee(con) else "appliquée"
                if effect == "appliquée":
                    module.appliquer(con)
                    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'").fetchone():
                        con.execute("INSERT INTO audit_log(action, cible_type, details, source, resultat) "
                                    "VALUES ('migration', 'schema', ?, 'assistant', 'succes')", (f"{version:04d} {name}",))
                else:
                    missing = gaps(con, version, every)
                    if missing:
                        raise RuntimeError(f"{version:04d} {name} : la base a une partie seulement du schéma attendu (il manque "
                                           + ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "") + ") : rien n'est adopté.")
                if con.execute("PRAGMA foreign_key_check").fetchone() is not None or \
                        con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"{version:04d} {name} : intégrité invalide après la migration; rien n'est enregistré.")
                con.execute("INSERT INTO schema_migrations(version, nom, effet, empreinte) VALUES (?, ?, ?, ?)",
                            (version, name, effect, fingerprint(module)))
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                if off:
                    con.execute("PRAGMA foreign_keys=ON")
            done.append((version, name, effect))
    finally:
        con.close()
    return done, backup
