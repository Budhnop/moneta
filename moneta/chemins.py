"""Où Moneta garde ses données (spec 013, R6) : ~/_PersonalAgents/donnees/moneta, sous-dossiers en 700 et fichiers en 600.

MONETA_HOME déplace tout d'un coup, pour des données fictives seulement : un dossier sous ~/_PersonalAgents n'est jamais suivi,
la vraie base reste où elle est. MONETA_FINANCES_DB pointe les programmes enfants sur la copie d'un lot ou d'une lecture.
L'emplacement du code ne dépend d'aucun de ces réglages.
"""
import os
from pathlib import Path

from socle.chemins import private_folder, write_private  # noqa: F401  les deux mêmes fonctions, pour chaque appelant

CODE = Path(__file__).resolve().parents[1]
PAQUET = CODE / "moneta"
PERSONNEL = Path.home() / "_PersonalAgents"
REAL_HOME = PERSONNEL / "donnees" / "moneta"
SOUS_DOSSIERS = ("database", "backups", "exports", "imports", "private", "apps", "config")


def _hors_personnel(chemin):
    """Vrai seulement loin de ~/_PersonalAgents, comparé par le dossier lui-même (casse, lien ou /System/Volumes/Data compris)."""
    chemin = Path(os.path.abspath(chemin))
    texte = str(PERSONNEL).casefold()
    if any(str(p).casefold() == texte or str(p).casefold().startswith(texte + "/") for p in (chemin, Path(os.path.realpath(chemin)))):
        return False
    try:
        info = os.stat(PERSONNEL)
    except OSError:
        return True
    for dossier in (chemin, *chemin.parents):
        try:
            trouve = os.stat(dossier)
        except OSError:
            continue
        if (trouve.st_dev, trouve.st_ino) == (info.st_dev, info.st_ino):
            return False
    return True


def home():
    demande = os.environ.get("MONETA_HOME")
    if demande and _hors_personnel(Path(demande).expanduser()):
        return Path(demande).expanduser()
    return REAL_HOME


def python():
    """L'environnement Python vit avec le code; le lecteur compilé reste dans les données, les tests n'atteignent jamais le vrai."""
    return CODE / ".venv" / "bin" / "python"


def finances_db():
    return Path(os.environ.get("MONETA_FINANCES_DB") or home() / "database" / "finances.db").expanduser()


def backups():
    return home() / "backups"


def exports():
    """Rapports : des données réelles en clair, dans un dossier privé (voir private_folder)."""
    return home() / "exports"


def imports():
    return home() / "imports"


def apps():
    return home() / "apps"


def releve():
    """Le lecteur de relevés PDF, compilé par bin/moneta-construire-releve."""
    return apps() / "moneta-releve"


def drive():
    """Le Google Drive d'Ugo, par le dossier que l'app Drive garde synchronisé sur le Mac (MONETA_DRIVE_DOSSIER pour les tests)."""
    return Path(os.environ.get("MONETA_DRIVE_DOSSIER")
                or Path.home() / "Library/CloudStorage/GoogleDrive-ugo@dojod.ca/My Drive").expanduser()
