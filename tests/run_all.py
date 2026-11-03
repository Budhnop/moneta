"""Toute la suite de Moneta : chaque fichier test_*.py dans son propre monde fictif, en parallèle.

Depuis la racine : bin/moneta-tests [fichiers...]. MONETA_HOME, le Drive et la fenêtre du Mac sont fictifs pour chaque fichier;
aucune donnée réelle n'est lue. MONETA_TESTS_SMITH : le dépôt de Smith dont les tests lancent le Dojo fictif (avant la mise en
service, le worktree de la spec 013). La dernière ligne dit TOUT EST VERT ou ÉCHEC, avec le nombre de tests. Une suite complète
écrit son verdict dans .conformite.json (socle.conformite, spec 014 de Smith), que smith-verifier lit sans rien lancer.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from socle.conformite import ecrire_resultat

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / 'tests'
DELAI = 600
# Variables qui pourraient mener un test vers de vraies données : jamais héritées.
RETIREES = ('MONETA_HOME', 'MONETA_FINANCES_DB', 'MONETA_DRIVE_DOSSIER', 'MONETA_LECTURE_PAIEMENTS', 'UGOD_HOME', 'UGOD_DB',
            'UGOD_FINANCES_DB', 'UGOD_DRIVE_DOSSIER', 'UGOD_SIGNAL_TEXTE', 'UGOD_SAUVEGARDE_DESTINATION', 'UGOD_CONFIRMATION',
            'UGOD_SAUVEGARDE_DISQUE', 'MONETA_SAUVEGARDE_DESTINATION', 'MONETA_SAUVEGARDE_DISQUE')


def executer(nom):
    debut = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='moneta-tests-') as monde:
        env = {k: v for k, v in os.environ.items() if k not in RETIREES}
        env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1', MONETA_HOME=str(Path(monde) / 'donnees'),
                   MONETA_DRIVE_DOSSIER=str(Path(monde) / 'drive'), UGOD_CONFIRMATION='automatique',
                   # Aucun paiement par défaut : seul un Dojo fictif (tests/outils_tests.seed_fictive) mène au programme de Smith.
                   MONETA_LECTURE_PAIEMENTS=str(ROOT / 'moneta' / 'scripts' / 'paiements_fictifs.py'),
                   MONETA_TESTS_SMITH=os.environ.get('MONETA_TESTS_SMITH') or str(Path.home() / '_PersonalAgents' / 'smith'))
        (Path(monde) / 'drive').mkdir()
        try:
            fait = subprocess.run([sys.executable, str(TESTS / nom)], cwd=ROOT, env=env, capture_output=True, text=True,
                                  timeout=DELAI, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return nom, False, 0, f'délai de {DELAI} s dépassé', time.monotonic() - debut
    sortie = fait.stdout + fait.stderr
    nombres = [int(n) for n in re.findall(r'^Ran (\d+) tests?', sortie, re.M)]
    return nom, fait.returncode == 0 and bool(nombres), sum(nombres), sortie, time.monotonic() - debut


def main(noms):
    fichiers = [Path(n).name for n in noms] or sorted(p.name for p in TESTS.glob('test_*.py'))
    debut = time.monotonic()
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        resultats = list(pool.map(executer, fichiers))
    total, echecs = 0, 0
    for nom, ok, nombre, sortie, duree in resultats:
        total += nombre
        print(f"{'OK  ' if ok else 'FAIL'} {nom} ({nombre}) — {duree:.2f} s")
        if not ok:
            echecs += 1
            print(sortie[-6000:])
    if not noms:
        # Seule une suite complète dit le verdict : un fichier lancé seul n'en dit rien.
        ecrire_resultat(ROOT, not echecs, {nom: nombre for nom, ok, nombre, *_ in resultats if ok})
    etat = 'TOUT EST VERT' if not echecs else 'ÉCHEC'
    print(f'{etat} — {total} tests OK, {len(fichiers)} fichiers, {echecs} échecs — {time.monotonic() - debut:.2f} s')
    return 1 if echecs else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
