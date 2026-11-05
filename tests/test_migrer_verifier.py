"""bin/moneta-migrer et bin/moneta-verifier --json (spec 013, contrat verificateur-agents) sur des données fictives.

Le vérificateur rend {"version": 2, "ok", "controles": [{"ok", "texte", "corriger", "attention"}]}, textes fermés de 200 caractères au plus;
le migrateur passe par socle.migrations, sauvegarde vérifiée avant toute application.
"""
from contextlib import closing
from datetime import datetime
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
LECTEUR = HOME / 'apps' / 'moneta-releve'


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
        # Un faux lecteur, exécutable en 700 comme le pose bin/moneta-construire-releve (T066).
        LECTEUR.parent.mkdir(mode=0o700)
        LECTEUR.write_text('#!/bin/sh\nexit 0\n')
        LECTEUR.chmod(0o700)
        # Un journal de nuit sain, daté d'aujourd'hui : les trois copies vérifiées (spec 018, T024) ne sont pas ce que ces tests regardent.
        maintenant = datetime.now()
        image = f'moneta-{maintenant:%Y-%m-%d}_211500.dmg'
        (HOME / 'private').mkdir(mode=0o700)
        journal = HOME / 'private' / 'sauvegarde-quotidienne.log'
        journal.write_text(''.join(f'{maintenant:%Y-%m-%d %H:%M} OK {etape} : {detail}\n' for etape, detail in (
            ('local', f'finances_{maintenant:%Y-%m-%d}.db'), ('hors Mac', f'quotidiennes/{image} (1 fichiers vérifiés, 0 pièce(s))'),
            ('disque', f'quotidiennes/{image} (1 fichiers vérifiés)'))), encoding='utf-8')
        journal.chmod(0o600)

    def base(self):
        return HOME / 'database' / 'finances.db'

    def test_parcours(self):
        # Base absente : le vérificateur le dit, le migrateur en statut ne crée rien.
        code, valeur = verdict()
        self.assertEqual((code, valeur['version'], valeur['ok']), (1, 2, False))
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
            self.assertEqual(set(controle), {'ok', 'texte', 'corriger', 'attention'})
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
        self.assertIn({'ok': False, 'texte': 'Migrations en attente', 'corriger': 'bin/moneta-migrer --appliquer', 'attention': False},
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
        self.assertIn({'ok': True, 'texte': texte, 'corriger': '', 'attention': False}, valeur['controles'])

    def test_lecteur_de_releves(self):
        # Spec 013, T066 : le contrôle du lecteur, fait par smith-verifier jusqu'à T051, passe à moneta-verifier. Le lecteur
        # est le seul fichier des données de Moneta en 700 (exécutable), et il doit l'être : en 600 il ne se lance pas, en 755
        # il sort des modes privés.
        lancer('moneta-migrer', '--appliquer')
        texte = 'Lecteur de relevés PDF présent (moneta-releve)'
        self.assertIn({'ok': True, 'texte': texte, 'corriger': '', 'attention': False}, verdict()[1]['controles'])
        LECTEUR.unlink()
        code, valeur = verdict()
        self.assertEqual(code, 1)
        self.assertIn(texte, [c['texte'] for c in valeur['controles'] if not c['ok']])
        self.assertEqual([c['corriger'] for c in valeur['controles'] if not c['ok']],
                         ['lancer bin/moneta-construire-releve (sans lui, aucun relevé PDF ne se lit)'])
        for mode, mauvais in ((0o600, ['Données de Moneta : mode, lien ou propriétaire', texte]),
                              (0o755, ['Données de Moneta : mode, lien ou propriétaire'])):
            with self.subTest(mode=oct(mode)):
                LECTEUR.write_text('#!/bin/sh\nexit 0\n')
                LECTEUR.chmod(mode)
                code, valeur = verdict()
                self.assertEqual(code, 1)
                self.assertEqual(sorted(c['texte'] for c in valeur['controles'] if not c['ok']), mauvais)
                LECTEUR.unlink()
        LECTEUR.symlink_to('/bin/sh')
        self.assertEqual(sorted(c['texte'] for c in verdict()[1]['controles'] if not c['ok']),
                         ['Données de Moneta : mode, lien ou propriétaire', texte])
        LECTEUR.unlink()
        # Un autre fichier des apps en 700 reste refusé : l'exception est le lecteur seul.
        (HOME / 'apps' / 'autre').write_text('x')
        (HOME / 'apps' / 'autre').chmod(0o700)
        LECTEUR.write_text('#!/bin/sh\nexit 0\n')
        LECTEUR.chmod(0o700)
        self.assertEqual([c['texte'] for c in verdict()[1]['controles'] if not c['ok']],
                         ['Données de Moneta : mode, lien ou propriétaire'])

    def test_sans_json(self):
        lancer('moneta-migrer', '--appliquer')
        fait = lancer('moneta-verifier')
        self.assertEqual(fait.returncode, 0)
        self.assertIn('✅', fait.stdout)
        self.assertEqual(lancer('moneta-verifier', '--autre').returncode, 2)


if __name__ == '__main__':
    unittest.main()
