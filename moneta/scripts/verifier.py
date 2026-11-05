"""État propre de Moneta (spec 013, contrat verificateur-agents) : base lisible, migrations à jour, dossiers à Ugo seulement,
le dossier « à classer » des factures dans Google Drive (contrôle repris de smith-verifier, T051), le lecteur de relevés PDF
(T066) et, depuis la spec 018 (T024), l'âge de la dernière copie vérifiée du Mac, du disque et de Drive.

--json : {"version": 2, "ok", "controles": [{"ok", "texte", "corriger", "attention"}]}, lu par smith-verifier (30 secondes, sans
entrée). « attention » (booléen) est un ⚠️ : toujours avec ok vrai, jamais un ❌; il ne rend pas l'état propre faux. Les trois
derniers contrôles disent l'âge de la dernière copie vérifiée (socle.fraicheur), lu dans le journal de la nuit
(private/sauvegarde-quotidienne.log), sans ouvrir aucune image.
Les textes sont fermés : aucun texte de la base n'en sort, seulement les chemins de Moneta.
"""
from contextlib import closing
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.chemins import PAQUET, environnement_reel, finances_db, home, releve  # noqa: E402
from moneta.outils.factures import INBOX, inbox_present  # noqa: E402
from socle import fraicheur  # noqa: E402
from socle.migrations import changed, pending  # noqa: E402

MIGRATIONS = PAQUET / "migrations" / "finances"
VERSION = 2
MIGRER = "bin/moneta-migrer --appliquer"
LABEL = "ca.dojod.moneta.sauvegarde"
JOURNAL = "sauvegarde-quotidienne.log"
IMAGE = re.compile(r"^moneta-(?:mensuelle-)?\d{4}-\d{2}-\d{2}_\d{6}\.dmg$")
LIGNE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (OK|ATTENTION|ERREUR) (.+?) : (.*)$")
# Les étapes du journal de la nuit : le Mac (la copie en clair), le disque, puis Drive.
ETAPE_MAC = "local"
ETAPE_DISQUE = "disque"
ETAPE_DRIVE = "hors Mac"


def controle(ok, texte, corriger="", attention=False):
    """Un contrôle du contrat v2 : attention (⚠️) suppose ok, et garde son corriger."""
    return {"ok": ok, "texte": texte[:200], "corriger": "" if ok and not attention else corriger[:200],
            "attention": attention and ok}


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


def journal_de_nuit(racine):
    """Les lignes (moment, niveau, étape, détail) du journal de la nuit de Moneta; aucune si le journal manque ou est illisible.
    Lecture seule : le dossier private/ n'est pas créé ici (log_path() de la nuit le créerait)."""
    lignes = []
    try:
        texte = (Path(racine) / "private" / JOURNAL).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return lignes
    for ligne in texte.splitlines():
        trouve = LIGNE.match(ligne)
        if not trouve:
            continue
        try:
            lignes.append((datetime.strptime(trouve[1], "%Y-%m-%d %H:%M"), trouve[2], trouve[3], trouve[4]))
        except ValueError:  # une date impossible (2026-13-45) n'est pas une ligne du journal
            continue
    return lignes


def sauvegardes(lignes, aujourdhui=None):
    """Trois contrôles, toujours présents, dans l'ordre Mac, disque, Drive : l'âge de la dernière copie vérifiée."""
    aujourdhui = aujourdhui or date.today()
    trouvees = (("mac", fraicheur.derniere(lignes, ETAPE_MAC, IMAGE, par_ligne=True)),
                ("disque", fraicheur.derniere(lignes, ETAPE_DISQUE, IMAGE)),
                ("drive", fraicheur.derniere(lignes, ETAPE_DRIVE, IMAGE)))
    jugements = [fraicheur.juger("Moneta", destination, jour, aujourdhui, LABEL) for destination, jour in trouvees]
    return [controle(j["ok"], j["texte"], j["corriger"], j["attention"]) for j in jugements]


def verifier():
    controles = [donnees(home()), *base(finances_db()), factures(), lecteur(), *sauvegardes(journal_de_nuit(home()))]
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
            marque = "❌ " if not c["ok"] else "⚠️ " if c["attention"] else "✅ "
            print(marque + c["texte"] + (f" — corriger : {c['corriger']}" if not c["ok"] or c["attention"] else ""))
    return 0 if valeur["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
