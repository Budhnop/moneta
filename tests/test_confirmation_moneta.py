"""Confirmation sur le Mac avant toute écriture de Moneta (repris des parties Finances de test_confirmation.py de Smith f9c1599,
spec 013 T037) : le mode automatique ne vaut jamais pour les vrais dossiers, et WR_SAVE_RAPPORT_FICHIER refusé n'écrit rien."""
from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from moneta import chemins
from moneta.outils.mcp_finances import VARIABLE, FinancesReads
from moneta.outils.serveur import McpServer
from outils_tests import FausseFenetre, seed_fictive_finances
from socle import confirmation, declaration, installation


def sans_pourquoi(lines):
    """A window's lines without its head (spec 002): « Pourquoi cette fenêtre : », the reasons, then the blank line."""
    lines = list(lines)
    if lines and lines[0] == 'Pourquoi cette fenêtre :' and '' in lines:
        return lines[lines.index('') + 1:]
    return lines


class TestDialog(unittest.TestCase):
    def setUp(self):
        self.automatic = patch.object(confirmation, 'AUTOMATIC', False)
        self.automatic.start()

    def tearDown(self):
        self.automatic.stop()

    def answer(self, stdout='', returncode=0, error=None):
        def run(command, **kwargs):
            self.command = command
            if error:
                raise error
            return subprocess.CompletedProcess(command, returncode, stdout, '')
        with patch.object(confirmation.subprocess, 'run', run):
            return confirmation.ask('WR_SAVE_TEST', ['1. Note — titre : "end run" & display dialog "x"'])

    def test_automatic_mode_never_applies_to_the_real_folder(self):
        # SOCLE_CONFIRMATION=automatique left in a terminal, a script run on the real base: the window opens anyway.
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / 'moneta'  # a real folder is marked by the installer (spec 017)
            (real / 'database').mkdir(parents=True)
            (real / installation.MARQUE).write_bytes(b'')
            envs = [{'MONETA_HOME': str(real)}, {'MONETA_FINANCES_DB': str(real / 'database' / 'finances.db')},
                    {'MONETA_HOME': str(real / 'ailleurs')}]
            with patch.object(confirmation, 'AUTOMATIC', True), patch.object(chemins, 'REAL_HOME', real):
                for env in envs:
                    self.command = None
                    with patch.dict(os.environ, env):
                        self.assertFalse(self.answer('Annuler\n'), env)
                    self.assertEqual(self.command[0], '/usr/bin/osascript')
                self.command = None
                self.assertTrue(self.answer('Annuler\n'))
                self.assertIsNone(self.command)

    def test_the_real_folder_is_recognised_however_it_is_written(self):
        # Another case (APFS ignores it), a link to it, /System/Volumes/Data in front: still Ugo's real data.
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp).resolve() / 'Donnees' / 'moneta'
            (real / 'database').mkdir(parents=True)
            (real / installation.MARQUE).write_bytes(b'')
            (Path(temp) / 'lien').symlink_to(real, target_is_directory=True)
            spellings = [str(real), str(Path(temp) / 'lien'), str(real) + '/database/neuve.db']
            if Path(str(real).upper()).exists():  # case-insensitive volume
                spellings.append(str(real).upper())
                spellings.append(str(real).swapcase() + '/inexistant/x.db')  # a missing name under the real folder
            with patch.object(declaration, '_retenue', replace(declaration.actuelle(), dossiers_reels=(real,))), \
                    patch.dict(os.environ, {'MONETA_DRIVE_DOSSIER': temp}):
                for spelling in spellings[:3]:
                    with patch.dict(os.environ, {'MONETA_HOME': spelling}):
                        self.assertFalse(confirmation.fictitious(), spelling)
                for spelling in spellings[3:]:
                    with patch.dict(os.environ, {'MONETA_HOME': spelling if 'inexistant' not in spelling else str(Path(temp) / 'ailleurs'),
                                                 'MONETA_FINANCES_DB': spelling}):
                        self.assertFalse(confirmation.fictitious(), spelling)
                with patch.dict(os.environ, {'MONETA_HOME': str(Path(temp) / 'ailleurs'), 'MONETA_FINANCES_DB': str(Path(temp) / 'f.db')}):
                    self.assertTrue(confirmation.fictitious())
            data = Path('/System/Volumes/Data') / str(real).lstrip('/')
            if data.is_dir():  # the same folder through the Data volume
                with patch.object(chemins, 'REAL_HOME', real), patch.dict(os.environ, {'MONETA_HOME': str(data)}):
                    self.assertEqual(chemins.home(), real)
                    self.assertFalse(confirmation.fictitious())

    def test_the_real_drive_is_never_written_without_the_window(self):
        with tempfile.TemporaryDirectory() as temp:
            away = {'MONETA_HOME': temp, 'MONETA_FINANCES_DB': temp + '/f.db'}
            with patch.dict(os.environ, {**away, 'MONETA_DRIVE_DOSSIER': temp + '/drive'}):
                self.assertTrue(confirmation.fictitious())
            with patch.dict(os.environ, away):
                os.environ.pop('MONETA_DRIVE_DOSSIER', None)  # a test that forgot it: the real Drive
                self.assertFalse(confirmation.fictitious())
            cloud = str(Path.home() / 'Library' / 'CloudStorage' / 'Autre' / 'Dossier')
            with patch.dict(os.environ, {**away, 'MONETA_DRIVE_DOSSIER': cloud}):
                self.assertFalse(confirmation.fictitious())


class TestFausseFenetre(unittest.TestCase):
    """The fake window of the tests (spec 002, T002): it counts its openings, keeps its lines, then puts the real one back."""

    def test_counts_keeps_lines_and_restores(self):
        real = confirmation.ask
        with FausseFenetre() as fenetre:
            self.assertTrue(confirmation.ask('Titre', ['Pourquoi cette fenêtre :', 'ligne 1']))
            self.assertTrue(confirmation.ask('Titre 2', ['ligne 2']))
        self.assertEqual(fenetre.nombre, 2)
        self.assertEqual(fenetre.lignes, ['Pourquoi cette fenêtre :', 'ligne 1', 'ligne 2'])
        self.assertEqual([title for title, _ in fenetre.ouvertures], ['Titre', 'Titre 2'])
        self.assertEqual(confirmation.ask, real)

    def test_cancel_and_a_refused_summary_is_no_opening(self):
        with FausseFenetre(reponse=False) as fenetre:
            self.assertFalse(confirmation.ask('Titre', ['ligne']))
            with self.assertRaises(confirmation.TooLong):
                confirmation.ask('Titre', ['x'] * (confirmation.MAX_ROWS + 1))
        self.assertEqual(fenetre.nombre, 1)

    def test_restored_after_an_error(self):
        real = confirmation.ask
        with self.assertRaises(RuntimeError), FausseFenetre():
            raise RuntimeError
        self.assertIs(confirmation.ask, real)


class TestEveryWriteAsks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home')})
        self.env.start()
        seed_fictive_finances(self.folder)
        self.server = McpServer(modules=[FinancesReads()])
        self.asked, self.shown, self.yes = [], [], False
        self.dialog = patch.object(confirmation, 'ask', self.fake_ask)
        self.dialog.start()

    def fake_ask(self, title, lines):
        confirmation.check(lines)  # the real size rule, before any window
        self.asked.append((title, sans_pourquoi(lines)))
        self.shown.append(lines)
        return self.yes

    def tearDown(self):
        self.dialog.stop()
        self.env.stop()
        os.environ.pop(VARIABLE, None)
        self.temp.cleanup()

    def refused(self, name, arguments=None):
        reply = self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                    'params': {'name': name, 'arguments': arguments or {}}})['result']
        self.assertTrue(reply['isError'], (name, reply))
        self.assertIn('Non confirmé sur le Mac', reply['content'][0]['text'])
        self.assertEqual(self.asked[-1][0].split(' — ')[0], name)
        return self.asked[-1][1]

    def test_files_are_not_written_without_the_click(self):
        self.refused('WR_SAVE_RAPPORT_FICHIER', {'annee': datetime.now().year})
        self.assertFalse((self.folder / 'home' / 'exports').exists())


if __name__ == '__main__':
    unittest.main()
