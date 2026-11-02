"""bin/moneta-migrer et bin/moneta-verifier --json (spec 013, contrat verificateur-agents) sur des données fictives.

Le vérificateur rend {"version": 1, "ok", "controles": [{"ok", "texte", "corriger"}]}, textes fermés de 200 caractères au plus;
le migrateur passe par socle.migrations, sauvegarde vérifiée avant toute application.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ['MONETA_HOME'])
INBOX = Path(os.environ['MONETA_DRIVE_DOSSIER']) / 'DojoD' / '05 Administration' / 'Factures' / 'à classer'


def lancer(programme, *arguments):
    return subprocess.run([str(ROOT / 'bin' / programme), *arguments], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)


def verdict():
    fait = lancer('moneta-verifier', '--json')
    valeur = json.loads(fait.stdout)
    return fait.returncode, valeur


class TestMigrerVerifier(unittest.TestCase):
    def setUp(self):
        # Chaque test part d'un dossier de données neuf.
        shutil.rmtree(HOME, ignore_errors=True)
        HOME.mkdir(parents=True)
        HOME.chmod(0o700)
        INBOX.mkdir(parents=True, exist_ok=True)

    def base(self):
        return HOME / 'database' / 'finances.db'

    def test_parcours(self):
        # Base absente : le vérificateur le dit, le migrateur en statut ne crée rien.
        code, valeur = verdict()
        self.assertEqual((code, valeur['version'], valeur['ok']), (1, 1, False))
        self.assertIn('Base finances.db absente', [c['texte'] for c in valeur['controles'] if not c['ok']])
        statut = lancer('moneta-migrer')
        self.assertEqual(statut.returncode, 0)
        self.assertIn('En attente', statut.stdout)
        self.assertFalse(self.base().exists())
        # Application : base créée, à jour, en 600 dans un dossier 700.
        fait = lancer('moneta-migrer', '--appliquer')
        self.assertEqual(fait.returncode, 0, fait.stdout + fait.stderr)
        self.assertIn('0008', fait.stdout)
        self.assertEqual(self.base().stat().st_mode & 0o777, 0o600)
        self.assertEqual(lancer('moneta-migrer', '--appliquer').stdout.strip(), 'OK : finances déjà à jour.')
        code, valeur = verdict()
        self.assertEqual((code, valeur['ok']), (0, True), valeur)
        for controle in valeur['controles']:
            self.assertEqual(set(controle), {'ok', 'texte', 'corriger'})
            self.assertLessEqual(len(controle['texte']), 200)
            self.assertLessEqual(len(controle['corriger']), 200)

    def test_une_base_partielle_n_est_jamais_adoptee(self):
        # Repris de test_foundations.py de Smith f9c1599 (spec 013) : une base Finances avec ses tables mais sans les catégories
        # T2125 est refusée, jamais adoptée comme déjà migrée.
        from outils_tests import FOLDERS
        from socle.migrations import migrate
        ledger = HOME / 'bricolee' / 'finances.db'
        ledger.parent.mkdir()
        migrate(ledger, HOME / 'bricolee' / 'backups', FOLDERS['finances'])
        with closing(sqlite3.connect(ledger)) as con, con:
            con.execute('DELETE FROM categories')
            con.execute('DROP TABLE schema_migrations')
        with self.assertRaisesRegex(RuntimeError, 'données de référence de categories'):
            migrate(ledger, HOME / 'bricolee' / 'backups', FOLDERS['finances'])

    def test_migrations_en_attente(self):
        lancer('moneta-migrer', '--appliquer')
        with closing(sqlite3.connect(self.base())) as con, con:
            con.execute('DELETE FROM schema_migrations WHERE version=8')
        code, valeur = verdict()
        self.assertEqual(code, 1)
        self.assertIn({'ok': False, 'texte': 'Migrations en attente', 'corriger': 'bin/moneta-migrer --appliquer'},
                      valeur['controles'])

    def test_base_illisible(self):
        self.base().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.base().write_bytes(b'pas une base SQLite' * 200)
        self.base().chmod(0o600)
        code, valeur = verdict()
        self.assertEqual(code, 1)
        self.assertIn('Base finances.db illisible', [c['texte'] for c in valeur['controles'] if not c['ok']])
        self.assertNotIn('pas une base', json.dumps(valeur))

    def test_modes_et_liens(self):
        lancer('moneta-migrer', '--appliquer')
        cas = [(lambda: self.base().chmod(0o644), lambda: self.base().chmod(0o600)),
               (lambda: (HOME / 'database').chmod(0o755), lambda: (HOME / 'database').chmod(0o700)),
               (lambda: (HOME / 'lien').symlink_to('/tmp'), lambda: (HOME / 'lien').unlink())]
        for abimer, reparer in cas:
            with self.subTest(abimer=abimer):
                abimer()
                try:
                    code, valeur = verdict()
                    self.assertEqual(code, 1)
                    mauvais = [c for c in valeur['controles'] if not c['ok']]
                    self.assertEqual([c['texte'] for c in mauvais], ['Données de Moneta : mode, lien ou propriétaire'])
                    self.assertIn('chmod', mauvais[0]['corriger'])
                finally:
                    reparer()
        self.assertEqual(verdict()[0], 0)

    def test_dossier_des_factures(self):
        lancer('moneta-migrer', '--appliquer')
        texte = 'Dossier des factures « DojoD/05 Administration/Factures/à classer » dans Google Drive'
        INBOX.rmdir()
        try:
            code, valeur = verdict()
            self.assertEqual(code, 1)
            self.assertEqual([c['texte'] for c in valeur['controles'] if not c['ok']], [texte])
            INBOX.symlink_to(HOME)
            self.assertEqual(verdict()[0], 1)
            INBOX.unlink()
        finally:
            INBOX.mkdir(exist_ok=True)
        code, valeur = verdict()
        self.assertEqual(code, 0)
        self.assertIn({'ok': True, 'texte': texte, 'corriger': ''}, valeur['controles'])

    def test_sans_json(self):
        lancer('moneta-migrer', '--appliquer')
        fait = lancer('moneta-verifier')
        self.assertEqual(fait.returncode, 0)
        self.assertIn('✅', fait.stdout)
        self.assertEqual(lancer('moneta-verifier', '--autre').returncode, 2)


if __name__ == '__main__':
    unittest.main()
