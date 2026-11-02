"""The only path to a source base: verified copies for rehearsal, and one backed-up, all-or-nothing commit.

The commit holds the source's write lock (BEGIN IMMEDIATE) from the precondition check to COMMIT: the steps run on a copy taken
under that lock, then the changed tables are carried into the source in that one transaction. Nothing reaches the source unless
every step succeeded; an interruption leaves SQLite to roll back; no other writer can slip in between, and none is erased by a
restore. The backup before is kept for disasters, never used as a rollback.
"""
from collections import Counter
from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from socle.database_backup import backup_database
from socle.lots import ToolError


@contextmanager
def pointer_variable(path, variable):
    """Child programs read their base from this variable: it aims at the copy for the time of the block."""
    previous = os.environ.get(variable)
    os.environ[variable] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = previous


def source_signature(source):
    # Metadata plus a content digest: the base is small, and a same-size, same-instant edit must still count.
    # An empty -wal holds nothing: the commit's own lock creates one on a WAL base, and that is no change.
    files = [Path(str(source) + suffix) for suffix in ("", "-wal")]
    return [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            if p.exists() and (p.stat().st_size or i == 0) else None for i, p in enumerate(files)]


def carry(work, con):
    """Copy into con's open transaction every table whose rows differ in work; same schema required, foreign keys checked."""
    with closing(sqlite3.connect(f"file:{work}?mode=ro", uri=True)) as done:
        schema = "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex%' ORDER BY type, name"
        if done.execute(schema).fetchall() != con.execute(schema).fetchall():
            raise ToolError("Le schéma de la base a changé pendant l'enregistrement; rien n'est enregistré.")
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_stat%'")]
        for table in tables:
            query = f'SELECT * FROM "{table}"'
            after = done.execute(query)
            columns = [c[0] for c in after.description]
            rows = after.fetchall()
            if Counter(rows) == Counter(con.execute(query).fetchall()):
                continue
            con.execute(f'DELETE FROM "{table}"')
            names = ", ".join(f'"{c}"' for c in columns)
            con.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({", ".join("?" * len(columns))})', rows)
    if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ToolError("Clés étrangères invalides après le lot; rien n'est enregistré.")


class DepotSQLite:
    """executer(step) -> (done, output) runs a step on the copy the variable aims at. deplacer(steps) -> moved and annuler(moved)
    are the hooks for pieces that follow the transaction: moved last, still under the lock, and moved back if COMMIT fails;
    annuler may raise ToolError to say what could not come back."""
    LOCK_SECONDS = 10

    def __init__(self, source, backups, after=None, variable=None, *, executer=None, deplacer=None, annuler=None):
        self.source, self.backups, self.after, self.variable = Path(source), Path(backups), after, variable
        self.executer, self.deplacer, self.annuler = executer, deplacer, annuler

    def signature(self):
        return source_signature(self.source)

    def snapshot(self, directory):
        copy = backup_database(self.source, directory)
        copy.chmod(0o600)
        return copy

    def commit(self, steps, expected=None):
        """expected: signature the lot was rehearsed against; checked under the lock, so no write can come between."""
        try:
            backup_database(self.source, self.backups)
        except Exception:
            raise ToolError("Sauvegarde de sécurité impossible; rien n'est enregistré.") from None
        outputs = []
        try:
            with closing(sqlite3.connect(self.source, timeout=self.LOCK_SECONDS, isolation_level=None)) as con:
                try:
                    con.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    raise ToolError("La base est occupée par une autre écriture; rien n'est enregistré. Réessaie dans un instant.") from None
                try:
                    if expected is not None and self.signature() != expected:
                        raise ToolError("La base a changé depuis le récapitulatif; rien n'est enregistré. "
                                        "Un nouveau récapitulatif et un nouveau oui sont nécessaires.")
                    with tempfile.TemporaryDirectory(prefix="socle-commit-") as folder:
                        work = self.snapshot(folder)
                        with pointer_variable(work, self.variable):
                            for number, step in enumerate(steps, 1):
                                done, output = self.executer(step)
                                if not done:
                                    raise ToolError(f"Étape {number} ({step.title}) refusée : {output} Rien n'est enregistré.")
                                outputs.append(output)
                        carry(work, con)
                    moved = self.deplacer(steps) if self.deplacer else None
                    try:
                        con.execute("COMMIT")
                    except BaseException:
                        if self.annuler:
                            self.annuler(moved)
                        raise
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise
        finally:
            if self.after is not None:
                try:
                    self.after()
                except Exception:
                    # The next read retries the refresh.
                    print("Rafraîchissement de la copie reporté.", file=sys.stderr)
        return outputs
