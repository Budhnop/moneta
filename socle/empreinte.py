"""L'épingle d'une copie du socle : version, commit, SHA-256 de chaque fichier. Lit seulement, ne corrige jamais.

Une copie n'a jamais de cache : Python chargerait un .pyc planté à la place d'une source conforme, donc comparer le nomme.
"""
import hashlib
import json
import os
from pathlib import Path
import re

CLES = {'version', 'commit', 'fichiers'}
VERSION = re.compile(r'^\d+\.\d+\.\d+$')
COMMIT = re.compile(r'^[0-9a-f]{40}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
LUE = re.compile(r'''^__version__\s*=\s*["']([^"']*)["']''', re.M)
CACHE = '__pycache__'


def _parcourir(dossier):
    """(chemin relatif, chemin, est un lien) de tout ce que la copie contient, hors des caches."""
    dossier = Path(dossier)
    for parent, dossiers, fichiers in os.walk(dossier):
        for nom in sorted(dossiers):
            if (Path(parent) / nom).is_symlink():
                yield (Path(parent) / nom).relative_to(dossier).as_posix(), Path(parent) / nom, True
        dossiers[:] = sorted(n for n in dossiers if n != CACHE and not (Path(parent) / n).is_symlink())
        for nom in sorted(fichiers):
            chemin = Path(parent) / nom
            yield chemin.relative_to(dossier).as_posix(), chemin, chemin.is_symlink()


def _sha(chemin):
    return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()


def empreintes(dossier):
    """{chemin relatif: sha256}; ignore __pycache__; lève sur un lien."""
    trouve = {}
    for relatif, chemin, lien in _parcourir(dossier):
        if lien:
            raise ValueError(f'Lien dans la copie : {relatif}')
        trouve[relatif] = _sha(chemin)
    return dict(sorted(trouve.items()))


def comparer(dossier, epingle):
    """Les écarts nommés, vide si la copie est exactement l'épingle."""
    attendus = epingle['fichiers']
    ecarts, vus = {}, set()
    for relatif, chemin, lien in _parcourir(dossier):
        vus.add(relatif)
        if lien:
            ecarts[relatif] = 'lien'
        elif relatif not in attendus:
            ecarts[relatif] = 'ajouté'
        elif _sha(chemin) != attendus[relatif]:
            ecarts[relatif] = 'changé'
    ecarts.update({relatif: 'retiré' for relatif in attendus if relatif not in vus})
    for parent, _, fichiers in os.walk(dossier):
        if CACHE in Path(parent).parts:
            ecarts.update({(Path(parent) / nom).relative_to(dossier).as_posix(): 'cache' for nom in fichiers})
    sortie = [f'{relatif} ({quoi})' for relatif, quoi in sorted(ecarts.items())]
    try:
        trouvee = LUE.search((Path(dossier) / '__init__.py').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        trouvee = None
    version = trouvee.group(1) if trouvee else None
    if version != epingle['version'] and '__init__.py' not in ecarts:
        sortie.append(f"__version__ ({version}, épinglée {epingle['version']})")
    return sortie


def lire_epingle(chemin):
    """L'épingle validée; ValueError si elle est absente, illisible ou non conforme."""
    try:
        epingle = json.loads(Path(chemin).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError(f'Épingle absente ou illisible : {Path(chemin).name}') from None
    if not isinstance(epingle, dict) or set(epingle) != CLES:
        raise ValueError('Épingle non conforme : clés exactes version, commit, fichiers.')
    if not isinstance(epingle['version'], str) or not VERSION.match(epingle['version']):
        raise ValueError('Épingle non conforme : version MAJEUR.MINEUR.CORRECTIF.')
    if not isinstance(epingle['commit'], str) or not COMMIT.match(epingle['commit']):
        raise ValueError('Épingle non conforme : commit de 40 caractères hexadécimaux.')
    fichiers = epingle['fichiers']
    if not isinstance(fichiers, dict) or not fichiers:
        raise ValueError('Épingle non conforme : au moins un fichier.')
    for relatif, sha in fichiers.items():
        if not isinstance(sha, str) or not SHA.match(sha) or relatif.startswith('/') or '..' in relatif.split('/'):
            raise ValueError(f'Épingle non conforme : {relatif}.')
    return epingle
