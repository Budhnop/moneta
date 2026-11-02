"""La copie vérifiée qu'un serveur lit : une par processus, rafraîchie quand la source change, effacée à la fin."""
import os
from pathlib import Path
import shutil

from socle.database_backup import backup_database
from socle.depot_sqlite import source_signature


def remove(db):
    # SQLite may leave -wal/-shm sidecars after a read.
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class CopiePrivee:
    """variable : la variable d'environnement qui pointe les programmes enfants sur la copie.
    anciens : préfixes de fichiers laissés par d'anciennes versions de l'agent, effacés par purge_stale."""

    def __init__(self, source, dossier, variable, anciens=()):
        # Claude Desktop and a manual run may serve at the same time: each owns its own folder.
        self.source, self.root, self.variable, self.anciens = Path(source), Path(dossier), variable, tuple(anciens)
        self.directory = self.root / str(os.getpid())
        self.signature = self.current = None

    def refresh(self):
        signature = source_signature(self.source)
        if signature == self.signature and self.current is not None and self.current.exists():
            return
        copy = backup_database(self.source, self.directory)
        copy.chmod(0o600)
        previous, self.current, self.signature = self.current, copy, signature
        os.environ[self.variable] = str(copy)
        if previous is not None:
            remove(previous)

    def purge_stale(self):
        # Copies left behind by servers that stopped abruptly; live servers keep theirs.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in self.root.iterdir():
            if path.is_dir() and path.name.isdigit() and not alive(int(path.name)):
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file() and self.anciens and path.name.startswith(self.anciens):
                path.unlink(missing_ok=True)

    def purge(self):
        shutil.rmtree(self.directory, ignore_errors=True)
        self.current = None
