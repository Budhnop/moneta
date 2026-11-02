"""Les dossiers de sortie qu'un agent ne touche jamais (Drive : les sauvegardes, D056)."""
from pathlib import Path
import unicodedata


def same_name(value):
    # The Mac's disk ignores case and accent encoding: « mon agent SAUVEGARDES » and « Mon Agent sauvegardes » are one folder.
    return unicodedata.normalize('NFC', value).casefold()


def reserved(root, target, reserves):
    """True when target is a reserved folder or inside one: by its name whatever the case, or by the folder itself
    (a link or another spelling that leads to it). reserves : chemins relatifs à root, donnés par l'agent."""
    root, target = Path(root), Path(target)
    parts = [same_name(p) for p in target.relative_to(root).parts]
    if any(parts[:len(Path(name).parts)] == [same_name(p) for p in Path(name).parts] for name in reserves):
        return True
    marks = set()
    for name in reserves:
        try:
            info = (root / name).stat()
            marks.add((info.st_dev, info.st_ino))
        except OSError:
            pass
    for path in (target, *target.parents):
        try:
            info = path.stat()
        except OSError:
            continue
        if (info.st_dev, info.st_ino) in marks:
            return True
        if path == root:
            break
    return False
