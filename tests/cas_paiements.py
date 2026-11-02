"""Spec 011 de Smith (T004–T005) : les joueurs des captures fictives de 3351db7 (tests/references/cas_paiements, copiées telles
quelles), rejoués par le code de Moneta (spec 013). Repris de assistant/tests/capturer_cas_paiements.py de Smith, sans la capture.

Le monde de chaque cas est fait comme sur 3351db7 : la base Dojo par les migrations de Smith, ses lignes écrites telles quelles
(données anciennes atypiques comprises), la base Finances par les migrations de Moneta. Moneta lit les paiements par son seul
chemin, lecture_dojo; le programme lancé est tests/lecture_spec011.py, qui lit ce monde par le code de la spec 011 de Smith.
"""
from contextlib import closing, contextmanager, nullcontext
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
SOURCE = '3351db7'
REFERENCES = Path(__file__).resolve().parent / 'references' / 'cas_paiements'
LECTEUR = Path(__file__).resolve().parent / 'lecture_spec011.py'
JOUR = '2030-06-15'

# Fait par le Python et le code de Smith : migrations de Dojo (toutes, ou la première seulement), puis les lignes du cas.
DOJO = ('import json, sqlite3, sys\nfrom contextlib import closing\nfrom core.migrations import available, FOLDERS\n'
        'db, entree = sys.argv[1], json.loads(sys.argv[2])\n'
        'with closing(sqlite3.connect(db)) as con, con:\n'
        ' migrations = available(FOLDERS["dojo"])\n'
        ' if entree.get("base") == "sans_mode":\n'
        '  migrations = migrations[:1]\n'
        ' for _, _, migration in migrations:\n'
        '  migration.appliquer(con)\n'
        ' con.execute("INSERT INTO familles(id, nom, cree_le) VALUES (1, \'Famille Exemple\', \'2030-01-01\')")\n'
        ' con.execute("INSERT INTO familles(id, nom, cree_le) VALUES (2, \'Famille Fictive\', \'2030-01-01\')")\n'
        ' con.execute("INSERT INTO familles(id, nom, cree_le) VALUES (3, \'\', \'2030-01-01\')")\n'
        ' con.execute("INSERT INTO sessions(id, nom) VALUES (1, \'Session fictive\')")\n'
        ' con.execute("PRAGMA ignore_check_constraints=ON")\n'
        ' for famille, jour, montant, mode in entree.get("paiements", []):\n'
        '  colonnes, valeurs = "famille_id, session_id, date_paiement, montant", [famille, 1, jour, montant]\n'
        '  if entree.get("base") != "sans_mode":\n'
        '   colonnes += ", mode"\n'
        '   valeurs.append(mode)\n'
        '  con.execute(f"INSERT INTO paiements({colonnes}) VALUES ({\',\'.join(\'?\' for _ in valeurs)})", valeurs)\n'
        ' if entree.get("nombre"):\n'
        '  con.executemany("INSERT INTO paiements(famille_id, session_id, date_paiement, montant, mode) "\n'
        '                  "VALUES (1, 1, \'2030-01-01\', 1.005, \'virement\')", [()] * entree["nombre"])\n')


def dojo(dossier, entree):
    from outils_tests import SMITH
    env = {k: v for k, v in os.environ.items() if not k.startswith(('UGOD_', 'MONETA_'))}
    env.update(UGOD_HOME=str(dossier / 'smith'), PYTHONPATH=str(SMITH / 'assistant'), PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([str(SMITH / '.venv' / 'bin' / 'python'), '-c', DOJO, str(dossier / 'dojo.db'), json.dumps(entree)],
                   cwd=SMITH / 'assistant', env=env, capture_output=True, text=True, timeout=120, check=True)


@contextmanager
def monde(entree):
    with tempfile.TemporaryDirectory(prefix='cas-paiements-') as nom:
        dossier = Path(nom)
        env = {'MONETA_HOME': str(dossier / 'home'), 'MONETA_FINANCES_DB': str(dossier / 'finances.db'),
               'MONETA_DRIVE_DOSSIER': str(dossier / 'drive'), 'MONETA_LECTURE_PAIEMENTS': str(LECTEUR),
               'UGOD_DB': str(dossier / 'dojo.db'), 'PYTHONDONTWRITEBYTECODE': '1'}
        with patch.dict(os.environ, env):
            (dossier / 'home').mkdir()
            from outils_tests import FOLDERS
            from socle.migrations import available
            if entree.get('base') == 'sans_table':
                sqlite3.connect(dossier / 'dojo.db').close()  # une base vide, sans aucune table
            elif entree.get('base') != 'absente':
                dojo(dossier, entree)
            if entree.get('finances') != 'absente':
                with closing(sqlite3.connect(dossier / 'finances.db')) as con, con:
                    for _, _, migration in available(FOLDERS['finances']):
                        migration.appliquer(con)
                    con.executemany("INSERT INTO revenus(compte_id, date, montant_cents, type, mode, description, cree_le) "
                                    "VALUES (1, ?, ?, 'autre', ?, ?, '2030-01-01')", entree.get('autres', []))
            with closing(sqlite3.connect(dossier / 'dojo.db')) if entree.get('base') == 'verrouillee' else nullcontext() as verrou:
                if verrou is not None:
                    verrou.execute('BEGIN EXCLUSIVE')
                try:
                    yield dossier
                finally:
                    if verrou is not None:
                        verrou.rollback()


def jouer(famille, entree):
    with monde(entree) as dossier:
        from moneta.agent.sources import detail_annee, revenus
        from moneta.finances.finances import report
        from moneta.outils.mcp_finances import HANDLERS
        today = date.fromisoformat(entree['today'])
        try:
            if famille == 'report':
                return report(dossier / 'finances.db', entree.get('annee', today.year), cash=entree.get('comptant', False))
            if famille == 'revenus':
                return revenus(today=today)
            if famille == 'detail_annee':
                return detail_annee(today=today, genre='revenus')
            if famille == 'finances_rapport':
                # Le vrai gestionnaire MCP lance finances.py par l'exécuteur, avec notre environnement temporaire.
                return HANDLERS['finances_rapport']({'annee': entree.get('annee', today.year),
                                                    'inclure_comptant': entree.get('comptant', False)})
            raise AssertionError(famille)
        except Exception as exc:
            if entree.get('message'):
                return {'exception': type(exc).__name__, 'message': str(exc)}
            if entree.get('base') in ('absente', 'sans_table', 'verrouillee'):
                return {'exception': type(exc).__name__}
            if 'annee' in entree:
                return {'exception': type(exc).__name__, 'message': str(exc)}
            raise


JOUEURS = {nom: (lambda entree, nom=nom: jouer(nom, entree))
           for nom in ('report', 'revenus', 'detail_annee', 'finances_rapport')}
JOUEURS['complements'] = JOUEURS['modes_espaces'] = lambda entree: jouer(entree['joueur'], entree)
