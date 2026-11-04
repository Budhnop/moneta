"""Le lot Finances de Moneta : go, fenêtre et journal (repris des parties Finances de test_mcp_writes.py de Smith f9c1599, spec 013
T037). Les tests de Smith sur le seul lot du Dojo y restent; ceux du signal et du plafond du jour sont rejoués ici avec des dépenses."""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from moneta import regles
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
from moneta.outils.serveur import McpServer
from outils_tests import FausseFenetre, SourceStore, seed_fictive_finances
from socle import confirmation, journal_ecritures, signal_texte
from socle.confirmation import describe
from test_banque import CHEQUING, DOJO_LINES, PERSO_LINES, SAVINGS, ofx, statement
from test_budget import base_budget
from test_points import fill

confirmation.AUTOMATIC = True  # fictitious data only: no dialog on the Mac

GO = "go : attends le oui d'Ugo; aucune fenêtre"
POURQUOI = 'Pourquoi cette fenêtre :'


def call(server, name, arguments):
    return server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})['result']


def scalar(db, sql, *params):
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as con:
        return con.execute(sql, params).fetchone()[0]


def backups(folder):
    return len(list((Path(folder) / 'backups').glob('*.db')))


class Ecritures(unittest.TestCase):
    """Spec 002, récit 1, on fictitious bases: an old signal (31 min and more), the journal inside a temporary MONETA_HOME."""

    TODAY = date.today().isoformat()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home'),
                                           'SOCLE_SIGNAL_TEXTE': str(self.folder / 'signaux' / 'texte-autrui.json')})
        self.env.start()
        signal_texte.inscrire(maintenant=datetime.now(timezone.utc) - timedelta(hours=2))

    def tearDown(self):
        self.env.stop()
        os.environ.pop(VARIABLE, None)
        self.temp.cleanup()

    def ok(self, name, arguments=None, server=None):
        reply = call(server or self.server, name, arguments or {})
        self.assertFalse(reply['isError'], (name, arguments, reply))
        return reply['structuredContent']

    def journal(self):
        """Every line of the journal, in order (all months)."""
        return [json.loads(line) for path in sorted(journal_ecritures.dossier().glob('*.jsonl'))
                for line in path.read_text(encoding='utf-8').splitlines()]

    def spy_commit(self, lot):
        """The types of the journal's new lines at the moment the store writes: the intention must already be there."""
        seen, real, start = [], lot.store.commit, len(self.journal())

        def commit(*a, **k):
            seen.append([line['type'] for line in self.journal()[start:]])
            return real(*a, **k)
        lot.store.commit = commit
        return seen

    def finances(self, before=None):
        """A fictive ledger (prepared by before, ledger in hand), its batch and a server."""
        ledger = seed_fictive_finances(self.folder)
        if before:
            before(ledger)
        lot = finances_lot(SourceStore(ledger, self.folder / 'backups', variable=VARIABLE))
        self.server = McpServer(modules=[FinancesReads(), lot])
        return ledger, self.server

    def signal(self, minutes):
        signal_texte.inscrire(maintenant=datetime.now(timezone.utc) - timedelta(minutes=minutes))

    def window(self, prepared, raisons, server=None):
        """The summary said fenetre for these reasons; the write opens one window, says why first, and logs it."""
        labels = [regles.RAISONS[r] for r in raisons]
        self.assertEqual((prepared['confirmation'], prepared['raisons_fenetre']), ('fenetre', labels))
        self.assertNotIn(GO, prepared['message'])
        return self.commit_window(prepared['code'], raisons, server)

    def commit_window(self, code, raisons, server=None):
        labels = [regles.RAISONS[r] for r in raisons]
        before = len(self.journal())
        with FausseFenetre() as fenetre:
            done = self.ok('WR_SAVE_FINANCES_BD', {'code': code}, server=server)
        self.assertEqual(fenetre.nombre, 1)
        self.assertEqual((done['enregistre'], done['confirmation'], done['raisons_fenetre']), (True, 'fenetre', labels))
        shown = fenetre.ouvertures[0][1]
        self.assertEqual(shown[shown.index(POURQUOI) + 1:shown.index(POURQUOI) + 1 + len(labels)], labels)
        intention, result = self.journal()[before:]
        self.assertEqual((intention['outil'], intention['mode'], intention['raisons']), ('WR_SAVE_FINANCES_BD', 'fenetre', list(raisons)))
        self.assertEqual((result['id'], result['resultat']), (intention['id'], 'enregistre'))
        self.assertEqual(intention['agent'], 'moneta')
        return done

    def go(self, server=None):
        prepared = self.ok('finances_lot_preparer', server=server)
        self.assertEqual(prepared['confirmation'], 'go', prepared)
        with FausseFenetre() as fenetre:
            self.ok('WR_SAVE_FINANCES_BD', {'code': prepared['code']}, server=server)
        self.assertEqual(fenetre.nombre, 0)

    def expense(self, amount):
        return self.ok('depense_preparer', {'date': self.TODAY, 'montant': amount, 'categorie': '8811'})


class TestGo(Ecritures):
    def assert_go(self, prepared, montant):
        self.assertEqual((prepared['confirmation'], prepared['raisons_fenetre']), ('go', []))
        self.assertEqual(prepared['montant_compte'], montant)
        self.assertIn(GO, prepared['message'])

    def assert_logged(self, before, outil, lot, lines):
        """One go intention, written before the store's commit, then its result enregistre."""
        new = self.journal()[before:]
        self.assertEqual([line['type'] for line in new], ['intention', 'resultat'])
        intention, result = new
        self.assertEqual((intention['outil'], intention['lot'], intention['mode'], intention['raisons']), (outil, lot, 'go', []))
        self.assertEqual([f"{e['numero']}. {e['operation']} — {e['resume']}" for e in intention['etapes']], lines)
        self.assertEqual((result['id'], result['resultat']), (intention['id'], 'enregistre'))

    def test_classification(self):
        imports = self.folder / 'home' / 'imports'
        imports.mkdir(parents=True)
        (imports / 'septembre.ofx').write_bytes(ofx(statement(CHEQUING, PERSO_LINES), statement(SAVINGS, DOJO_LINES)))
        ledger, server = self.finances()
        lot = server.parts[1]
        self.ok('releve_import_preparer', {'fichier': 'septembre.ofx', 'comptes': {'…3456 EOP': 'Perso', '…3456 ES1': 'Dojo'}},
                server=server)
        self.ok('WR_SAVE_FINANCES_BD', {'code': self.ok('finances_lot_preparer', server=server)['code']}, server=server)
        # The id from the base itself: a read tool would show bank text (récit 2 would then turn on the signal).
        deposit = scalar(ledger, "SELECT id FROM operations WHERE description LIKE 'DEPOT INSCRIPTIONS%'")
        self.ok('operation_classer_preparer', {'id': deposit, 'categorie': 'depot_dojo'}, server=server)
        lines = [describe(i, s.summary, s.title) for i, s in enumerate(lot.steps, 1)]
        prepared = self.ok('finances_lot_preparer', server=server)
        self.assert_go(prepared, 0)
        before, saved, seen = len(self.journal()), backups(self.folder), self.spy_commit(lot)
        with FausseFenetre() as fenetre:
            done = self.ok('WR_SAVE_FINANCES_BD', {'code': prepared['code']}, server=server)
        self.assertEqual(fenetre.nombre, 0)
        self.assertEqual((done['confirmation'], done['raisons_fenetre']), ('go', []))
        self.assertEqual(seen, [['intention']])
        self.assertEqual(backups(self.folder), saved + 1)
        self.assert_logged(before, 'WR_SAVE_FINANCES_BD', 'finances_lot', lines)
        self.assertEqual(scalar(ledger, 'SELECT p.code FROM operations o JOIN categories_perso p ON p.id=o.categorie_perso_id '
                                        'WHERE o.id=?', deposit), 'depot_dojo')


class TestFenetreRevient(Ecritures):
    """T025, récit 2: someone else's text read in the last 30 minutes (any conversation), or a removal, opens the window
    whatever the batch. The commit's decision is the one that acts: stricter than the summary's, never looser."""

    def test_a_signal_of_29_minutes_opens_the_window(self):
        self.finances()
        self.signal(29)
        self.expense(60)
        self.window(self.ok('finances_lot_preparer'), ['texte_autrui_recent'])

    def test_a_signal_of_31_minutes_goes(self):
        self.finances()
        self.signal(31)
        self.expense(60)
        self.go()

    def test_a_signal_between_the_summary_and_the_write_opens_the_window(self):
        self.finances()
        self.expense(60)
        prepared = self.ok('finances_lot_preparer')
        self.assertEqual((prepared['confirmation'], prepared['raisons_fenetre']), ('go', []))
        signal_texte.inscrire()  # a read in another conversation, after the summary
        self.commit_window(prepared['code'], ['texte_autrui_recent'])

    def test_more_than_1000_on_the_go_in_a_day_opens_the_window(self):
        self.finances()
        self.expense(600)
        self.go()
        self.expense(400)
        self.go()
        self.expense(1)
        self.window(self.ok('finances_lot_preparer'), ['plafond_jour'])

    def test_a_missing_or_future_signal_is_a_doubt(self):
        self.finances()
        for spoil in (lambda: Path(os.environ['SOCLE_SIGNAL_TEXTE']).unlink(),
                      lambda: signal_texte.inscrire(maintenant=datetime.now(timezone.utc) + timedelta(minutes=5))):
            spoil()
            self.expense(60)
            self.window(self.ok('finances_lot_preparer'), ['signal_douteux'])

    def removal(self, outil, arguments, server=None):
        self.ok(outil, arguments, server=server)
        return self.window(self.ok('finances_lot_preparer', server=server), ['suppression'], server)

    def test_cancelling_an_expense_or_an_income_and_removing_a_rule(self):
        ledger, server = self.finances()
        self.ok('depense_preparer', {'date': self.TODAY, 'montant': 20, 'categorie': '8811'}, server=server)
        self.ok('revenu_preparer', {'date': self.TODAY, 'montant': 85, 'type': 'kimono', 'mode': 'virement'}, server=server)
        self.go(server)
        # A rule has its window too (F11, D091), with its own reason.
        self.ok('regle_preparer', {'motif': 'location salle', 'categorie': '8910'}, server=server)
        self.window(self.ok('finances_lot_preparer', server=server), ['reglage'], server)
        self.removal('depense_annulation_preparer', {'id': scalar(ledger, 'SELECT max(id) FROM depenses')}, server)
        self.removal('revenu_annulation_preparer', {'id': scalar(ledger, 'SELECT max(id) FROM revenus')}, server)
        rule = self.ok('finances_reference', server=server)['regles'][0]['id']
        self.removal('regle_retirer_preparer', {'id': rule}, server)

    def test_removing_the_points_goal(self):
        _, server = self.finances(lambda ledger: fill(ledger, [], [{'cible': 120_000, 'date_visee': '2028-05-01',
                                                                     'cree_le': '2026-11-02 10:00:00'}]))
        self.removal('objectif_points_retirer_preparer', {}, server)

    def test_removing_a_budget(self):
        _, server = self.finances(lambda ledger: base_budget(ledger, budgets=[('epicerie', '2026-06', 80_000)]))
        self.removal('budget_retirer_preparer', {'categorie': 'epicerie'}, server)


if __name__ == '__main__':
    unittest.main()
