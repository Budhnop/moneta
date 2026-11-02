"""Finances phase 1 sur bases fictives : schéma, lot WR_SAVE_FINANCES_BD, compléments, annulation, rapport annuel."""
from contextlib import closing
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from outils_tests import FOLDERS, migrate
from moneta.outils.serveur import McpServer
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
from outils_tests import SourceStore, seed_fictive, seed_fictive_finances
from socle import confirmation

confirmation.AUTOMATIC = True  # fictitious data only: no dialog on the Mac

ROOT = Path(__file__).resolve().parents[1]
TODAY = date.today().isoformat()


def scalar(db, sql, *params):
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as con:
        return con.execute(sql, params).fetchone()[0]


class TestFinances(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home')})
        self.env.start()
        self.dojo = seed_fictive(self.folder)
        self.ledger = seed_fictive_finances(self.folder)
        self.backups = self.folder / 'backups-finances'
        self.lot = finances_lot(SourceStore(self.ledger, self.backups, variable=VARIABLE))
        self.server = McpServer(modules=[FinancesReads(), self.lot])

    def tearDown(self):
        self.env.stop()
        os.environ.pop('UGOD_DB', None)
        os.environ.pop(VARIABLE, None)
        self.temp.cleanup()

    def call(self, name, arguments=None):
        return self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                   'params': {'name': name, 'arguments': arguments or {}}})['result']

    def ok(self, name, arguments=None):
        reply = self.call(name, arguments)
        self.assertFalse(reply['isError'], (name, arguments, reply))
        return reply['structuredContent']

    def fails(self, name, arguments, fragment):
        reply = self.call(name, arguments)
        self.assertTrue(reply['isError'], (name, arguments, reply))
        self.assertIn(fragment, reply['content'][0]['text'])

    def commit(self):
        prepared = self.ok('finances_lot_preparer')
        self.assertEqual(prepared['confirmer_avec'], 'WR_SAVE_FINANCES_BD')
        return self.ok('WR_SAVE_FINANCES_BD', {'code': prepared['code']})

    def backups_count(self):
        return len(list(self.backups.glob('*.db')))

    def test_schema_from_migrations(self):
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM categories'), 14)
        self.assertEqual(scalar(self.ledger, "SELECT nom FROM comptes"), 'Dojo')
        self.assertIsNone(scalar(self.ledger, "SELECT pct_deductible FROM categories WHERE code='9281'"))
        self.assertEqual(scalar(self.ledger, "SELECT pct_deductible FROM categories WHERE code='8523'"), 50)
        self.assertEqual(migrate(self.ledger, self.backups, FOLDERS['finances']), ([], None))
        with self.assertRaises(sqlite3.IntegrityError), closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("INSERT INTO depenses(compte_id, date, montant_cents, categorie_id, pct_deductible) VALUES (1, '2026-01-01', 0, 1, 100)")

    def test_catalog_is_separate_from_the_dojo_lot(self):
        # Spec 013 : le lot du Dojo est resté chez Smith; Moneta ne le connaît pas du tout.
        names = {t['name'] for t in self.ok_list()}
        self.assertTrue({'depense_preparer', 'finances_lot_preparer', 'WR_SAVE_FINANCES_BD', 'finances_rapport'} <= names)
        self.assertFalse({'lot_preparer', 'lot_voir', 'WR_SAVE_DOJO_BD'} & names)
        self.ok('depense_preparer', {'date': TODAY, 'montant': 12.5, 'categorie': '8811'})
        self.assertEqual(self.unknown('lot_voir'), 'Outil inconnu.')
        # A finances code never confirms the Dojo lot; refusing it there leaves the finances proposal intact.
        code = self.ok('finances_lot_preparer')['code']
        self.assertEqual(self.unknown('WR_SAVE_DOJO_BD', {'code': code}), 'Outil inconnu.')
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM depenses'), 0)
        self.ok('WR_SAVE_FINANCES_BD', {'code': code})
        self.fails('WR_SAVE_FINANCES_BD', {'code': code}, 'Aucun lot préparé')
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM depenses'), 1)
        self.assertEqual(scalar(self.dojo, "SELECT count(*) FROM audit_log WHERE action NOT IN ('migration')"), 0)

    def unknown(self, name, arguments=None):
        return self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                   'params': {'name': name, 'arguments': arguments or {}}})['error']['message']

    def ok_list(self):
        return self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']

    def test_validation_refuses_before_anything_is_written(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.fails('depense_preparer', {'date': tomorrow, 'montant': 10, 'categorie': '8811'}, 'futur')
        self.fails('depense_preparer', {'date': TODAY, 'montant': 10, 'categorie': '1234'}, 'Catégorie inconnue')
        self.fails('depense_preparer', {'date': TODAY, 'montant': 10.001, 'categorie': '8811'}, 'Montant invalide')
        self.fails('depense_preparer', {'date': TODAY, 'montant': 40, 'categorie': '9281'}, 'Pourcentage déductible requis')
        self.fails('depense_preparer', {'date': TODAY, 'montant': 40, 'categorie': '8811', 'mode': 'troc'}, 'mode')
        self.fails('depense_preparer', {'date': TODAY, 'montant': 40, 'categorie': '8811', 'sql': 'DROP'}, 'Arguments attendus')
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_lot_writes_once_with_one_backup_and_reports_gaps(self):
        meal = self.ok('depense_preparer', {'date': TODAY, 'montant': 85.25, 'categorie': '8523', 'fournisseur': 'Restaurant fictif'})
        self.assertEqual((meal['pourcentage_deductible'], meal['deductible']), (50, 42.63))
        self.assertEqual(meal['manque'], ['piece', 'mode'])
        self.ok('depense_preparer', {'date': TODAY, 'montant': 60, 'categorie': '9281', 'pourcentage': 70, 'mode': 'carte',
                                     'piece': 'REC-1', 'fournisseur': 'Garage fictif'})
        self.ok('depense_preparer', {'date': TODAY, 'montant': 300, 'categorie': '8910', 'mode': 'virement', 'piece': 'B-2',
                                     'fournisseur': 'Salle fictive'})
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM depenses'), 0)
        done = self.commit()
        self.assertEqual(len(done['etapes']), 3)
        self.assertEqual(self.backups_count(), 1)
        self.assertEqual(scalar(self.ledger, 'SELECT sum(montant_cents) FROM depenses'), 44525)
        self.assertEqual(scalar(self.ledger, "SELECT count(*) FROM audit_log WHERE action='ajouter_depense'"), 3)

        incomplete = self.ok('finances_depenses', {'incompletes': True})
        self.assertEqual([d['categorie'] for d in incomplete['depenses']], ['8523'])
        number = incomplete['depenses'][0]['id']
        completed = self.ok('depense_completer_preparer', {'id': number, 'piece': 'F-77', 'mode': 'comptant'})
        self.assertEqual(completed['changements']['piece'], {'avant': None, 'apres': 'F-77'})
        self.commit()
        self.assertEqual(self.ok('finances_depenses', {'incompletes': True})['nombre'], 0)

        year = date.today().year
        report = self.ok('finances_rapport', {'annee': year})
        income = round(scalar(self.dojo, "SELECT coalesce(sum(montant), 0) FROM paiements WHERE substr(date_paiement, 1, 4)=?", str(year)), 2)
        self.assertEqual(report['revenus_dojo']['total'], income)
        self.assertEqual(report['depenses']['total_brut'], 445.25)
        self.assertEqual(report['depenses']['total_deductible'], 42.63 + 42.0 + 300.0)
        self.assertEqual(report['revenu_net_estime'], round(income - 384.63, 2))
        self.assertEqual(report['a_completer']['depenses_incompletes']['nombre'], 0)
        self.assertEqual([l['code'] for l in report['depenses']['par_ligne']], ['8523', '8910', '9281'])

    def test_rehearsal_sees_earlier_steps_and_cancellation_leaves_the_report(self):
        self.ok('depense_preparer', {'date': TODAY, 'montant': 20, 'categorie': '8811'})
        self.commit()
        number = self.ok('finances_depenses')['depenses'][0]['id']
        self.ok('depense_annulation_preparer', {'id': number})
        # The copy already holds the first cancellation: a second one is refused before any write.
        self.fails('depense_annulation_preparer', {'id': number}, 'déjà annulée')
        self.fails('depense_completer_preparer', {'id': number, 'piece': 'X'}, 'annulée')
        self.commit()
        report = self.ok('finances_rapport', {'annee': date.today().year})
        self.assertEqual(report['depenses']['nombre'], 0)
        self.assertEqual(scalar(self.ledger, 'SELECT annulee FROM depenses WHERE id=?', number), 1)
        self.assertEqual(self.ok('finances_depenses', {'annulees': True})['nombre'], 1)

    def test_other_income_is_added_once_to_the_report(self):
        year = date.today().year
        dojo_income = self.ok('finances_rapport', {'annee': year})['revenus_dojo']['total']
        sale = self.ok('revenu_preparer', {'date': TODAY, 'montant': 85, 'type': 'kimono'})
        self.assertEqual(sale['manque'], ['mode'])
        self.ok('revenu_preparer', {'date': TODAY, 'montant': 300, 'type': 'abonnement', 'mode': 'virement', 'description': 'Hiver'})
        self.fails('revenu_preparer', {'date': TODAY, 'montant': 10, 'type': 'don'}, 'type')
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM revenus'), 0)
        self.commit()
        report = self.ok('finances_rapport', {'annee': year})
        self.assertEqual(report['autres_revenus']['total'], 385.0)
        self.assertEqual(report['autres_revenus']['par_type'], {'abonnement': 300.0, 'kimono': 85.0})
        self.assertEqual(report['revenus_total'], round(dojo_income + 385, 2))
        self.assertEqual(report['revenu_net_estime'], report['revenus_total'])
        self.assertEqual(report['a_completer']['autres_revenus_sans_mode'], {'nombre': 1, 'total': 85.0})
        number = self.ok('finances_revenus', {'type': 'kimono'})['revenus'][0]['id']
        self.ok('revenu_annulation_preparer', {'id': number})
        self.fails('revenu_annulation_preparer', {'id': number}, 'déjà annulé')
        self.commit()
        self.assertEqual(self.ok('finances_rapport', {'annee': year})['autres_revenus']['total'], 300.0)
        self.assertEqual(scalar(self.ledger, "SELECT count(*) FROM audit_log WHERE cible_type='revenu'"), 3)
        page = Path(self.ok('WR_SAVE_RAPPORT_FICHIER', {'annee': year})['fichier']).read_text()
        self.assertIn('Autres revenus — abonnement', page)

    def test_cash_income_is_left_out_unless_asked_and_always_shown(self):
        # D058: cash leaves the report, the income list and the page unless Ugo asks; the amount left out is always given.
        year = date.today().year
        base = self.ok('finances_rapport', {'annee': year})
        self.ok('revenu_preparer', {'date': TODAY, 'montant': 50, 'type': 'kimono', 'mode': 'comptant'})
        self.ok('revenu_preparer', {'date': TODAY, 'montant': 120, 'type': 'abonnement', 'mode': 'virement'})
        self.commit()
        with closing(sqlite3.connect(self.dojo)) as con, con:
            con.execute("INSERT INTO paiements(famille_id,session_id,montant,date_paiement,mode) "
                        "SELECT famille_id, session_id, 70, ?, 'comptant' FROM inscriptions LIMIT 1", (TODAY,))
        report = self.ok('finances_rapport', {'annee': year})
        self.assertEqual(report['revenus_total'], round(base['revenus_total'] + 120, 2))
        self.assertEqual(report['revenus_dojo']['total'], base['revenus_dojo']['total'])
        self.assertNotIn('comptant', report['revenus_dojo']['par_mode'])
        self.assertEqual(report['autres_revenus']['par_type'], {'abonnement': 120.0})
        self.assertEqual(report['comptant'], {'inclus': False, 'exclu': {'paiements_dojo': {'nombre': 1, 'total': 70.0},
                                                                           'autres_revenus': {'nombre': 1, 'total': 50.0}, 'total': 120.0}})
        self.assertIn('Revenu comptant exclu de ce rapport : 120,00 $', report['notes'][0])
        everything = self.ok('finances_rapport', {'annee': year, 'inclure_comptant': True})
        self.assertEqual(everything['revenus_total'], round(base['revenus_total'] + 240, 2))
        self.assertEqual(everything['comptant'], {'inclus': True, 'exclu': None})
        self.assertEqual(everything['revenu_net_estime'] - report['revenu_net_estime'], 120.0)
        listed = self.ok('finances_revenus', {})
        self.assertEqual((listed['nombre'], listed['total'], listed['comptant_exclu']), (1, 120.0, {'nombre': 1, 'total': 50.0}))
        self.assertEqual(self.ok('finances_revenus', {'inclure_comptant': True})['total'], 170.0)
        # Cancelling by number still finds a cash income.
        cash = self.ok('finances_revenus', {'inclure_comptant': True, 'type': 'kimono'})['revenus'][0]['id']
        self.ok('revenu_annulation_preparer', {'id': cash})
        self.fails('finances_rapport', {'annee': year, 'inclure_comptant': 'oui'}, 'vrai ou faux')
        page = Path(self.ok('WR_SAVE_RAPPORT_FICHIER', {'annee': year})['fichier']).read_text()
        self.assertIn('Revenu comptant exclu de ce rapport : 120,00\xa0$', page)
        self.assertEqual(page.count('Revenu comptant exclu'), 1)
        self.assertIn('sans le revenu comptant', page)
        page = Path(self.ok('WR_SAVE_RAPPORT_FICHIER', {'annee': year, 'inclure_comptant': True})['fichier']).read_text()
        self.assertNotIn('Revenu comptant exclu', page)
        self.assertIn('revenu comptant inclus', page)

    def test_printable_report_is_private(self):
        self.ok('depense_preparer', {'date': TODAY, 'montant': 19.99, 'categorie': '8521', 'description': 'Affiches <portes ouvertes>'})
        self.commit()
        made = self.ok('WR_SAVE_RAPPORT_FICHIER', {'annee': date.today().year})
        path = Path(made['fichier'])
        self.assertTrue(path.is_relative_to(self.folder / 'home' / 'exports'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        page = path.read_text()
        self.assertIn('Rapport financier', page)
        self.assertIn('19,99', page)
        self.assertNotIn('<portes', page)
        self.assertIn('manque reçu, fournisseur, mode de paiement', page)

    def test_writer_refuses_bad_input_directly(self):
        env = {**os.environ, VARIABLE: str(self.ledger)}
        for payload in ({'date': TODAY, 'montant_cents': 1.5, 'categorie': '8811'},
                        {'date': TODAY, 'montant_cents': 100, 'categorie': '9281'},
                        {'date': TODAY, 'montant_cents': 100, 'categorie': '8811', 'autre': 1}):
            out = subprocess.run([sys.executable, 'moneta/finances/finances.py', 'depense', json.dumps(payload)], cwd=ROOT, env=env,
                                 capture_output=True, text=True).stdout
            self.assertTrue(out.startswith('ERREUR'), out)
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM depenses'), 0)


class TestPointsScript(unittest.TestCase):
    """Spec 005 at the script level: the read « points » and the goal writes « objectif » and « retirer_objectif »."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home')})
        self.env.start()
        self.ledger = seed_fictive_finances(self.folder)

    def tearDown(self):
        self.env.stop()
        os.environ.pop(VARIABLE, None)
        self.temp.cleanup()

    def script(self, command, payload):
        env = {**os.environ, VARIABLE: str(self.ledger)}
        return subprocess.run([sys.executable, 'moneta/finances/finances.py', command, json.dumps(payload)], cwd=ROOT, env=env,
                              capture_output=True, text=True).stdout

    def test_points_read_command(self):
        from moneta.outils.catalogue import FINANCES_JSON, TOOLS
        out = json.loads(self.script('points', {'historique': True}))
        self.assertEqual(out, {'message': 'Aucun relevé Cobalt importé : aucun solde de points connu.', 'historique': [],
                               'objectifs_passes': []})
        self.assertTrue(self.script('points', {'autre': 1}).startswith('ERREUR : '))
        self.assertTrue(self.script('points', {'historique': 'oui'}).startswith('ERREUR : '))
        for name, access in (('finances_points', 'read'), ('finances_objectif', 'write'), ('finances_retirer_objectif', 'write')):
            self.assertIn(name, FINANCES_JSON)
            self.assertEqual(TOOLS[name].access, access)
        # The read opens the base read-only: a write through it is impossible.
        from moneta.finances import finances
        with patch.object(finances.points, 'state', lambda con, today, historique: con.execute(
                'INSERT INTO objectifs_points(cible) VALUES (1)')):
            with self.assertRaises(sqlite3.OperationalError):
                finances.points_read(self.ledger, {})


class TestObjectifScript(TestPointsScript):
    def goals(self):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute('SELECT id, cible, date_visee, fin_le IS NOT NULL, fin FROM objectifs_points ORDER BY id').fetchall()

    def audits(self):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute("SELECT action, cible_id, details FROM audit_log WHERE cible_type='objectif_points' ORDER BY id").fetchall()

    def test_fix_change_remove(self):
        from moneta.finances.finances import remove_goal, set_goal
        later = (date.today() + timedelta(days=500)).isoformat()
        self.assertIn('fixé', set_goal(self.ledger, {'cible': 100_000}))
        self.assertIn('changé', set_goal(self.ledger, {'cible': 120_000, 'date_visee': later}))
        self.assertEqual(self.goals(), [(1, 100_000, None, 1, 'change'), (2, 120_000, later, 0, None)])
        self.assertIn('retiré', remove_goal(self.ledger, {}))
        self.assertEqual(self.goals(), [(1, 100_000, None, 1, 'change'), (2, 120_000, later, 1, 'retire')])
        self.assertEqual(self.audits(), [
            ('fixer_objectif_points', 1, '100 000 points, sans date visée'),
            ('changer_objectif_points', 2, f'100 000 points, sans date visée -> 120 000 points, visé le {later}'),
            ('retirer_objectif_points', 2, f'120 000 points, visé le {later}')])

    def test_refusals_with_a_reason(self):
        from moneta.finances.finances import Refus, remove_goal, set_goal
        today = date.today()
        cases = [{'cible': 0}, {'cible': -5}, {'cible': 1.5}, {'cible': '100'}, {'cible': True}, {'cible': 10_000_001},
                 {'cible': 5, 'date_visee': '2028-5-1'}, {'cible': 5, 'date_visee': '20280501'}, {'cible': 5, 'date_visee': 'demain'},
                 {'cible': 5, 'date_visee': today.isoformat()}, {'cible': 5, 'date_visee': (today - timedelta(days=1)).isoformat()},
                 {'cible': 5, 'autre': 1}, {}]
        for payload in cases:
            with self.assertRaises(Refus, msg=payload):
                set_goal(self.ledger, payload)
        with self.assertRaises(Refus) as raised:
            remove_goal(self.ledger, {})
        self.assertIn('aucun objectif en vigueur', str(raised.exception))
        set_goal(self.ledger, {'cible': 100_000})
        with self.assertRaises(Refus) as raised:
            set_goal(self.ledger, {'cible': 100_000, 'date_visee': None})
        self.assertIn('rien ne change', str(raised.exception))
        # The Mac's own date decides, not the model's: a « tomorrow » that is today on the Mac is refused.
        with self.assertRaises(Refus):
            set_goal(self.ledger, {'cible': 7, 'date_visee': '2030-01-01'}, today=date(2030, 1, 1))
        self.assertEqual(self.goals(), [(1, 100_000, None, 0, None)])
        # A change is one transaction: an insert that fails leaves the goal in force open.
        from moneta.finances import finances
        real = finances.connect

        def failing(db, readonly=False):
            con = real(db, readonly)
            con.execute("CREATE TEMP TRIGGER casse BEFORE INSERT ON objectifs_points BEGIN SELECT RAISE(ABORT, 'casse'); END")
            return con
        with patch.object(finances, 'connect', failing), self.assertRaises(sqlite3.IntegrityError):
            set_goal(self.ledger, {'cible': 150_000})
        self.assertEqual(self.goals(), [(1, 100_000, None, 0, None)])
        out = self.script('objectif', {'cible': 0})
        self.assertTrue(out.startswith('ERREUR : cible'), out)


class TestBudgetScript(TestPointsScript):
    """Spec 004 at the script level: the writes « budget_fixer » and « budget_retirer », the Mac's date in place of today."""
    TODAY = date(2026, 11, 15)

    def lines(self):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute('SELECT b.id, p.code, b.debut, b.montant_cents, b.remplace_le IS NOT NULL FROM budgets b '
                               'JOIN categories_perso p ON p.id=b.categorie_perso_id ORDER BY b.id').fetchall()

    def audits(self):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute("SELECT action, cible_id, details FROM audit_log WHERE cible_type='budget' ORDER BY id").fetchall()

    def fix(self, categorie, cents, debut):
        from moneta.finances.finances import set_budget
        return set_budget(self.ledger, {'categorie': categorie, 'montant_cents': cents, 'debut': debut}, today=self.TODAY)

    def remove(self, categorie, debut):
        from moneta.finances.finances import remove_budget
        return remove_budget(self.ledger, {'categorie': categorie, 'debut': debut}, today=self.TODAY)

    def test_fix_change_and_remove(self):
        self.assertIn('fixé', self.fix('epicerie', 90_000, '2026-06'))
        # Changed at the same month of start: the old line is marked replaced, in the same transaction.
        self.assertIn('changé', self.fix('epicerie', 80_000, '2026-06'))
        # From another month: a new line, the earlier one stays for the months before (récit 2.2).
        self.fix('epicerie', 100_000, '2027-01')
        self.assertIn('retiré', self.remove('epicerie', '2027-03'))
        self.remove('epicerie', '2027-01')  # replaces the line of that same month
        self.assertEqual(self.lines(), [(1, 'epicerie', '2026-06', 90_000, 1), (2, 'epicerie', '2026-06', 80_000, 0),
                                        (3, 'epicerie', '2027-01', 100_000, 1), (4, 'epicerie', '2027-03', None, 0),
                                        (5, 'epicerie', '2027-01', None, 0)])
        self.assertEqual(self.audits(), [
            ('fixer_budget', 1, 'Épicerie : 900,00 $ par mois dès 2026-06'),
            ('changer_budget', 2, 'Épicerie dès 2026-06 : 900,00 $ -> 800,00 $'),
            ('fixer_budget', 3, 'Épicerie : 1 000,00 $ par mois dès 2027-01'),
            ('retirer_budget', 4, 'Épicerie : retiré dès 2027-03 (était 1 000,00 $)'),
            ('retirer_budget', 5, 'Épicerie : retiré dès 2027-01 (était 1 000,00 $)')])

    def test_refusals_with_a_reason(self):
        from moneta.finances.finances import Refus
        good = {'categorie': 'epicerie', 'montant_cents': 90_000, 'debut': '2026-11'}
        cases = [({'montant_cents': 0}, 'montant_cents'), ({'montant_cents': -500}, 'montant_cents'), ({'montant_cents': 1.5}, 'montant_cents'),
                 ({'montant_cents': '900'}, 'montant_cents'), ({'montant_cents': True}, 'montant_cents'),
                 ({'montant_cents': 10_000_001}, 'montant_cents'),
                 ({'categorie': 'revenus'}, 'catégorie de dépense'), ({'categorie': 'virement_interne'}, 'catégorie de dépense'),
                 ({'categorie': 'depot_dojo'}, 'catégorie de dépense'), ({'categorie': 'inconnue'}, 'categorie'),
                 ({'categorie': '8811'}, 'categorie'), ({'categorie': 5}, 'categorie'),
                 ({'debut': '2026-1'}, 'debut'), ({'debut': '2026-13'}, 'debut'), ({'debut': '2026-00'}, 'debut'),
                 ({'debut': 202611}, 'debut'), ({'debut': '2025-12'}, 'debut'), ({'debut': '2027-12'}, 'debut')]
        from moneta.finances.finances import set_budget
        for change, fragment in cases:
            with self.assertRaises(Refus, msg=change) as raised:
                set_budget(self.ledger, {**good, **change}, today=self.TODAY)
            self.assertIn(fragment, str(raised.exception), change)
        with self.assertRaises(Refus):
            set_budget(self.ledger, {**good, 'autre': 1}, today=self.TODAY)
        # Twelve months after the current month is the last one allowed.
        self.fix('epicerie', 90_000, '2027-11')
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("UPDATE categories_perso SET actif=0 WHERE code='cadeaux'")
        with self.assertRaises(Refus) as raised:
            self.fix('cadeaux', 5_000, '2026-11')
        self.assertIn('categorie', str(raised.exception))
        self.fix('restaurants', 25_000, '2026-06')
        for call, fragment in ((lambda: self.fix('restaurants', 25_000, '2026-09'), 'rien ne change'),
                               (lambda: self.fix('restaurants', 25_000, '2026-06'), 'rien ne change'),
                               (lambda: self.remove('restaurants', '2026-05'), 'aucun budget en vigueur pour restaurants en 2026-05'),
                               (lambda: self.remove('loisirs', '2026-11'), 'aucun budget en vigueur pour loisirs en 2026-11')):
            with self.assertRaises(Refus) as raised:
                call()
            self.assertIn(fragment, str(raised.exception))
        self.remove('restaurants', '2026-10')
        with self.assertRaises(Refus) as raised:
            self.remove('restaurants', '2026-12')
        self.assertIn('aucun budget en vigueur', str(raised.exception))
        # Nothing erased nor changed but remplace_le (FR-009).
        self.assertEqual(self.lines(), [(1, 'epicerie', '2027-11', 90_000, 0), (2, 'restaurants', '2026-06', 25_000, 0),
                                        (3, 'restaurants', '2026-10', None, 0)])
        out = self.script('budget_fixer', {'categorie': 'revenus', 'montant_cents': 100, 'debut': '2026-11'})
        self.assertTrue(out.startswith('ERREUR : '), out)

    def test_a_budget_set_again_at_the_month_of_its_removal(self):
        # Fresh review: the line replaced is a removal, without amount.
        self.fix('epicerie', 90_000, '2026-06')
        self.remove('epicerie', '2026-11')
        self.assertIn('changé', self.fix('epicerie', 80_000, '2026-11'))
        self.assertEqual(self.audits()[-1][2], 'Épicerie dès 2026-11 : retiré -> 800,00 $')
        self.assertEqual([l[3:] for l in self.lines()], [(90_000, 0), (None, 1), (80_000, 0)])

    def test_a_change_is_one_transaction(self):
        from moneta.finances import finances
        self.fix('epicerie', 90_000, '2026-06')
        real = finances.connect

        def failing(db, readonly=False):
            con = real(db, readonly)
            con.execute("CREATE TEMP TRIGGER casse BEFORE INSERT ON budgets BEGIN SELECT RAISE(ABORT, 'casse'); END")
            return con
        with patch.object(finances, 'connect', failing), self.assertRaises(sqlite3.IntegrityError):
            self.fix('epicerie', 80_000, '2026-06')
        self.assertEqual(self.lines(), [(1, 'epicerie', '2026-06', 90_000, 0)])

    def test_the_read_command(self):
        today = date.today()
        out = json.loads(self.script('budget', {'annee': today.year, 'historique': True}))
        self.assertEqual((out['message'], out['sans_budget'], out['historique']), ('Aucun budget fixé.', [], []))
        self.assertEqual(out['periode'], {'annee': today.year, 'du': f'{today.year}-01', 'au': f'{today.year}-{today.month:02d}'})
        self.fix('epicerie', 90_000, '2026-01')
        out = json.loads(self.script('budget', {'annee': 2026, 'mois': 1}))
        self.assertEqual(out['categories'][0]['budget'], 900.0)
        self.assertEqual(json.loads(self.script('budget', {'categorie': 'epicerie'})), {'lignes': [{'debut': '2026-01', 'montant_cents': 90_000}]})
        future = [{'annee': today.year + 1}] + ([{'annee': today.year, 'mois': today.month + 1}] if today.month < 12 else [])
        for payload in [{'autre': 1}, {}, {'annee': 2025}, {'annee': '2026'}, {'annee': 2026, 'mois': 13}, {'annee': 2026, 'mois': 0},
                        {'annee': 2026, 'historique': 'oui'}, {'categorie': 'revenus'}, {'categorie': 'epicerie', 'annee': 2026}] + future:
            self.assertTrue(self.script('budget', payload).startswith('ERREUR : '), payload)
        # The read opens the base read-only: a write through it is impossible.
        from moneta.finances import finances
        with patch.object(finances.budget, 'state', lambda con, today, year, wanted, historique: con.execute(
                "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2026-02', 1)")):
            with self.assertRaises(sqlite3.OperationalError):
                finances.budget_read(self.ledger, {'annee': 2026})

    def test_the_writes_are_whitelisted(self):
        from moneta.outils.catalogue import FINANCES_JSON, TOOLS
        for name, access in (('finances_budget', 'read'), ('finances_budget_fixer', 'write'), ('finances_budget_retirer', 'write')):
            self.assertIn(name, FINANCES_JSON)
            self.assertEqual(TOOLS[name].access, access)


def test_stdio_server_exposes_finances():
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    done = subprocess.run([str(ROOT / 'bin' / 'moneta-mcp'), '--fictif', '--ecriture'], cwd=ROOT, env=env, timeout=30,
                          input=''.join(json.dumps(m) + '\n' for m in messages), capture_output=True, text=True)
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    names = {t['name'] for t in replies[1]['result']['tools']}
    assert {'WR_SAVE_FINANCES_BD', 'WR_SAVE_RAPPORT_FICHIER'} <= names and 'WR_SAVE_DOJO_BD' not in names, names
    assert 'WR_SAVE_FINANCES_BD' in replies[0]['result']['instructions']


class TestStdio(unittest.TestCase):
    def test_stdio(self):
        test_stdio_server_exposes_finances()


if __name__ == '__main__':
    unittest.main()
