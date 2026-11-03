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

Revue T074 (docs/29_REVUE_013.md de Smith) : les refus sont refaits après le mot tapé et le plan doit être celui du récapitulatif
(R1); aucun renommage n'écrase (lien dur puis effacement : une cible apparue fait échouer et remettre); un mois du journal commun
déjà commencé est fusionné par ajout, sans doublon, l'ancien gardé avec les originaux (R2); le commit de retour de Smith est donné
par Ugo à --appliquer, vérifié et noté à part (R3); aucun niveau d'un chemin touché n'est un lien ni ne sort de donnees/ (R4).
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
import signal
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

# Les dossiers qui bougent (data-model, Inventaire du déménagement) : (de, vers, motif des fichiers, tout le sous-arbre, fusion).
# Fusion : un fichier déjà présent à l'arrivée est fusionné ligne par ligne au lieu d'être refusé (le journal commun, R2).
# Le lecteur apps/agent-smith-releve n'y est pas : il est reconstruit par bin/moneta-construire-releve, jamais copié.
# backups/database/mcp-lecture-finances/ reste : des copies de processus, effacées.
DOSSIERS = (
    ("smith/database", "moneta/database", r"finances\.db(-wal|-shm)?", False, False),
    ("smith/backups/database/smith-finances", "moneta/backups/database/moneta", r".+", True, False),
    ("smith/backups/database/daily", "moneta/backups/database/daily", r"finances_.+\.db", False, False),
    ("smith/imports", "moneta/imports", r".+", True, False),
    ("smith/exports", "moneta/exports", r"rapport-finances-.+\.html", False, False),
    ("smith/private", "moneta/private", r"sauvegarde-pieces\.txt", False, False),
    ("smith/journal-ecritures", "journal-ecritures", r".+", True, True),  # le journal commun (R2, D110)
)
REMPLACES = "remplaces"
PLAN_BASCULE = "plan-bascule.json"  # dans le passage, avant la bascule (R7)  # sous l'archive : les fichiers d'arrivée remplacés par leur fusion
COMMIT = re.compile(r"[0-9a-f]{40}")

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


class Interrompu(BaseException):
    """SIGTERM ou SIGHUP pendant le déménagement : levé comme une erreur, pour que la remise se fasse (R7)."""


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
    for de, vers, motif, arbre, _ in dossiers:
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
    return tuple((vers, de, motif, arbre, fusion) for de, vers, motif, arbre, fusion in DOSSIERS)


def _fusionne(dossiers, vers):
    return any(fusion and vers.startswith(racine + "/") for _, racine, _, _, fusion in dossiers)


def _lignes_journal(octets, nom):
    """[(clé, heure, ligne)] d'un mois du journal; la clé est (agent, id, type) : une intention et son résultat partagent leur
    id. Refus sur une ligne illisible ou coupée : la fusion la mettrait au milieu, où le plafond du jour la refuserait."""
    try:
        texte = octets.decode("utf-8")
    except UnicodeDecodeError:
        raise Refus(f"{nom} n'est pas lisible : à regarder avant de fusionner le journal.") from None
    if texte and not texte.endswith("\n"):
        raise Refus(f"Dernière ligne coupée dans {nom} : à regarder avant de fusionner le journal.")
    lignes = []
    for brut in texte.splitlines():
        try:
            ligne = json.loads(brut)
        except ValueError:
            ligne = None
        if not (isinstance(ligne, dict) and ligne.get("type") in ("intention", "resultat") and isinstance(ligne.get("id"), str)):
            raise Refus(f"Ligne illisible dans {nom} : à regarder avant de fusionner le journal.")
        lignes.append(((ligne.get("agent") or "smith", ligne["id"], ligne["type"]), str(ligne.get("heure") or ""), brut))
    return lignes


def fusionner(source, cible, nom):
    """Le mois fusionné (R2), en octets : chaque ligne des deux fichiers une seule fois, rangées par heure, l'ordre gardé à
    heure égale. Une même clé avec deux contenus différents est refusée : rien n'est choisi à la place d'Ugo."""
    gardees = {}
    for cle, heure, brut in _lignes_journal(source, nom) + _lignes_journal(cible, nom):
        if cle in gardees and gardees[cle][1] != brut:
            raise Refus(f"{nom} : deux lignes différentes pour {cle[0]} {cle[1]} ({cle[2]}). À regarder avant de fusionner.")
        gardees.setdefault(cle, (heure, brut))
    return "".join(brut + "\n" for _, brut in sorted(gardees.values(), key=lambda g: g[0])).encode("utf-8")


def _fusion(donnees, f):
    return fusionner((donnees / f["de"]).read_bytes(), (donnees / f["vers"]).read_bytes(), f["vers"])


def _lien_sur(donnees, relatif):
    """Le premier niveau de donnees/relatif qui est un lien ou qui sortirait de donnees/ (R4), relatif à donnees/; sinon None."""
    donnees = Path(donnees)
    if donnees.is_symlink():
        return "."
    courant = donnees
    for partie in Path(relatif).parts:
        if partie in ("..", "/"):
            return relatif
        courant = courant / partie
        if courant.is_symlink():
            return courant.relative_to(donnees).as_posix()
    return None


def _plan(donnees, sens):
    """[{'de', 'vers', 'taille', 'sha256', 'cible', 'arrivee'}] : cible, l'empreinte du fichier déjà à l'arrivée, à fusionner
    (None sinon); arrivee, celle du fichier qui sera posé à l'arrivée. Un lien y reste, sans empreinte, pour être refusé."""
    donnees = Path(donnees)
    plan = []
    for de, vers in _fichiers(donnees, sens.dossiers):
        chemin, arrivee = donnees / de, donnees / vers
        empreinte = None if chemin.is_symlink() else sha(chemin)
        f = {"de": de, "vers": vers, "taille": chemin.lstat().st_size, "sha256": empreinte, "cible": None, "arrivee": empreinte}
        if empreinte and _fusionne(sens.dossiers, vers) and _lien_sur(donnees, vers) is None and arrivee.is_file():
            f["cible"] = sha(arrivee)
            f["arrivee"] = hashlib.sha256(_fusion(donnees, f)).hexdigest()
        plan.append(f)
    return plan


def inventaire(donnees):
    """Ce qui part de donnees/smith/ : [{'de', 'vers', 'taille', 'sha256'}], sans contenu."""
    return [{cle: f[cle] for cle in ("de", "vers", "taille", "sha256")} for f in _plan(donnees, ALLER)]


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
    niveaux = [sens.passage, sens.archive, sens.sauvegarde, Path(sens.note).parent.as_posix(), sens.base_de, sens.base_vers,
               *(d for d, *_ in sens.dossiers), *(v for _, v, *_ in sens.dossiers),
               *(f[cle] for f in fichiers for cle in ("de", "vers"))]
    for relatif in niveaux:
        lien = _lien_sur(donnees, relatif)
        if lien:
            raise Refus(f"Lien ou sortie de donnees/ sur le chemin : {lien}.")
    if not (donnees / sens.base_de).is_file():
        raise Refus(f"{sens.base_de} absent : rien à faire dans ce sens.")
    if (donnees / sens.base_vers).exists() or (donnees / sens.base_vers).is_symlink():
        raise Refus(f"{sens.base_vers} déjà présent : refusé.")
    for f in fichiers:
        chemin = donnees / f["de"]
        if chemin.is_symlink() or not chemin.is_file():
            raise Refus(f"Lien ou fichier spécial dans l'inventaire : {f['de']}.")
        if chemin.lstat().st_uid != sondes.uid:
            raise Refus(f"Fichier dont le propriétaire n'est pas Ugo : {f['de']}.")
        cible = donnees / f["vers"]
        if f["cible"] is None and (cible.exists() or cible.is_symlink()):
            raise Refus(f"{f['vers']} déjà présent : refusé.")
        if f["cible"] is not None and (cible.is_symlink() or not cible.is_file() or cible.lstat().st_uid != sondes.uid):
            raise Refus(f"{f['vers']} à fusionner n'est pas un fichier ordinaire d'Ugo : refusé.")
    if (donnees / sens.passage / PLAN_BASCULE).exists():
        raise Refus(f"Bascule interrompue : son plan est dans {sens.passage}/{PLAN_BASCULE} (chaque fichier avec sa place d'avant "
                    "et d'après, dans l'ordre). Remets chaque fichier à sa place d'avant à la main, puis efface le passage.")
    if (donnees / sens.passage).exists():
        raise Refus(f"{sens.passage} reste d'un essai interrompu : à regarder avant de recommencer.")
    attente = pending(donnees / sens.base_de, MIGRATIONS)
    if attente:
        raise Refus("Migration Finances en attente : " + ", ".join(f"{v:04d}" for v, _, _ in attente)
                    + ". Lance bin/moneta-migrer --appliquer d'abord.")


def recapitulatif(sens, fichiers, commit_retour=None):
    taille = sum(f["taille"] for f in fichiers)
    lignes = [f"{'Déménagement' if sens is ALLER else 'Retour'} de {len(fichiers)} fichier(s), {taille} octets :"]
    lignes += [f"  {f['de']} → {f['vers']}" + (" (fusionné avec le fichier déjà là)" if f["cible"] else "") for f in fichiers]
    lignes += [f"Sauvegarde vérifiée de la base dans {sens.sauvegarde}/, originaux gardés dans {sens.archive}/."]
    if commit_retour:
        lignes.append(f"Commit de retour de Smith noté : {commit_retour}.")
    lignes.append(f"Tape « {sens.mot} » pour continuer; tout autre mot ne fait rien.")
    if sens is RETOUR:
        lignes.append("Le code de Smith revient au commit de retour noté dans moneta/private/demenagement.json "
                      "(commit_retour_smith) par git switch, fait par Ugo.")
    return "\n".join(lignes)


def _dossiers_git(depot):
    """(dossier du dépôt ou du worktree, dossier commun), lus dans .git sans lancer Git."""
    meta = Path(depot) / ".git"
    if meta.is_file():
        meta = (Path(depot) / meta.read_text(encoding="utf-8").split(":", 1)[1].strip()).resolve()
    commun = meta
    if (meta / "commondir").is_file():
        commun = (meta / (meta / "commondir").read_text(encoding="utf-8").strip()).resolve()
    return meta, commun


def _commit(depot):
    """Le commit de HEAD, lu dans les fichiers du dépôt (worktree compris), sans lancer Git; None s'il est illisible."""
    try:
        meta, commun = _dossiers_git(depot)
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


def _objet_connu(depot, commit):
    """Vrai si l'objet est dans le dépôt : objet isolé, ou nom d'un index de paquet (version 2), cherché par dichotomie."""
    try:
        objets = _dossiers_git(depot)[1] / "objects"
        if (objets / commit[:2] / commit[2:]).is_file():
            return True
        cle = bytes.fromhex(commit)
        for index in (objets / "pack").glob("*.idx"):
            octets = index.read_bytes()
            if octets[:8] != b"\xfftOc\x00\x00\x00\x02":
                continue
            bas = int.from_bytes(octets[4 + 4 * cle[0]:8 + 4 * cle[0]], "big") if cle[0] else 0
            haut = int.from_bytes(octets[8 + 4 * cle[0]:12 + 4 * cle[0]], "big")
            while bas < haut:
                milieu = (bas + haut) // 2
                nom = octets[1032 + 20 * milieu:1052 + 20 * milieu]
                if nom == cle:
                    return True
                bas, haut = (milieu + 1, haut) if nom < cle else (bas, milieu)
    except (OSError, IndexError, ValueError):
        pass
    return False


def commit_retour_refus(depot, commit):
    """None si commit peut être le commit de retour de Smith (R3, CR-005), sinon la raison : écrit en entier, connu du dépôt,
    autre que son HEAD (le Smith déjà sans Finances). Qu'il soit un ancêtre de main qui a encore Finances, Ugo le vérifie
    avec Git avant (quickstart §7) : ce programme ne lance jamais Git."""
    if not COMMIT.fullmatch(commit or ""):
        return "le commit de retour s'écrit en entier : 40 caractères hexadécimaux, en minuscules."
    if commit == _commit(depot):
        return "c'est le commit actuel de Smith, déjà sans Finances; donne celui noté à l'étape 3 (CR-005)."
    if not _objet_connu(depot, commit):
        return f"{commit} est inconnu du dépôt de Smith."
    return None


def _horodatage():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")


def _rien(etape):
    return None


def _creer(racine, dossier):
    """Chaque dossier manquant de racine jusqu'à dossier, créé en 700; ceux qui existent restent tels quels; un lien sur un
    niveau est refusé (R4)."""
    for niveau in [*reversed(Path(dossier).relative_to(racine).parents[:-1]), Path(dossier).relative_to(racine)]:
        if (racine / niveau).is_symlink():
            raise Refus(f"Lien ou sortie de donnees/ sur le chemin : {niveau.as_posix()}.")
        if not (racine / niveau).is_dir():
            private_folder(racine / niveau)


def _poser(donnees, de, vers):
    """de renommé en vers sans jamais écraser (R1) : un lien dur, refusé si vers existe déjà, puis de effacé."""
    try:
        os.link(de, vers, follow_symlinks=False)
    except FileExistsError:
        raise Refus(f"{Path(vers).relative_to(donnees).as_posix()} est apparu pendant le déménagement : rien n'est écrasé, "
                    "ce qui était fait est remis.") from None
    try:
        os.unlink(de)
    except BaseException:
        os.unlink(vers)
        raise


def _interrompre(numero, _):
    raise Interrompu(f"signal {numero}")


def _deplacer(sens, donnees, sondes, go, crochet, commit_retour=None):
    """Sous SIGTERM et SIGHUP levés en Interrompu (R7), le tout ou rien de _deplacer_sans_signal; SIGINT lève déjà."""
    anciens = {}
    try:
        for numero in (signal.SIGTERM, signal.SIGHUP):
            anciens[numero] = signal.signal(numero, _interrompre)
    except ValueError:  # hors du fil principal : rien à poser
        pass
    try:
        return _deplacer_sans_signal(sens, donnees, sondes, go, crochet, commit_retour)
    finally:
        for numero, ancien in anciens.items():
            signal.signal(numero, ancien)


def _deplacer_sans_signal(sens, donnees, sondes, go, crochet, commit_retour):
    donnees = Path(donnees)
    crochet = crochet or _rien
    fichiers = _plan(donnees, sens)
    _refus_avant(donnees, sens, sondes, fichiers)
    if go(recapitulatif(sens, fichiers, commit_retour)) != sens.mot:
        raise Refus(f"Rien n'est fait : il faut taper exactement « {sens.mot} ».")
    # R1 : pendant l'attente du mot, un processus a pu partir, une cible apparaître, une ligne s'ajouter au journal.
    apres = _plan(donnees, sens)
    _refus_avant(donnees, sens, sondes, apres)
    if apres != fichiers:
        raise Refus("Les fichiers ont changé pendant l'attente du mot : rien n'est fait. Relance pour voir le nouveau "
                    "récapitulatif.")
    base = donnees / sens.base_de
    signature = source_signature(base)
    stamp = _horodatage()

    # 1. Sauvegarde vérifiée de la base.
    crochet("sauvegarde")
    _creer(donnees, donnees / sens.sauvegarde)
    copie = backup_database(base, donnees / sens.sauvegarde)
    copie.chmod(0o600)
    copie.rename(copie.with_name(f"finances-{stamp}.db"))

    # 2 et 3. Copie (ou fusion) dans le passage, puis empreintes comparées; la base ne doit pas avoir bougé.
    passage = donnees / sens.passage
    try:
        crochet("copie")
        for f in fichiers:
            cible = passage / f["vers"]
            _creer(donnees, cible.parent)
            if f["cible"] is None:
                shutil.copyfile(donnees / f["de"], cible, follow_symlinks=False)
            else:
                cible.write_bytes(_fusion(donnees, f))
            cible.chmod(0o600)
        crochet("empreintes")
        ecarts = [f["de"] for f in fichiers
                  if sha(passage / f["vers"]) != f["arrivee"] or sha(donnees / f["de"]) != f["sha256"]
                  or (f["cible"] is not None and sha(donnees / f["vers"]) != f["cible"])]
        if source_signature(base) != signature:
            raise Refus(f"{sens.base_de} a changé pendant la copie : rien n'est déplacé. Ferme ce qui écrit et recommence.")
        if ecarts:
            raise Refus("Empreinte différente après la copie : " + ", ".join(ecarts) + ". Rien n'est déplacé.")
        # R7 : le plan de la bascule, écrit avant elle; après un arrêt brutal, il dit où remettre chaque fichier.
        archive = donnees / sens.archive / stamp
        deplacements = []
        for f in fichiers:
            if f["cible"] is not None:
                deplacements.append((donnees / f["vers"], archive / REMPLACES / f["vers"], f))
            deplacements += [(passage / f["vers"], donnees / f["vers"], None), (donnees / f["de"], archive / f["de"], None)]
        plan_bascule = {"sens": sens.nom, "horodatage": stamp, "deplacements": [
            {"avant": de.relative_to(donnees).as_posix(), "apres": vers.relative_to(donnees).as_posix()}
            for de, vers, _ in deplacements]}
        write_private(passage, PLAN_BASCULE, (json.dumps(plan_bascule, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except BaseException:
        shutil.rmtree(passage, ignore_errors=True)
        raise

    # 4 et 5. Bascule par renommage, sans jamais écraser, puis la note; un échec remet ce qui a été renommé.
    faits = []

    def renommer(de, vers):
        crochet("bascule")
        _creer(donnees, vers.parent)
        _poser(donnees, de, vers)
        faits.append((de, vers))

    try:
        if source_signature(base) != signature:
            raise Refus(f"{sens.base_de} a changé avant la bascule : rien n'est déplacé.")
        for de, vers, remplace in deplacements:
            renommer(de, vers)
            if remplace is not None and sha(vers) != remplace["cible"]:
                raise Refus(f"{remplace['vers']} a changé avant la bascule : rien n'est déplacé.")
        for dossier in {vers.parent for _, vers in faits}:
            dossier.chmod(0o700)
        crochet("note")
        note = {"date": datetime.now().isoformat(timespec="seconds"), "sens": sens.nom,
                "commit_smith": _commit(SMITH), "commit_moneta": _commit(CODE),
                "originaux": sens.archive + "/" + stamp,
                "empreintes": {f["vers"]: f["arrivee"] for f in fichiers},
                "fusionnes": sorted(f["vers"] for f in fichiers if f["cible"] is not None)}
        if commit_retour:
            note["commit_retour_smith"] = commit_retour
        chemin_note = donnees / sens.note
        _creer(donnees, chemin_note.parent)
        write_private(chemin_note.parent, chemin_note.name,
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
            _poser(donnees, vers, de)
        except BaseException:
            chemins = [v.relative_to(donnees).as_posix() for _, v in reversed(faits)]
            details = [f"{v.relative_to(donnees).as_posix()} (sa place : {d.relative_to(donnees).as_posix()})"
                       for d, v in reversed(faits)]
            raise Bloque(chemins, details) from None
        faits.pop()


def appliquer(donnees, sondes, go, crochet=None, commit_retour=None):
    """L'aller : de donnees/smith/ à donnees/moneta/ et au journal commun. commit_retour : le commit de retour de Smith
    (CR-005), vérifié par main avant, noté à part (R3). Rend la note."""
    return _deplacer(ALLER, donnees, sondes, go, crochet, commit_retour)


def retour(donnees, sondes, go, crochet=None):
    """Le retour : les fichiers actuels de Moneta, écritures faites depuis comprises, reviennent dans donnees/smith/."""
    return _deplacer(RETOUR, donnees, sondes, go, crochet)


# --- La répétition sur un dossier fictif ---------------------------------------------------------------------------------

FICTIVES = Sondes(processus=lambda: [], lot_finances=lambda: [], uid=os.getuid())


def _etat(donnees, plan):
    """'avant', 'fait' ou None, d'après le plan : chaque fichier à une seule place, avec son empreinte; avant, un fichier à
    fusionner est encore à l'arrivée, tel quel."""
    donnees = Path(donnees)

    def tel(relatif, empreinte):
        return (donnees / relatif).is_file() and sha(donnees / relatif) == empreinte
    if all(tel(f["de"], f["sha256"]) and (tel(f["vers"], f["cible"]) if f["cible"] else not (donnees / f["vers"]).exists())
           for f in plan):
        return "avant"
    if all(tel(f["vers"], f["arrivee"]) and not (donnees / f["de"]).exists() for f in plan):
        return "fait"
    return None


class _Injecte(Exception):
    pass


def _repeter_refus(donnees, dire):
    """Chaque refus du contrat, sur des copies jetables du dossier fictif : rien ne doit changer."""
    with tempfile.TemporaryDirectory(prefix="moneta-repeter-") as jetable:
        def essai(nom, preparer=None, sondes=FICTIVES, mot=MOT_ALLER, crochet=None, sens=appliquer, go=None):
            copie = Path(jetable) / "donnees"
            shutil.rmtree(copie, ignore_errors=True)
            shutil.copytree(donnees, copie, symlinks=True)
            if preparer:
                preparer(copie)
            avant = _arbre(copie)
            try:
                sens(copie, sondes, go(copie) if go else lambda recap: mot, crochet=crochet(copie) if crochet else None)
            except Refus:
                if go:
                    avant = {**avant, **{k: v for k, v in _arbre(copie).items() if k not in avant}}
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

        def lien_d_arrivee(copie):
            ailleurs = Path(jetable) / "ailleurs"
            ailleurs.mkdir(exist_ok=True)
            (copie / "moneta").symlink_to(ailleurs, target_is_directory=True)
        essai("lien sur un dossier d'arrivée", preparer=lien_d_arrivee)

        def journal_illisible(copie):
            _journal_commence(copie)
            for mois in (copie / "journal-ecritures").glob("*.jsonl"):
                with mois.open("a", encoding="utf-8") as journal:
                    journal.write("pas du json\n")
        if any((donnees / "smith" / "journal-ecritures").glob("*.jsonl")):
            essai("ligne illisible au journal commun", preparer=journal_illisible)

        def cible_pendant_le_mot(copie):
            # R1 : un fichier apparaît à l'arrivée pendant que le récapitulatif attend le mot.
            def go(recap):
                ecrire = copie / next(f["vers"] for f in _plan(copie, ALLER) if f["cible"] is None)
                _creer(copie, ecrire.parent)
                ecrire.write_bytes(b"apparu pendant le mot")
                return MOT_ALLER
            return go
        essai("cible apparue pendant le mot", go=cible_pendant_le_mot)
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


def _repeter_echecs(donnees, dire, sens, preparer, suffixe=""):
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
            attendu = _plan(c, ALLER if sens is appliquer else RETOUR)
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
        dire(f"{sens_nom(sens)}{suffixe} : échec injecté à chacune des {len(vues)} étapes, chaque fois avant ou fait")


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
    plan = _plan(donnees, ALLER if sens is appliquer else RETOUR)
    sens(donnees, FICTIVES, lambda recap: sens_mot(sens))
    if _etat(donnees, plan) != "fait":
        raise RuntimeError(f"{sens_nom(sens)} : empreintes différentes à l'arrivée.")
    dire(f"{sens_nom(sens)} : {len(plan)} fichier(s), empreintes identiques")


LIGNE_COMMENCEE = {"id": "c0ffee11", "heure": "2026-11-02T09:00:00", "type": "intention", "agent": "smith",
                   "outil": "WR_SAVE_DOJO_BD", "lot": "dojo", "mode": "go", "raisons": [], "etapes": [], "montant": "0.00",
                   "origine": "desktop"}


def _journal_commence(copie):
    """Le journal commun déjà commencé par Smith 1.1.0 (R2) : pour chaque mois de Smith, une ligne nouvelle et sa première
    ligne en double."""
    for mois in sorted((Path(copie) / "smith" / "journal-ecritures").glob("*.jsonl")):
        premiere = mois.read_text(encoding="utf-8").splitlines()[:1]
        commun = Path(copie) / "journal-ecritures" / mois.name
        _creer(Path(copie), commun.parent)
        commun.write_text("".join(l + "\n" for l in premiere + [json.dumps(LIGNE_COMMENCEE, ensure_ascii=False)]),
                          encoding="utf-8")
        commun.chmod(0o600)


def _repeter_fusion(donnees, dire):
    """R2 : aller puis retour avec un journal commun commencé; chaque ligne arrive une seule fois, rien ne se perd."""
    mois = sorted((Path(donnees) / "smith" / "journal-ecritures").glob("*.jsonl"))
    if not mois:
        return
    with tempfile.TemporaryDirectory(prefix="moneta-repeter-") as jetable:
        c = Path(jetable) / "donnees"
        shutil.copytree(donnees, c, symlinks=True)
        _journal_commence(c)
        attendu = {m.name: set(m.read_text(encoding="utf-8").splitlines())
                   | {json.dumps(LIGNE_COMMENCEE, ensure_ascii=False)} for m in mois}
        for sens, dossier in ((appliquer, c / "journal-ecritures"), (retour, c / "smith" / "journal-ecritures")):
            sens(c, FICTIVES, lambda recap: sens_mot(sens))
            for nom, lignes in attendu.items():
                trouvees = (dossier / nom).read_text(encoding="utf-8").splitlines()
                if set(trouvees) != lignes or len(trouvees) != len(lignes):
                    raise RuntimeError(f"{sens_nom(sens)} : journal commun mal fusionné ({nom}).")
    dire(f"journal commun commencé : {len(mois)} mois fusionné(s) à l'aller et au retour, chaque ligne une fois")


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
    if any((donnees / "smith" / "journal-ecritures").glob("*.jsonl")):
        _repeter_echecs(donnees, dire, appliquer, _journal_commence, " (journal commun commencé)")
    _repeter_fusion(donnees, dire)
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


def _commit_retour_note(donnees):
    try:
        note = json.loads((Path(donnees) / NOTE_ALLER).read_text(encoding="utf-8"))
        return note["commit_retour_smith"]
    except (OSError, ValueError, KeyError, TypeError):
        return "noté dans moneta/private/demenagement.json (commit_retour_smith)"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="moneta-demenager", description=__doc__.split("\n\n")[0])
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inventaire", nargs="?", const=str(DONNEES), metavar="DOSSIER")
    modes.add_argument("--repeter", metavar="DOSSIER")
    modes.add_argument("--appliquer", action="store_true")
    modes.add_argument("--retour", action="store_true")
    parser.add_argument("--commit-retour", metavar="COMMIT",
                        help="avec --appliquer, obligatoire : le commit de Smith noté à l'étape 3 (CR-005), écrit en entier")
    args = parser.parse_args(argv)
    if args.appliquer != bool(args.commit_retour):
        parser.error("--commit-retour va avec --appliquer, et --appliquer le demande.")
    try:
        if args.inventaire:
            for f in inventaire(Path(args.inventaire).expanduser()):
                print(f"{f['de']} → {f['vers']}  {f['taille']} octets  {f['sha256'] or 'lien'}")
        elif args.repeter:
            repeter(args.repeter)
            print("OK : répétition complète, chaque refus et chaque échec vérifiés, empreintes identiques à chaque passage.")
        else:
            if args.appliquer:
                raison = commit_retour_refus(SMITH, args.commit_retour)
                if raison:
                    raise Refus(f"--commit-retour : {raison}")

            def go(recap):
                print(recap)
                try:
                    return input("> ")
                except EOFError:
                    return ""
            if args.appliquer:
                note = appliquer(DONNEES, Sondes(), go, commit_retour=args.commit_retour)
            else:
                note = retour(DONNEES, Sondes(), go)
            print(f"OK : {len(note['empreintes'])} fichier(s), empreintes identiques; note dans "
                  f"{NOTE_ALLER if args.appliquer else NOTE_RETOUR}.")
            if args.retour:
                print("Reste à Ugo : revenir au commit de retour de Smith, " + _commit_retour_note(DONNEES)
                      + " (git switch).")
    except Refus as refus:
        print(f"REFUS : {refus}", file=sys.stderr)
        return 1
    except Bloque as bloque:
        print(f"ARRÊT : {bloque}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
