"""État propre de Moneta (spec 013, contrat verificateur-agents) : base lisible, migrations à jour, dossiers à Ugo seulement,
le dossier « à classer » des factures dans Google Drive (contrôle repris de smith-verifier, T051) et le lecteur de relevés PDF
(T066).

--json : {"version": 1, "ok", "controles": [{"ok", "texte", "corriger"}]}, lu par smith-verifier (30 secondes, sans entrée).
Les textes sont fermés : aucun texte de la base n'en sort, seulement les chemins de Moneta.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.chemins import PAQUET, environnement_reel, finances_db, home, releve  # noqa: E402
from moneta.outils.factures import INBOX, inbox_present  # noqa: E402
from socle.migrations import changed, pending  # noqa: E402

MIGRATIONS = PAQUET / "migrations" / "finances"
VERSION = 1
MIGRER = "bin/moneta-migrer --appliquer"


def controle(ok, texte, corriger=""):
    return {"ok": ok, "texte": texte[:200], "corriger": "" if ok else corriger[:200]}


def donnees(racine):
    """Dossiers en 700, fichiers en 600 sauf le lecteur de relevés en 700, aucun lien, tout à l'utilisateur."""
    programme = os.path.join(racine, "apps", "moneta-releve")
    try:
        if not racine.exists():
            return controle(True, "Données de Moneta : dossier absent")
        mauvais = False
        for dossier, sous, fichiers in os.walk(racine, followlinks=False):
            chemins = [os.path.join(dossier, f) for f in fichiers]
            for nom, attendu in [(dossier, 0o700)] + [(c, 0o700 if c == programme else 0o600) for c in chemins] \
                    + [(os.path.join(dossier, s), None) for s in sous]:
                info = os.lstat(nom)
                if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() \
                        or (attendu is not None and stat.S_IMODE(info.st_mode) != attendu):
                    mauvais = True
    except OSError:
        mauvais = True
    if mauvais:
        return controle(False, "Données de Moneta : mode, lien ou propriétaire",
                        f"find {racine} -type d -exec chmod 700 {{}} + ; find {racine} -type f -exec chmod 600 {{}} + "
                        "; retirer les liens; puis bin/moneta-construire-releve")
    return controle(True, "Données de Moneta : 700 et 600, aucun lien")


def base(db):
    if not db.is_file():
        return [controle(False, "Base finances.db absente", f"déménager Finances de Smith, sinon {MIGRER}")]
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
            con.execute("PRAGMA query_only=ON")
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError
        todo, drift = pending(db, MIGRATIONS), changed(db, MIGRATIONS)
    except (OSError, sqlite3.Error):
        return [controle(False, "Base finances.db illisible", "restaurer la dernière sauvegarde vérifiée (backups/database/moneta)")]
    return [controle(True, "Base finances.db lisible"),
            controle(not todo, "Migrations à jour" if not todo else "Migrations en attente", MIGRER),
            controle(not drift, "Migrations inchangées" if not drift else "Migration modifiée depuis son application",
                     "revoir moneta/migrations/finances avant toute écriture")]


def factures():
    """Le dossier où Ugo dépose les factures, comme smith-verifier le contrôlait avant la spec 013 : son nom seulement."""
    return controle(inbox_present(), f"Dossier des factures « {INBOX} » dans Google Drive",
                    "le créer dans Google Drive (depuis le Mac ou le téléphone)")


def lecteur():
    """Le lecteur de relevés PDF, comme smith-verifier le contrôlait avant la spec 013 : un fichier exécutable, jamais un lien."""
    chemin = releve()
    present = chemin.is_file() and not chemin.is_symlink() and os.access(chemin, os.X_OK)
    return controle(present, "Lecteur de relevés PDF présent (moneta-releve)",
                    "lancer bin/moneta-construire-releve (sans lui, aucun relevé PDF ne se lit)")


def verifier():
    controles = [donnees(home()), *base(finances_db()), factures(), lecteur()]
    return {"version": VERSION, "ok": all(c["ok"] for c in controles), "controles": controles}


def main(argv):
    if argv not in ([], ["--json"]):
        print("Usage : moneta-verifier [--json]", file=sys.stderr)
        return 2
    environnement_reel()
    valeur = verifier()
    if argv == ["--json"]:
        sys.stdout.write(json.dumps(valeur, ensure_ascii=False) + "\n")
    else:
        for c in valeur["controles"]:
            print(("✅ " if c["ok"] else "❌ ") + c["texte"] + ("" if c["ok"] else f" — corriger : {c['corriger']}"))
    return 0 if valeur["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
