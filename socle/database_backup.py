"""Create a verified private backup without opening production through SQLite."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile


def backup_database(source, directory):
    source, directory = Path(source), Path(directory)
    if not source.is_file():
        raise ValueError("Base source introuvable.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    files = [Path(str(source) + suffix) for suffix in ("", "-wal")]

    def signature():
        return [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in files]

    before = signature()
    with tempfile.TemporaryDirectory(prefix="smith-backup-read-") as folder:
        snapshot = Path(folder) / "source.db"
        for path, suffix in zip(files, ("", "-wal")):
            if path.exists():
                shutil.copyfile(path, Path(str(snapshot) + suffix))
        if signature() != before:
            raise RuntimeError("Base modifiée pendant la copie; réessayer avec la source au repos.")
        with tempfile.NamedTemporaryFile(prefix="dojo-smith-", suffix=".db", dir=directory, delete=False) as file:
            target = Path(file.name)
        try:
            # Closed before returning: a copy still open when the caller moves it leaves its -wal and -shm behind.
            with closing(sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)) as original, \
                    closing(sqlite3.connect(target)) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("Intégrité de sauvegarde invalide.")
                if backup.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Clés étrangères de sauvegarde invalides.")
            return target
        except Exception:
            target.unlink(missing_ok=True)
            raise
