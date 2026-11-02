"""Le monde fictif des tests de Moneta : sa base, la fausse fenêtre du Mac, et un Dojo fictif tenu par Smith.

Le Dojo n'est jamais construit ici : la base fictive est faite par le code de Smith (scripts.smith_mcp.seed_fictive), et ses
paiements sont lus par le vrai bin/smith-lecture-paiements, comme en service (contrat lecture-paiements-moneta). Le dépôt de
Smith est MONETA_TESTS_SMITH, par défaut ~/_PersonalAgents/smith. Rien n'y est lu de réel : UGOD_HOME et UGOD_DB sont fictifs.
"""
import os
from pathlib import Path
import subprocess

from moneta.chemins import PAQUET
from moneta.outils.lot import SourceStore  # noqa: F401  la base des lots, comme dans les tests de Smith
from moneta.outils.mcp_finances import VARIABLE
from socle import confirmation
from socle.migrations import migrate

FOLDERS = {'finances': PAQUET / 'migrations' / 'finances'}
SMITH = Path(os.environ.get('MONETA_TESTS_SMITH') or Path.home() / '_PersonalAgents' / 'smith').expanduser()


def seed_fictive_finances(folder):
    """Base Finances fictive vide (schéma, compte Dojo et catégories T2125 des migrations)."""
    db = Path(folder) / 'finances.db'
    migrate(db, Path(folder) / 'backups', FOLDERS['finances'])
    db.chmod(0o600)
    os.environ[VARIABLE] = str(db)
    return db


def seed_fictive(folder):
    """Dojo fictif de Smith dans folder (familles, une inscription, un paiement de 50 $), lu par le vrai programme de Smith."""
    return _dojo(folder, 'from scripts.smith_mcp import seed_fictive; print(seed_fictive(sys.argv[1]))')


def seed_database(folder):
    """Dojo fictif de Smith sans paiement (core.fictif.seed_database), dans folder/dojo.db."""
    return _dojo(folder, 'from pathlib import Path; from core.fictif import seed_database; '
                         'print(seed_database(Path(sys.argv[1]) / "dojo.db"))')


def _dojo(folder, code):
    folder = Path(folder)
    smith_home, drive = folder / 'smith', folder / 'smith-drive'
    drive.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('UGOD_', 'MONETA_'))}
    env.update(UGOD_HOME=str(smith_home), UGOD_DRIVE_DOSSIER=str(drive), PYTHONPATH=str(SMITH / 'assistant'),
               PYTHONDONTWRITEBYTECODE='1')
    fait = subprocess.run([str(SMITH / '.venv' / 'bin' / 'python'), '-c',
                           'import sys; ' + code, str(folder)],
                          cwd=SMITH / 'assistant', env=env, capture_output=True, text=True, timeout=60, check=True)
    db = Path(fait.stdout.strip().splitlines()[-1])
    # Le programme de Smith suit UGOD_HOME et UGOD_DB parce qu'ils sont fictifs; il ne lit jamais la vraie base.
    os.environ.update(UGOD_HOME=str(smith_home), UGOD_DB=str(db), UGOD_DRIVE_DOSSIER=str(drive),
                      MONETA_LECTURE_PAIEMENTS=str(SMITH / 'bin' / 'smith-lecture-paiements'))
    return db


class FausseFenetre:
    """Fake Mac window for tests (spec 002, T002): counts its openings and keeps every line it showed.

    It replaces confirmation.ask while active. The real check() still runs first, so a summary too
    tall or blurred is refused the same way and never counts as an opening. `reponse` is the click: True for
    « Enregistrer », False for Annuler, a closed window or no answer."""

    def __init__(self, reponse=True):
        self.reponse = reponse
        self.ouvertures = []  # one (title, lines) per window opened, in order

    @property
    def nombre(self):
        return len(self.ouvertures)

    @property
    def lignes(self):
        """Every line shown, all windows together."""
        return [line for _, lines in self.ouvertures for line in lines]

    def _window(self, title, lines):
        lines = list(lines)
        confirmation.check(lines)
        self.ouvertures.append((title, lines))
        return self.reponse

    def __enter__(self):
        self._avant = confirmation.ask
        confirmation.ask = self._window
        return self

    def __exit__(self, *exc):
        confirmation.ask = self._avant
        return False
