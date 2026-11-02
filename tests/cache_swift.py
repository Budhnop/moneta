"""Binaires de test réutilisables, publiés atomiquement hors du dépôt."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def compile_ou_cache(source_ou_texte, destination, options=()):
    destination = Path(destination)
    if isinstance(source_ou_texte, os.PathLike):
        source = Path(source_ou_texte).read_bytes()
    else:
        try:
            fichier = '\n' not in source_ou_texte and Path(source_ou_texte).is_file()
        except OSError:
            fichier = False
        source = Path(source_ou_texte).read_bytes() if fichier else source_ou_texte.encode()
    options = list(options)
    version = subprocess.check_output(['/usr/bin/swiftc', '--version'], stderr=subprocess.STDOUT)
    sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-version'], stderr=subprocess.STDOUT)
    cle = hashlib.sha256(json.dumps([source.hex(), options, version.hex(), sdk.hex()]).encode()).hexdigest()
    cache = Path(os.environ.get('UGOD_TEST_CACHE', Path.home() / 'Library/Caches/moneta-tests')).expanduser().resolve()
    depot = Path(__file__).resolve().parents[1]
    donnees = Path.home() / '_PersonalAgents/donnees'
    if cache.is_relative_to(depot) or cache.is_relative_to(donnees):
        raise ValueError('Le cache Swift doit rester hors du dépôt et de donnees')
    sans_cache = os.environ.get('UGOD_TEST_SANS_CACHE') == '1'
    if not sans_cache:
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        cache.chmod(0o700)
    cible = cache / cle
    if sans_cache or not cible.is_file():
        with tempfile.TemporaryDirectory(prefix='moneta-swift-', dir=None if sans_cache else cache) as dossier:
            dossier = Path(dossier)
            fichier = dossier / 'main.swift'
            fichier.write_bytes(source)
            binaire = dossier / 'programme'
            modules = dossier / 'modules'
            env = dict(os.environ, TMPDIR=str(dossier), CLANG_MODULE_CACHE_PATH=str(modules),
                       SWIFT_MODULECACHE_PATH=str(modules))
            try:
                subprocess.run(['/usr/bin/swiftc', *options, '-module-cache-path', str(modules),
                                str(fichier), '-o', str(binaire)], check=True, capture_output=True,
                               timeout=300, env=env)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError((exc.stdout + exc.stderr).decode(errors='replace')) from exc
            binaire.chmod(0o700)
            if sans_cache:
                shutil.copyfile(binaire, destination)
            else:
                os.replace(binaire, cible)
    if not sans_cache:
        shutil.copyfile(cible, destination)
    destination.chmod(0o700)
    return destination
