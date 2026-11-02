"""Le déménagement des données de Finances, de donnees/smith/ à donnees/moneta/, et son retour (spec 013 de Smith, récit 6, R11,
contrat demenagement, EF-012).

--inventaire [DOSSIER] : chemins, tailles et empreintes de ce qui bouge, sans contenu (par défaut le vrai donnees/).
--repeter DOSSIER      : un donnees/ fictif complet, hors de ~/_PersonalAgents : chaque refus, un échec injecté à chaque étape,
                         puis aller, écriture fictive, retour et aller, empreintes comparées à chaque passage.
--appliquer, --retour  : le vrai donnees/, après le récapitulatif et le mot tapé par Ugo (« déménager », « retour »).

Tout se fait avant le moindre changement ou pas du tout : refus, puis sauvegarde vérifiée de finances.db, copie de chaque fichier
dans un dossier de passage, empreintes SHA-256 comparées à l'original, signature de la base comparée, puis bascule par renommage
(la copie à sa place, l'original de côté en 600). Un échec pendant la bascule ou la note remet les renommages faits; une remise
impossible lève Bloque avec la liste exacte des fichiers qui ne sont pas à leur place. Aucun appel à Git : le commit de chaque dépôt
est lu dans ses fichiers. Aucun outil MCP ni l'app n'appelle ce programme; Ugo le lance dans le terminal.
"""
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.chemins import CODE, PAQUET, PERSONNEL, _hors_personnel  # noqa: E402
from socle.chemins import private_folder, write_private  # noqa: E402
from socle.database_backup import backup_database  # noqa: E402
from socle.depot_sqlite import source_signature  # noqa: E402
from socle.lots import STEP_SECONDS  # noqa: E402
from socle.migrations import pending  # noqa: E402

DONNEES = PERSONNEL / "donnees"
SMITH = PERSONNEL / "smith"
MIGRATIONS = PAQUET / "migrations" / "finances"
MOT_ALLER, MOT_RETOUR = "déménager", "retour"
BASE_SMITH, BASE_MONETA = "smith/database/finances.db", "moneta/database/finances.db"
ARCHIVE_ALLER = "smith/private/avant-moneta"      # les originaux de Smith, jusqu'à la décision d'Ugo (CR-007)
ARCHIVE_RETOUR = "moneta/private/avant-retour"    # les fichiers de Moneta d'avant un retour
SAUVEGARDE_ALLER = "smith/backups/database/avant-moneta"
SAUVEGARDE_RETOUR = "moneta/backups/database/avant-retour"
PASSAGE_ALLER, PASSAGE_RETOUR = "moneta/.passage", "smith/.passage"
NOTE_ALLER, NOTE_RETOUR = "moneta/private/demenagement.json", "smith/private/retour-moneta.json"

# Les dossiers qui bougent (data-model, Inventaire du déménagement) : (de, vers, motif des fichiers, tout le sous-arbre).
# Le lecteur apps/agent-smith-releve n'y est pas : il est reconstruit par bin/moneta-construire-releve, jamais copié.
# backups/database/mcp-lecture-finances/ reste : des copies de processus, effacées.
DOSSIERS = (
    ("smith/database", "moneta/database", r"finances\.db(-wal|-shm)?", False),
    ("smith/backups/database/smith-finances", "moneta/backups/database/moneta", r".+", True),
    ("smith/backups/database/daily", "moneta/backups/database/daily", r"finances_.+\.db", False),
    ("smith/imports", "moneta/imports", r".+", True),
    ("smith/exports", "moneta/exports", r"rapport-finances-.+\.html", False),
    ("smith/private", "moneta/private", r"sauvegarde-pieces\.txt", False),
    ("smith/journal-ecritures", "journal-ecritures", r".+", True),  # le journal commun (R2, D110)
)

# Les lignes de `ps` qui empêchent le déménagement (libellé, motif).
PROCESSUS = (
    ("Claude Desktop", r"/Claude\.app/Contents/MacOS/Claude(\s|$)"),
    ("app Smith", r"smith_app\.py"),
    ("serveur Smith", r"smith_mcp\.py"),
    ("serveur Moneta", r"moneta_mcp\.py"),
    ("tâche de nuit", r"(smith|moneta)-sauvegarde-quotidienne|sauvegarde_quotidienne\.py"),
)


class Refus(Exception):
    """Rien n'a changé (sauf, pour une base modifiée pendant la copie, la sauvegarde faite avant)."""


class Bloque(Exception):
    """Une remise en place a échoué : chemins, relatifs à donnees/, sont les fichiers qui ne sont pas à leur place."""

    def __init__(self, chemins, details):
        self.chemins = list(chemins)
        super().__init__("Remise incomplète, arrêt. Ces fichiers ne sont pas à leur place : " + "; ".join(details)
                         + ". Remets-les à la main avant toute autre chose.")


def processus_en_cours(lignes):
    """Les libellés des processus trouvés dans les lignes de `ps -axo command=`, chacun une fois, dans l'ordre."""
    trouves = []
    for ligne in lignes:
        for libelle, motif in PROCESSUS:
            if re.search(motif, ligne) and libelle not in trouves:
                trouves.append(libelle)
    return trouves


def _ps():
    fait = subprocess.run(["/bin/ps", "-axo", "command="], capture_output=True, text=True, timeout=30, check=True)
    return processus_en_cours(fait.stdout.splitlines())


def _lot_reel():
    # Un lot vit dans la mémoire de son serveur et meurt avec lui : sans serveur Smith ni Moneta (refusé avant), aucun lot
    # ni aucune étape ne peut être en attente.
    return []


@dataclass(frozen=True)
class Sondes:
    """processus() : les libellés de ce qui roule; lot_finances() : l'âge en secondes de chaque étape du lot Finances;
    uid : le propriétaire attendu de chaque fichier."""
    processus: Callable = _ps
    lot_finances: Callable = _lot_reel
    uid: int = os.getuid()


def sha(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        for bloc in iter(lambda: fichier.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _fichiers(donnees, dossiers):
    """[(de, vers)] relatifs à donnees/, triés; un lien, même vers un dossier, y est gardé pour être refusé."""
    donnees = Path(donnees)
    trouves = []
    for de, vers, motif, arbre in dossiers:
        racine = donnees / de
        if racine.is_symlink():
            trouves.append((de, vers))
            continue
        if not racine.is_dir():
            continue
        if arbre:
            for dossier, sous, noms in os.walk(racine):
                noms = noms + [n for n in sous if (Path(dossier) / n).is_symlink()]
                for nom in noms:
                    relatif = (Path(dossier) / nom).relative_to(racine).as_posix()
                    trouves.append((f"{de}/{relatif}", f"{vers}/{relatif}"))
        else:
            for chemin in racine.iterdir():
                if re.fullmatch(motif, chemin.name) and (chemin.is_symlink() or chemin.is_file()):
                    trouves.append((f"{de}/{chemin.name}", f"{vers}/{chemin.name}"))
    return sorted(trouves)


def _retour_dossiers():
    return tuple((vers, de, motif, arbre) for de, vers, motif, arbre in DOSSIERS)


def _inventorier(donnees, dossiers):
    donnees = Path(donnees)
    return [{"de": de, "vers": vers, "taille": (donnees / de).lstat().st_size,
             "sha256": None if (donnees / de).is_symlink() else sha(donnees / de)}
            for de, vers in _fichiers(donnees, dossiers)]


def inventaire(donnees):
    """Ce qui part de donnees/smith/ : [{'de', 'vers', 'taille', 'sha256'}], sans contenu."""
    return _inventorier(donnees, DOSSIERS)


@dataclass(frozen=True)
class Sens:
    nom: str
    dossiers: tuple
    base_de: str
    base_vers: str
    mot: str
    passage: str
    archive: str
    sauvegarde: str
    note: str


ALLER = Sens("aller", DOSSIERS, BASE_SMITH, BASE_MONETA, MOT_ALLER, PASSAGE_ALLER, ARCHIVE_ALLER, SAUVEGARDE_ALLER, NOTE_ALLER)
RETOUR = Sens("retour", _retour_dossiers(), BASE_MONETA, BASE_SMITH, MOT_RETOUR, PASSAGE_RETOUR, ARCHIVE_RETOUR,
              SAUVEGARDE_RETOUR, NOTE_RETOUR)


def _refus_avant(donnees, sens, sondes, fichiers):
    en_cours = list(sondes.processus())
    if en_cours:
        raise Refus(f"En cours : {', '.join(en_cours)}. Quitte-les (et attends la fin de la tâche de nuit), puis recommence.")
    ages = list(sondes.lot_finances())
    if any(age < STEP_SECONDS for age in ages):
        raise Refus(f"Le lot Finances a des étapes de moins de {STEP_SECONDS // 60} minutes; attends et vide-le d'abord.")
    if ages:
        raise Refus("Le lot Finances n'est pas vide; vide-le d'abord.")
    if not (donnees / sens.base_de).is_file():
        raise Refus(f"{sens.base_de} absent : rien à faire dans ce sens.")
    if (donnees / sens.base_vers).exists() or (donnees / sens.base_vers).is_symlink():
        raise Refus(f"{sens.base_vers} déjà présent : refusé.")
    for dossier in {sens.passage, *(d for d, *_ in sens.dossiers)}:
        if (donnees / dossier).is_symlink():
            raise Refus(f"Lien à la place d'un dossier : {dossier}.")
    for f in fichiers:
        chemin = donnees / f["de"]
        if chemin.is_symlink() or not chemin.is_file():
            raise Refus(f"Lien ou fichier spécial dans l'inventaire : {f['de']}.")
        if chemin.lstat().st_uid != sondes.uid:
            raise Refus(f"Fichier dont le propriétaire n'est pas Ugo : {f['de']}.")
        cible = donnees / f["vers"]
        if cible.exists() or cible.is_symlink():
            raise Refus(f"{f['vers']} déjà présent : refusé.")
    if (donnees / sens.passage).exists():
        raise Refus(f"{sens.passage} reste d'un essai interrompu : à regarder avant de recommencer.")
    attente = pending(donnees / sens.base_de, MIGRATIONS)
    if attente:
        raise Refus("Migration Finances en attente : " + ", ".join(f"{v:04d}" for v, _, _ in attente)
                    + ". Lance bin/moneta-migrer --appliquer d'abord.")


def recapitulatif(sens, fichiers):
    taille = sum(f["taille"] for f in fichiers)
    lignes = [f"{'Déménagement' if sens is ALLER else 'Retour'} de {len(fichiers)} fichier(s), {taille} octets :"]
    lignes += [f"  {f['de']} → {f['vers']}" for f in fichiers]
    lignes += [f"Sauvegarde vérifiée de la base dans {sens.sauvegarde}/, originaux gardés dans {sens.archive}/.",
               f"Tape « {sens.mot} » pour continuer; tout autre mot ne fait rien."]
    if sens is RETOUR:
        lignes.append("Le code revient au commit noté dans moneta/private/demenagement.json par git switch, fait par Ugo.")
    return "\n".join(lignes)


def _commit(depot):
    """Le commit de HEAD, lu dans les fichiers du dépôt (worktree compris), sans lancer Git; None s'il est illisible."""
    try:
        meta = Path(depot) / ".git"
        if meta.is_file():
            meta = (Path(depot) / meta.read_text(encoding="utf-8").split(":", 1)[1].strip()).resolve()
        commun = meta
        if (meta / "commondir").is_file():
            commun = (meta / (meta / "commondir").read_text(encoding="utf-8").strip()).resolve()
        tete = (meta / "HEAD").read_text(encoding="utf-8").strip()
        if not tete.startswith("ref: "):
            return tete
        ref = tete[5:]
        for dossier in (meta, commun):
            if (dossier / ref).is_file():
                return (dossier / ref).read_text(encoding="utf-8").strip()
        for ligne in (commun / "packed-refs").read_text(encoding="utf-8").splitlines():
            if ligne.endswith(" " + ref):
                return ligne.split(" ", 1)[0]
    except (OSError, IndexError):
        pass
    return None


def _horodatage():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")


def _rien(etape):
    return None


def _creer(racine, dossier):
    """Chaque dossier manquant de racine jusqu'à dossier, créé en 700; ceux qui existent restent tels quels."""
    for niveau in [*reversed(Path(dossier).relative_to(racine).parents[:-1]), Path(dossier).relative_to(racine)]:
        if not (racine / niveau).is_dir():
            private_folder(racine / niveau)


def _deplacer(sens, donnees, sondes, go, crochet):
    donnees = Path(donnees)
    crochet = crochet or _rien
    fichiers = _inventorier(donnees, sens.dossiers)
    _refus_avant(donnees, sens, sondes, fichiers)
    if go(recapitulatif(sens, fichiers)) != sens.mot:
        raise Refus(f"Rien n'est fait : il faut taper exactement « {sens.mot} ».")
    base = donnees / sens.base_de
    signature = source_signature(base)
    stamp = _horodatage()

    # 1. Sauvegarde vérifiée de la base.
    crochet("sauvegarde")
    copie = backup_database(base, private_folder(donnees / sens.sauvegarde))
    copie.chmod(0o600)
    copie.rename(copie.with_name(f"finances-{stamp}.db"))

    # 2 et 3. Copie dans le passage, puis empreintes comparées à l'original; la base ne doit pas avoir bougé.
    passage = donnees / sens.passage
    try:
        crochet("copie")
        for f in fichiers:
            cible = passage / f["vers"]
            _creer(donnees, cible.parent)
            shutil.copyfile(donnees / f["de"], cible, follow_symlinks=False)
            cible.chmod(0o600)
        crochet("empreintes")
        ecarts = [f["de"] for f in fichiers
                  if sha(passage / f["vers"]) != f["sha256"] or sha(donnees / f["de"]) != f["sha256"]]
        if source_signature(base) != signature:
            raise Refus(f"{sens.base_de} a changé pendant la copie : rien n'est déplacé. Ferme ce qui écrit et recommence.")
        if ecarts:
            raise Refus("Empreinte différente après la copie : " + ", ".join(ecarts) + ". Rien n'est déplacé.")
    except BaseException:
        shutil.rmtree(passage, ignore_errors=True)
        raise

    # 4 et 5. Bascule par renommage, puis la note; un échec remet ce qui a été renommé.
    archive = donnees / sens.archive / stamp
    faits = []

    def renommer(de, vers):
        crochet("bascule")
        _creer(donnees, vers.parent)
        os.rename(de, vers)
        faits.append((de, vers))

    try:
        if source_signature(base) != signature:
            raise Refus(f"{sens.base_de} a changé avant la bascule : rien n'est déplacé.")
        for f in fichiers:
            renommer(passage / f["vers"], donnees / f["vers"])
            renommer(donnees / f["de"], archive / f["de"])
        for dossier in {(donnees / f["vers"]).parent for f in fichiers} | {(archive / f["de"]).parent for f in fichiers}:
            dossier.chmod(0o700)
        crochet("note")
        note = {"date": datetime.now().isoformat(timespec="seconds"), "sens": sens.nom,
                "commit_smith": _commit(SMITH), "commit_moneta": _commit(CODE),
                "originaux": sens.archive + "/" + stamp,
                "empreintes": {f["vers"]: f["sha256"] for f in fichiers}}
        chemin_note = donnees / sens.note
        write_private(private_folder(chemin_note.parent), chemin_note.name,
                      (json.dumps(note, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except BaseException:
        _remettre(donnees, faits, crochet)
        shutil.rmtree(passage, ignore_errors=True)
        raise
    shutil.rmtree(passage, ignore_errors=True)
    return note


def _remettre(donnees, faits, crochet):
    while faits:
        de, vers = faits[-1]
        try:
            crochet("remise")
            os.rename(vers, de)
        except BaseException:
            chemins = [v.relative_to(donnees).as_posix() for _, v in reversed(faits)]
            details = [f"{v.relative_to(donnees).as_posix()} (sa place : {d.relative_to(donnees).as_posix()})"
                       for d, v in reversed(faits)]
            raise Bloque(chemins, details) from None
        faits.pop()


def appliquer(donnees, sondes, go, crochet=None):
    """L'aller : de donnees/smith/ à donnees/moneta/ et au journal commun. Rend la note."""
    return _deplacer(ALLER, donnees, sondes, go, crochet)


def retour(donnees, sondes, go, crochet=None):
    """Le retour : les fichiers actuels de Moneta, écritures faites depuis comprises, reviennent dans donnees/smith/."""
    return _deplacer(RETOUR, donnees, sondes, go, crochet)


# --- La répétition sur un dossier fictif ---------------------------------------------------------------------------------

FICTIVES = Sondes(processus=lambda: [], lot_finances=lambda: [], uid=os.getuid())


def _etat(donnees, attendu):
    """'avant', 'fait' ou None, d'après {de: (vers, sha)} : chaque fichier à une seule place, avec son empreinte."""
    donnees = Path(donnees)
    if all(sha(donnees / de) == s if (donnees / de).is_file() else False for de, (_, s) in attendu.items()) \
            and not any((donnees / vers).exists() for vers, _ in attendu.values()):
        return "avant"
    if all(sha(donnees / vers) == s if (donnees / vers).is_file() else False for vers, s in attendu.values()) \
            and not any((donnees / de).exists() for de in attendu):
        return "fait"
    return None


class _Injecte(Exception):
    pass


def _repeter_refus(donnees, dire):
    """Chaque refus du contrat, sur des copies jetables du dossier fictif : rien ne doit changer."""
    with tempfile.TemporaryDirectory(prefix="moneta-repeter-") as jetable:
        def essai(nom, preparer=None, sondes=FICTIVES, mot=MOT_ALLER, crochet=None, sens=appliquer):
            copie = Path(jetable) / "donnees"
            shutil.rmtree(copie, ignore_errors=True)
            shutil.copytree(donnees, copie, symlinks=True)
            if preparer:
                preparer(copie)
            avant = _arbre(copie)
            try:
                sens(copie, sondes, lambda recap: mot, crochet=crochet(copie) if crochet else None)
            except Refus:
                if nom != "base changée pendant la copie" and _arbre(copie) != avant:
                    raise RuntimeError(f"Refus « {nom} » : le dossier a changé.") from None
                dire(f"refus vérifié : {nom}")
                return
            raise RuntimeError(f"Refus attendu et pas obtenu : {nom}.")

        for libelle, _ in PROCESSUS:
            essai(libelle, sondes=Sondes(processus=lambda libelle=libelle: [libelle], lot_finances=lambda: [], uid=os.getuid()))
        essai("lot Finances non vide", sondes=Sondes(processus=lambda: [], lot_finances=lambda: [STEP_SECONDS * 2],
                                                     uid=os.getuid()))
        essai("étapes de moins de 30 minutes", sondes=Sondes(processus=lambda: [], lot_finances=lambda: [60], uid=os.getuid()))
        essai("propriétaire", sondes=Sondes(processus=lambda: [], lot_finances=lambda: [], uid=os.getuid() + 1))
        essai("mot inexact", mot="oui")
        essai("lien", preparer=lambda c: (c / "smith" / "imports").mkdir(parents=True, exist_ok=True)
              or (c / "smith" / "imports" / "lien-essai").symlink_to(c / BASE_SMITH))
        essai("cible déjà présente", preparer=lambda c: private_folder(c / "moneta" / "database")
              and (c / BASE_MONETA).write_bytes(b""))
        essai("retour sans aller", sens=retour)

        def sans_derniere_migration(copie):
            with closing(sqlite3.connect(copie / BASE_SMITH)) as con, con:
                con.execute("DELETE FROM schema_migrations WHERE version = (SELECT MAX(version) FROM schema_migrations)")
        essai("migration en attente", preparer=sans_derniere_migration)

        def ecrire_pendant_la_copie(copie):
            def crochet(etape):
                if etape == "copie":
                    _ecriture_fictive(copie / BASE_SMITH, "pendant la copie")
            return crochet
        essai("base changée pendant la copie", crochet=ecrire_pendant_la_copie)


def _repeter_echecs(donnees, dire, sens, preparer):
    """Un échec injecté à chaque étape d'un sens, chacun sur une copie jetable : avant ou fait, jamais entre les deux."""
    with tempfile.TemporaryDirectory(prefix="moneta-repeter-") as jetable:
        def copie():
            c = Path(jetable) / "donnees"
            shutil.rmtree(c, ignore_errors=True)
            shutil.copytree(donnees, c, symlinks=True)
            preparer(c)
            return c
        vues = []
        sens(copie(), FICTIVES, lambda recap: sens_mot(sens), crochet=vues.append)
        for rang in range(len(vues)):
            c = copie()
            attendu = {f["de"]: (f["vers"], f["sha256"]) for f in _inventorier(c, ALLER.dossiers if sens is appliquer
                                                                                 else RETOUR.dossiers)}
            appels = []

            def crochet(etape):
                appels.append(etape)
                if len(appels) == rang + 1:
                    raise _Injecte(etape)
            try:
                sens(c, FICTIVES, lambda recap: sens_mot(sens), crochet=crochet)
            except _Injecte:
                pass
            etat = _etat(c, attendu)
            if etat is None:
                raise RuntimeError(f"Échec injecté à l'étape {rang + 1} ({vues[rang]}) : ni avant ni fait.")
        dire(f"{sens_nom(sens)} : échec injecté à chacune des {len(vues)} étapes, chaque fois avant ou fait")


def sens_mot(sens):
    return MOT_ALLER if sens is appliquer else MOT_RETOUR


def sens_nom(sens):
    return "aller" if sens is appliquer else "retour"


def _arbre(donnees):
    donnees = Path(donnees)
    trouve = {}
    for dossier, sous, noms in os.walk(donnees):
        for nom in noms + [n for n in sous if (Path(dossier) / n).is_symlink()]:
            chemin = Path(dossier) / nom
            trouve[chemin.relative_to(donnees).as_posix()] = os.readlink(chemin) if chemin.is_symlink() else sha(chemin)
    return trouve


def _ecriture_fictive(base, action):
    with closing(sqlite3.connect(base)) as con, con:
        con.execute("INSERT INTO audit_log (action, resultat) VALUES (?, 'ok')", (action,))


def _passer(donnees, sens, dire):
    attendu = {f["de"]: (f["vers"], f["sha256"]) for f in _inventorier(donnees, ALLER.dossiers if sens is appliquer
                                                                       else RETOUR.dossiers)}
    sens(donnees, FICTIVES, lambda recap: sens_mot(sens))
    arrives = {vers: sha(Path(donnees) / vers) for vers, _ in attendu.values()}
    if arrives != {vers: s for vers, s in attendu.values()} or _etat(donnees, attendu) != "fait":
        raise RuntimeError(f"{sens_nom(sens)} : empreintes différentes à l'arrivée.")
    dire(f"{sens_nom(sens)} : {len(attendu)} fichier(s), empreintes identiques")


def repeter(donnees, dire=print):
    """La répétition complète sur un donnees/ fictif; Refus sur un dossier réel, RuntimeError sur un écart."""
    donnees = Path(donnees).expanduser()
    if not _hors_personnel(donnees):
        raise Refus("--repeter : dossier fictif seulement, hors de ~/_PersonalAgents; rien n'est lu.")
    if not (donnees / BASE_SMITH).is_file() or (donnees / BASE_MONETA).exists():
        raise Refus(f"--repeter : il faut un donnees/ fictif complet, avec {BASE_SMITH} et sans {BASE_MONETA}.")
    _repeter_refus(donnees, dire)
    _repeter_echecs(donnees, dire, appliquer, lambda c: None)
    _repeter_echecs(donnees, dire, retour, lambda c: appliquer(c, FICTIVES, lambda recap: MOT_ALLER))
    _passer(donnees, appliquer, dire)
    _ecriture_fictive(donnees / BASE_MONETA, "écriture fictive du déménagement")
    dire("écriture fictive faite chez Moneta")
    _passer(donnees, retour, dire)
    if _derniere_action(donnees / BASE_SMITH) != "écriture fictive du déménagement":
        raise RuntimeError("retour : l'écriture fictive n'est pas revenue.")
    dire("retour : l'écriture fictive est revenue")
    _passer(donnees, appliquer, dire)


def _derniere_action(base):
    with closing(sqlite3.connect(f"file:{base}?mode=ro", uri=True)) as con:
        ligne = con.execute("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return ligne[0] if ligne else None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="moneta-demenager", description=__doc__.split("\n\n")[0])
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inventaire", nargs="?", const=str(DONNEES), metavar="DOSSIER")
    modes.add_argument("--repeter", metavar="DOSSIER")
    modes.add_argument("--appliquer", action="store_true")
    modes.add_argument("--retour", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.inventaire:
            for f in inventaire(Path(args.inventaire).expanduser()):
                print(f"{f['de']} → {f['vers']}  {f['taille']} octets  {f['sha256'] or 'lien'}")
        elif args.repeter:
            repeter(args.repeter)
            print("OK : répétition complète, chaque refus et chaque échec vérifiés, empreintes identiques à chaque passage.")
        else:
            faire = appliquer if args.appliquer else retour

            def go(recap):
                print(recap)
                try:
                    return input("> ")
                except EOFError:
                    return ""
            note = faire(DONNEES, Sondes(), go)
            print(f"OK : {len(note['empreintes'])} fichier(s), empreintes identiques; note dans "
                  f"{NOTE_ALLER if args.appliquer else NOTE_RETOUR}.")
            if args.retour:
                print("Reste à Ugo : revenir au commit de Smith noté dans moneta/private/demenagement.json (git switch).")
    except Refus as refus:
        print(f"REFUS : {refus}", file=sys.stderr)
        return 1
    except Bloque as bloque:
        print(f"ARRÊT : {bloque}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
