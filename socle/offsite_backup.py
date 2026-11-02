"""Encrypted backup meant to leave the Mac: AES-256 disk image (hdiutil), verified by mounting the copy at its destination.

The passphrase comes from the macOS Keychain and reaches hdiutil through stdin only. Restoring needs nothing
but a Mac and the passphrase: double-click the .dmg. The agent gives the function that fills the image and every name of its
own (keychain service, kinds, volume); this engine reads no base.

A second destination (D079): each verified image is copied onto an external disk and verified there against the manifest
inside it (copy_to_disk).
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile

MINIMUM_PASSPHRASE = 12


def keychain_passphrase(service):
    result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Mot de passe introuvable dans le trousseau (service {service}).")
    return result.stdout.rstrip("\n")


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_destination(destination, data_home, proprietaire="de l'agent"):
    destination = Path(destination).expanduser().resolve()
    if not destination.is_dir():
        raise ValueError(f"Destination introuvable : {destination} (disque branché? dossier créé?)")
    if destination.is_relative_to(Path(data_home).resolve()):
        raise ValueError(f"La destination est dans le dossier de données {proprietaire} : ce n'est pas une copie hors du Mac.")
    return destination


def same_device(a, b):
    return os.stat(a).st_dev == os.stat(b).st_dev


def check_disk(disk, data_home, synced, proprietaire="de l'agent"):
    """The disk's folder, or None when it is absent (disk unplugged): it is never created, so nothing lands on the Mac's own
    disk under /Volumes. A link, a file, the data folder, a synced folder (Drive) or the Mac's disk are refused."""
    disk = Path(disk).expanduser()
    if disk.is_symlink():
        raise ValueError(f"Le disque {disk} est un lien : refusé.")
    if not disk.exists():
        return None
    if not disk.is_dir():
        raise ValueError(f"Le disque {disk} n'est pas un dossier : refusé.")
    disk = disk.resolve()
    if disk.is_relative_to(Path(data_home).resolve()):
        raise ValueError(f"Le disque est dans le dossier de données {proprietaire} : refusé.")
    if any(disk.is_relative_to(Path(folder).resolve()) for folder in (synced, Path.home() / "Library/CloudStorage")):
        raise ValueError(f"Le disque {disk} est dans un dossier synchronisé : refusé.")
    if same_device(disk, data_home):
        raise ValueError(f"Le disque {disk} est le même disque que le Mac : refusé.")
    return disk


def pieces(sources):
    """{« name/relative path »: (file, SHA-256)} of the plain files under each named folder: no link (file or folder),
    no hidden file. A missing folder gives nothing."""
    found = {}
    for name, root in sources.items():
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            continue
        for folder, folders, files in os.walk(root):
            folders[:] = sorted(f for f in folders if not f.startswith(".") and not (Path(folder) / f).is_symlink())
            for file in sorted(files):
                path = Path(folder) / file
                if file.startswith(".") or not stat.S_ISREG(path.lstat().st_mode):
                    continue
                found[f"{name}/{path.relative_to(root).as_posix()}"] = (path, digest(path))
    return found


def ecrire_manifeste(folder, entete):
    """MANIFESTE.txt of folder: the agent's own header, a blank line, then one digest per file; returns {relative: digest}."""
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    manifest = folder / "MANIFESTE.txt"
    manifest.write_text(entete + "\n\n" + "".join(f"{digest(p)}  {p.relative_to(folder)}\n" for p in files), encoding="utf-8")
    manifest.chmod(0o600)
    return {str(p.relative_to(folder)): digest(p) for p in files}


@contextmanager
def mounted(image, passphrase, hdiutil):
    with tempfile.TemporaryDirectory(prefix="socle-verif-") as point:
        run([hdiutil, "attach", "-quiet", "-stdinpass", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", point, str(image)],
            passphrase, "Image illisible avec ce mot de passe.")
        try:
            yield Path(point)
        finally:
            if subprocess.run([hdiutil, "detach", "-quiet", point], capture_output=True).returncode != 0:
                subprocess.run([hdiutil, "detach", "-quiet", "-force", point], capture_output=True)


def verify_image(image, passphrase, hdiutil="/usr/bin/hdiutil"):
    """Mounts the image and compares every file to the digest its manifest gives; returns how many."""
    with mounted(image, passphrase, hdiutil) as point:
        manifest = point / "MANIFESTE.txt"
        if not manifest.is_file():
            raise RuntimeError(f"Vérification échouée : MANIFESTE.txt absent de {Path(image).name}")
        listed = [line.split("  ", 1) for line in manifest.read_text(encoding="utf-8").split("\n\n", 1)[-1].splitlines() if line]
        if not listed:
            raise RuntimeError(f"Vérification échouée : manifeste vide dans {Path(image).name}")
        for value, relative in listed:
            if not (point / relative).is_file() or digest(point / relative) != value:
                raise RuntimeError(f"Vérification échouée : {relative}")
    return len(listed)


def private_copy(image, folder):
    """The image copied to a new file of unpredictable name, created exclusively (mkstemp): a link already at the destination
    is never followed. The name keeps .dmg, for hdiutil."""
    descriptor, name = tempfile.mkstemp(prefix=".partiel-", suffix=".dmg", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as sortie, open(image, "rb") as entree:
            shutil.copyfileobj(entree, sortie)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def publish(partial, final):
    """Gives the verified copy its final name, never over a file that appeared meanwhile (FileExistsError). A hard link refuses
    an existing name atomically; where the disk or Drive has no hard links, the last check before a rename is what is left."""
    try:
        os.link(partial, final, follow_symlinks=False)
    except FileExistsError:
        raise FileExistsError(f"déjà présente, rien d'écrasé : {Path(final).name}") from None
    except OSError:
        if os.path.lexists(final):
            raise FileExistsError(f"déjà présente, rien d'écrasé : {Path(final).name}") from None
        partial.rename(final)


def copy_to_disk(image, folder, passphrase, hdiutil="/usr/bin/hdiutil"):
    """Copies the image into folder (one of the disk's kind folders) and verifies the copy: same bytes, then mounted and every
    file compared. An identical image already there is verified again; a different one is never overwritten
    (FileExistsError). A failure leaves no image. Returns the files checked."""
    image, folder = Path(image), Path(folder)
    if folder.is_symlink():
        raise ValueError(f"« {folder.name} » est un lien sur le disque : refusé.")
    folder.mkdir(exist_ok=True, mode=0o700)
    target = folder / image.name
    source = digest(image)
    if target.exists() or target.is_symlink():
        if not stat.S_ISREG(target.lstat().st_mode) or digest(target) != source:
            raise FileExistsError(f"déjà présente et différente, rien d'écrasé : {image.name}")
        return verify_image(target, passphrase, hdiutil)
    partial = private_copy(image, folder)
    try:
        if digest(partial) != source:
            raise RuntimeError(f"Vérification échouée : copie différente de {image.name}")
        files = verify_image(partial, passphrase, hdiutil)
        publish(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return files


def run(command, passphrase, message):
    result = subprocess.run(command, input=passphrase.encode(), capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"{message} ({result.stderr.decode(errors='replace').strip()})")


def create_backup(destination, passphrase, remplir, *, data_home, volume, sorte, hdiutil="/usr/bin/hdiutil", now=None,
                  proprietaire="de l'agent"):
    """Build, encrypt, copy and verify into the kind's folder of the destination; returns (final image path, files checked).
    remplir(folder) fills the image and returns {relative: digest}; sorte is (subfolder, name prefix); volume is the volume's
    name without its time. A failure leaves no image at destination."""
    data_home = Path(data_home)
    destination = check_destination(destination, data_home, proprietaire)
    if len(passphrase) < MINIMUM_PASSPHRASE:
        raise ValueError(f"Mot de passe trop court : au moins {MINIMUM_PASSPHRASE} caractères.")
    subfolder, prefix = sorte
    destination = destination / subfolder
    if destination.is_symlink():
        raise ValueError(f"« {subfolder} » est un lien à la destination : refusé.")
    destination.mkdir(exist_ok=True, mode=0o700)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    name = f"{prefix}{stamp}.dmg"
    final = destination / name
    if final.exists():
        raise ValueError(f"Une sauvegarde porte déjà ce nom : {final}")
    with tempfile.TemporaryDirectory(prefix="socle-image-") as work:
        work = Path(work)
        content = work / "contenu"
        content.mkdir(mode=0o700)
        expected = remplir(content)
        image = work / name
        run([hdiutil, "create", "-quiet", "-encryption", "AES-256", "-stdinpass", "-srcfolder", str(content),
             "-format", "UDZO", "-volname", f"{volume} {stamp}", str(image)], passphrase, "Création de l'image chiffrée impossible.")
        partial = private_copy(image, destination)
        try:
            # Verify the copy that actually left, not the local original.
            with mounted(partial, passphrase, hdiutil) as point:
                for relative, value in expected.items():
                    if not (point / relative).is_file() or digest(point / relative) != value:
                        raise RuntimeError(f"Vérification échouée : {relative}")
            publish(partial, final)
        finally:
            partial.unlink(missing_ok=True)
    return final, len(expected)
