"""La nuit de Moneta (21 h 15, quinze minutes après Smith; spec 013 de Smith, R10, EF-015) : copie locale vérifiée de finances.db,
puis image chiffrée hors du Mac (Drive, puis le disque PRO-HD1), puis rétention. Repris de la nuit de Smith, sur le moteur
socle.offsite_backup, avec les noms de Moneta : sa phrase de passe au trousseau (ca.dojod.moneta.sauvegarde, jamais celle de Smith),
ses images (moneta-…), son journal private/sauvegarde-quotidienne.log.

Images (D066) : chaque soir une quotidienne (la base, son export CSV, et seulement les pièces — factures de Drive, relevés
d'imports — pas encore dans une image, d'après le registre private/sauvegarde-pieces.txt); la première de chaque mois est mensuelle
et complète. Une fois une mensuelle vérifiée, les quotidiennes d'avant elle sont retirées; les mensuelles restent 12 mois, celles de
janvier 7 ans. dojo.db et le journal commun des écritures restent dans l'image de Smith.

Copies en clair (quotidiennes, copies d'avant chaque écriture) de plus de 30 jours : retirées seulement quand une image vérifiée a
au plus 7 jours et tient leur jour (D051, D057); sinon gardées, et le journal le dit.

Disque externe (D079) : chaque image vérifiée dans Drive est copiée sur MONETA_SAUVEGARDE_DISQUE et vérifiée là, avec rattrapage;
disque absent : ATTENTION, rien d'autre.
"""
from contextlib import closing
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moneta.chemins import backups, drive, home  # noqa: E402
from moneta.outils import factures  # noqa: E402
from moneta.outils.csv_export import export_csv  # noqa: E402
from socle import offsite_backup as moteur  # noqa: E402
from socle.chemins import private_folder, write_private  # noqa: E402
from socle.database_backup import backup_database  # noqa: E402
from socle.offsite_backup import copy_to_disk, digest, ecrire_manifeste, pieces  # noqa: E402, F401

KEYCHAIN_SERVICE = "ca.dojod.moneta.sauvegarde"
KINDS = {"quotidienne": ("quotidiennes", "moneta-"), "mensuelle": ("mensuelles", "moneta-mensuelle-")}
PROPRIETAIRE = "de Moneta"
VOLUME = "Moneta"
BASE = "finances.db"

KEEP_DAYS = 30
FRESH_DAYS = 7
PLAIN_COPIES = ("moneta",)
DAILY = re.compile(r"^finances_(\d{4}-\d{2}-\d{2})\.db$")
IMAGE = re.compile(r"^moneta-(mensuelle-)?(\d{4}-\d{2}-\d{2})_\d{6}\.dmg$")
LOGGED = re.compile(r" OK hors Mac : (\S+\.dmg) ")
ON_DISK = re.compile(r" OK disque : (\S+\.dmg) ")
COVER_DAYS = 1
MONTHLY_KEEP_DAYS = 365
JANUARY_KEEP_YEARS = 7
REGISTRY = "sauvegarde-pieces.txt"


def keychain_passphrase(service=KEYCHAIN_SERVICE):
    return moteur.keychain_passphrase(service)


def local_copies(today=None, folder=None):
    """Une copie vérifiée de finances.db par jour; rend les copies faites."""
    today = today or date.today()
    folder = folder or backups() / "database" / "daily"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / f"finances_{today.isoformat()}.db"
    copy = backup_database(home() / "database" / BASE, folder)
    shutil.move(copy, target)
    target.chmod(0o600)
    remove_leftovers(folder)
    return [target]


def remove_leftovers(folder):
    """Les -wal et -shm dont la copie n'est plus là."""
    for path in folder.iterdir():
        base = path.name[:-4]
        if path.name.endswith(("-wal", "-shm")) and base.endswith(".db") and not (folder / base).exists():
            path.unlink()


def prune_daily(folder, today, covered):
    """Les copies quotidiennes de plus de KEEP_DAYS (d'après le jour de leur nom), d'un jour couvert; rend combien."""
    if not folder.is_dir():
        return 0
    old = [p for p in folder.iterdir()
           if DAILY.match(p.name) and date.fromisoformat(DAILY.match(p.name)[1]) < today - timedelta(days=KEEP_DAYS)
           and covered(date.fromisoformat(DAILY.match(p.name)[1]))]
    for path in old:
        path.unlink()
    remove_leftovers(folder)
    return len(old)


def prune_copies(folder, today, covered):
    """Les copies faites il y a plus de KEEP_DAYS, d'un jour couvert; rend combien."""
    if not folder.is_dir():
        return 0
    limit = datetime.combine(today - timedelta(days=KEEP_DAYS), datetime.min.time()).timestamp()
    old = [p for p in folder.iterdir()
           if p.suffix == ".db" and p.is_file() and not p.is_symlink() and p.stat().st_mtime < limit
           and covered(date.fromtimestamp(p.stat().st_mtime))]
    for path in old:
        path.unlink()
    remove_leftovers(folder)
    return len(old)


def log_path():
    return private_folder(home() / "private") / "sauvegarde-quotidienne.log"


def log(line):
    fd = os.open(log_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as file:
        file.write(f"{datetime.now():%Y-%m-%d %H:%M} {line}\n")


def logged_images(pattern=LOGGED):
    """Les noms des images que le journal dit vérifiées (« OK hors Mac » pour Drive, « OK disque » pour le disque)."""
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [Path(logged[1]).name for logged in filter(None, (pattern.search(line) for line in lines))
            if IMAGE.match(Path(logged[1]).name)]


def images_at(destination, pattern=LOGGED):
    """{nom: (jour, mensuelle, chemin)} des images vérifiées à leur création et encore à la destination, en fichiers ordinaires
    non vides : le journal seul ne suffit pas, une image retirée de Drive ne couvre rien."""
    if not destination:
        return {}
    found = {}
    for name in logged_images(pattern):
        match = IMAGE.match(name)
        place = Path(destination) / KINDS["mensuelle" if match[1] else "quotidienne"][0] / name
        try:
            info = place.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_size > 0:
            found[name] = (date.fromisoformat(match[2]), bool(match[1]), place)
    return found


def verified(destination, pattern=LOGGED):
    """[(jour, mensuelle)] des images vérifiées encore à la destination."""
    return sorted({(day, monthly) for day, monthly, _ in images_at(destination, pattern).values()})


def monthly_due(destination, today=None):
    """Vrai tant que le mois n'a aucune mensuelle vérifiée à la destination."""
    today = today or date.today()
    return not any(monthly and (day.year, day.month) == (today.year, today.month) for day, monthly in verified(destination))


def piece_sources():
    """Les pièces : les factures de Drive (DojoD/05 Administration/Factures) et les fichiers déposés dans imports/."""
    return {"factures": factures.dossier_factures(), "imports": home() / "imports"}


def registry():
    """« empreinte  pièce » de chaque pièce déjà dans une image vérifiée; vide sans registre (la prochaine image les reprend
    toutes une fois, sans mal)."""
    try:
        return set((private_folder(home() / "private") / REGISTRY).read_text(encoding="utf-8").splitlines())
    except OSError:
        return set()


def entry(relative, value):
    return f"{value}  {relative}"


def save_registry(chosen, replace):
    known = set() if replace else registry()
    known |= {entry(relative, value) for relative, (_, value) in chosen.items()}
    write_private(private_folder(home() / "private"), REGISTRY, "".join(f"{line}\n" for line in sorted(known)).encode("utf-8"))


def kept(day, monthly, latest_monthly, today):
    """D066 : sans mensuelle vérifiée, tout reste. Après une, les quotidiennes d'avant elle partent; les mensuelles restent
    12 mois, celles de janvier 7 ans, et la dernière toujours."""
    if latest_monthly is None or day >= latest_monthly:
        return True
    return monthly and (day >= today - timedelta(days=MONTHLY_KEEP_DAYS)
                        or (day.month == 1 and day >= today.replace(year=today.year - JANUARY_KEEP_YEARS, day=1)))


def prune_images(destination, latest_monthly, today):
    """Retire les images de Moneta que kept() laisse partir, fichiers ordinaires seulement (jamais celles de Smith, d'un
    autre nom). Rend (quotidiennes, mensuelles) retirées."""
    destination = Path(destination)
    daily = monthly = 0
    for folder in (destination / "quotidiennes", destination / "mensuelles"):
        if folder.is_symlink() or not folder.is_dir():
            continue
        for path in folder.iterdir():
            name = IMAGE.match(path.name)
            if not name or not stat.S_ISREG(path.lstat().st_mode) or bool(name[1]) != (folder.name == "mensuelles"):
                continue
            if not kept(date.fromisoformat(name[2]), bool(name[1]), latest_monthly, today):
                path.unlink()
                if name[1]:
                    monthly += 1
                else:
                    daily += 1
    return daily, monthly


def retention(today=None, destination=None):
    """Les copies en clair de plus de KEEP_DAYS partent seulement quand une image vérifiée d'au plus FRESH_DAYS est à la
    destination, et seulement celles d'un jour qu'une image vérifiée tient (le jour même, la veille ou le lendemain)."""
    today = today or date.today()
    destination = destination if destination is not None else os.environ.get("MONETA_SAUVEGARDE_DESTINATION")
    found = verified(destination)
    images = {day for day, _ in found}
    latest_monthly = max((day for day, monthly in found if monthly), default=None)
    last = max(images, default=None)
    if last is None or last < today - timedelta(days=FRESH_DAYS):
        log(f"ATTENTION rétention : aucune sauvegarde chiffrée vérifiée depuis {FRESH_DAYS} jours (dernière : {last or 'aucune'}); "
            "copies en clair gardées")
        return
    # Un jour est couvert par sa propre image, ou par une mensuelle faite après lui (les quotidiennes d'avant partent, D066).
    covered = lambda day: (any(abs((day - image).days) <= COVER_DAYS for image in images)
                           or (latest_monthly is not None and day < latest_monthly))
    copies = prune_daily(backups() / "database" / "daily", today, covered)
    copies += sum(prune_copies(backups() / "database" / name, today, covered) for name in PLAIN_COPIES)
    if copies:
        log(f"OK rétention : {copies} copie(s) en clair de plus de {KEEP_DAYS} jours retirée(s)")
    if latest_monthly is not None:
        daily, monthly = prune_images(destination, latest_monthly, today)
        if daily or monthly:
            log(f"OK rétention des images : {daily} quotidienne(s) d'avant la mensuelle du {latest_monthly} et "
                f"{monthly} mensuelle(s) de plus de 12 mois retirées")


def stage(folder, data_home, chosen=None, note=""):
    """Le contenu de l'image : une copie cohérente de finances.db, son export CSV, les pièces choisies ({relatif: (fichier,
    empreinte)}, sous pieces/) et le manifeste des empreintes. Rien de Smith : ni dojo.db ni le journal des écritures."""
    folder = Path(folder)
    base = Path(data_home) / "database" / BASE
    if not base.is_file():
        raise ValueError("Aucune base à sauvegarder.")
    data = folder / "donnees"
    data.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="moneta-hors-mac-") as work:
        shutil.move(backup_database(base, work), data / BASE)
    (data / BASE).chmod(0o600)
    export_csv(data / BASE, folder / "csv" / "finances")
    for relative, (source, value) in sorted((chosen or {}).items()):
        target = folder / "pieces" / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, target)
        target.chmod(0o600)
        if digest(target) != value:
            raise RuntimeError(f"Pièce modifiée pendant la copie : {relative}")
    with closing(sqlite3.connect(f"file:{data / BASE}?mode=ro", uri=True)) as con:
        has_versions = con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        versions = [r[0] for r in con.execute("SELECT version FROM schema_migrations ORDER BY version")] if has_versions else []
    return ecrire_manifeste(folder, f"Sauvegarde Moneta du {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                                    f"Migrations de finances.db : {', '.join(f'{v:04d}' for v in versions) or 'aucune enregistrée'}\n"
                                    f"Pièces (factures, relevés) : {len(chosen or {})}{note}")


def create_backup(destination, passphrase, *, data_home=None, hdiutil="/usr/bin/hdiutil", now=None, kind="quotidienne",
                  chosen=None, note=""):
    """Construit, chiffre, copie et vérifie l'image dans le dossier de sa sorte; rend (image, fichiers vérifiés)."""
    data_home = Path(data_home) if data_home else home()
    return moteur.create_backup(destination, passphrase, lambda folder: stage(folder, data_home, chosen, note),
                                data_home=data_home, volume=VOLUME, sorte=KINDS[kind], hdiutil=hdiutil, now=now,
                                proprietaire=PROPRIETAIRE)


def offsite(destination):
    """L'image du soir : mensuelle et complète quand le mois n'en a pas encore, sinon quotidienne avec les pièces nouvelles.
    Jamais de mensuelle sans ses pièces (une pièce manquante partirait avec les quotidiennes) : elle attend un soir."""
    kind = "mensuelle" if monthly_due(destination) else "quotidienne"
    try:
        everything = pieces(piece_sources())
    except OSError as exc:
        log(f"ATTENTION pièces illisibles ({exc}) : image sans pièces"
            + (", quotidienne; la mensuelle attend" if kind == "mensuelle" else ""))
        everything, kind = None, "quotidienne"
    if everything is None:
        chosen, note = {}, " (pièces illisibles ce soir)"
    elif kind == "mensuelle":
        chosen, note = everything, " (toutes)"
    else:
        known = registry()
        chosen = {relative: value for relative, value in everything.items() if entry(relative, value[1]) not in known}
        note = " (nouvelles depuis la dernière image)"
    image, files = create_backup(destination, keychain_passphrase(), kind=kind, chosen=chosen, note=note)
    log(f"OK hors Mac : {image.parent.name}/{image.name} ({files} fichiers vérifiés, {len(chosen)} pièce(s))")
    if everything is not None:
        save_registry(chosen, replace=kind == "mensuelle")


def to_disk(destination, disk, today=None):
    """Copie sur le disque, de la plus vieille à la plus récente, chaque image vérifiée de Drive pas encore vérifiée là, sauf
    celles que sa rétention retirerait; puis la rétention du disque (D066), d'après ses mensuelles vérifiées."""
    today = today or date.today()
    sources = images_at(destination)
    folder = moteur.check_disk(disk, home(), drive(), PROPRIETAIRE)
    if folder is None:
        waiting = [name for name in sources if name not in set(logged_images(ON_DISK))]
        log(f"ATTENTION disque : absent ({disk}); {len(waiting)} image(s) en attente")
        return
    there = images_at(folder, ON_DISK)
    latest = max((day for day, monthly, _ in [*sources.values(), *there.values()] if monthly), default=None)
    missing = sorted((value for name, value in sources.items() if name not in there and kept(*value[:2], latest, today)),
                     key=lambda value: (value[0], value[2].name))
    passphrase = keychain_passphrase() if missing else None
    for day, monthly, image in missing:
        subfolder = KINDS["mensuelle" if monthly else "quotidienne"][0]
        try:
            files = copy_to_disk(image, folder / subfolder, passphrase)
        except FileExistsError as exc:
            log(f"ATTENTION disque : {exc}")
            continue
        log(f"OK disque : {subfolder}/{image.name} ({files} fichiers vérifiés)")
    if not missing:
        log(f"OK disque : à jour ({len(there)} image(s) déjà sur le disque)")
    latest_there = max((day for day, monthly in verified(folder, ON_DISK) if monthly), default=None)
    if latest_there is not None:
        daily, monthly = prune_images(folder, latest_there, today)
        if daily or monthly:
            log(f"OK rétention du disque : {daily} quotidienne(s) d'avant la mensuelle du {latest_there} et "
                f"{monthly} mensuelle(s) de plus de 12 mois retirées")


def main():
    status = 0
    try:
        made = local_copies()
        log(f"OK local : {', '.join(p.name for p in made)}")
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        log(f"ERREUR local : {exc}")
        status = 1
    destination = os.environ.get("MONETA_SAUVEGARDE_DESTINATION")
    if not destination:
        log("ERREUR hors Mac : MONETA_SAUVEGARDE_DESTINATION absente")
        status = 1
    else:
        try:
            offsite(destination)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            # Drive fermé ou endormi : rien n'est perdu, la nuit suivante recommence.
            log(f"ERREUR hors Mac : {exc}")
            status = 1
        disk = os.environ.get("MONETA_SAUVEGARDE_DISQUE")
        if not disk:
            log("ATTENTION disque : MONETA_SAUVEGARDE_DISQUE absente")
        else:
            try:
                to_disk(destination, disk)
            except (OSError, ValueError, RuntimeError) as exc:
                log(f"ERREUR disque : {exc}")
                status = 1
    try:
        retention()
    except OSError as exc:
        log(f"ERREUR rétention : {exc}")
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
