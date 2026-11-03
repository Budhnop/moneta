"""Où Moneta garde ses données (spec 013, R6) : ~/_PersonalAgents/donnees/moneta, sous-dossiers en 700 et fichiers en 600.

MONETA_HOME déplace tout d'un coup, pour des données fictives seulement : un dossier sous ~/_PersonalAgents n'est jamais suivi,
la vraie base reste où elle est. MONETA_FINANCES_DB pointe les programmes enfants sur la copie d'un lot ou d'une lecture.
L'emplacement du code ne dépend d'aucun de ces réglages.

Revue T074 de Smith, R8 : MONETA_DRIVE_DOSSIER n'est suivie qu'avec un MONETA_HOME suivi. MONETA_FINANCES_DB ne peut pas l'être
de même : le serveur réel la pose pour les étapes de son lot, sur une copie dans un dossier temporaire; chaque programme de tête
(serveur, migration, vérificateur, nuit, rapport) appelle donc environnement_reel() au démarrage, qui retire ce qu'il a hérité.
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


VARIABLES_DE_TEST = ("MONETA_FINANCES_DB", "MONETA_DRIVE_DOSSIER")


def environnement_reel():
    """Au démarrage d'un programme de tête, sur les vraies données (aucun MONETA_HOME suivi) : les variables qui déplacent la
    base ou le Drive, héritées du terminal ou de l'app (qui passe son environnement), sont retirées (R8). Les enfants du lot
    reçoivent ensuite celle que le serveur pose lui-même."""
    if home() == REAL_HOME:
        for variable in VARIABLES_DE_TEST:
            os.environ.pop(variable, None)


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
    demande = os.environ.get("MONETA_DRIVE_DOSSIER") if home() != REAL_HOME else None
    return Path(demande
                or Path.home() / "Library/CloudStorage/GoogleDrive-ugo@dojod.ca/My Drive").expanduser()


def factures_import():
    """Chaque niveau en 700, pas seulement le dernier : private_folder laisse ses parents en 755 (revue T074 de Smith, R9)."""
    dossier = home()
    for nom in ("private", "app", "factures"):
        dossier = private_folder(dossier / nom)
    return dossier
