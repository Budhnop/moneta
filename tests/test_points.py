"""Points pour le Japon (spec 005) sur relevés de points fictifs : migration 0006, série Cobalt, objectif par le lot Finances,
rythme et date estimée. Les valeurs attendues de l'avancement sont calculées à la main ici, jamais par le code testé (SC-003)."""
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

from socle import confirmation
from outils_tests import FausseFenetre
from outils_tests import FOLDERS, migrate
from moneta.outils import mcp_finances
from moneta.outils.serveur import McpServer
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
from moneta.finances import points
from outils_tests import SourceStore, seed_fictive, seed_fictive_finances

confirmation.AUTOMATIC = True  # fictitious data only: no dialog on the Mac

ROOT = Path(__file__).resolve().parents[1]
PERSO = 2  # compte Perso (migration Finances 0003)
CARDS = ('…1001', '…2002')  # two fictitious Cobalt cards (a replaced card): never real digits


def monthly(first, gains, start=10_000, used=None):
    """Concordant calendar-month statements from first (AAAA-MM), one per value of gains, balances following each other."""
    year, month = map(int, first.split('-'))
    out, balance = [], start
    for i, earned in enumerate(gains):
        du = date(year, month, 1)
        au = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
        spent = (used or {}).get(i, 0)
        out.append({'du': du.isoformat(), 'au': au.isoformat(), 'precedent': balance, 'gagnes': earned, 'utilises': spent,
                    'nouveau': balance + earned - spent})
        balance += earned - spent
        year, month = year + month // 12, month % 12 + 1
    return out


def fill(ledger, releves, objectifs=()):
    """The aide de test of T001: for each statement an import (cobalt, pdf), its dollar balances and, unless illisible, its points."""
    with closing(sqlite3.connect(ledger)) as con, con:
        for n, card in enumerate(CARDS, 1):
            con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (?, ?, ?, 'carte')",
                        (PERSO, str(n) * 64, card))
        for i, r in enumerate(releves, 1):
            card = r.get('carte', 1)
            number = con.execute("INSERT INTO imports(fichier, empreinte, format, source, du, au, lignes, nouvelles) "
                                 "VALUES (?, ?, 'pdf', ?, ?, ?, 0, 0)",
                                 (r.get('fichier', f"cobalt-{r['du']}.pdf"), f'{i:064d}', r.get('source', 'cobalt'), r['du'], r['au'])).lastrowid
            con.execute('INSERT INTO soldes_releves(import_id, compte_bancaire_id, du, au, solde_debut_cents, solde_fin_cents) '
                        'VALUES (?, ?, ?, ?, ?, ?)', (number, card, r['du'], r['au'], r.get('solde_debut', -12_345), r.get('solde_fin', -67_890)))
            if not r.get('illisible'):
                con.execute('INSERT INTO points_releves(import_id, compte_bancaire_id, du, au, precedent, gagnes, utilises, nouveau, concorde) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (number, card, r['du'], r['au'], r['precedent'], r['gagnes'],
                                                                   r['utilises'], r['nouveau'], r.get('concorde', 1)))
        for g in objectifs:
            con.execute('INSERT INTO objectifs_points(cible, date_visee, cree_le, fin_le, fin) VALUES (?, ?, ?, ?, ?)',
                        (g['cible'], g.get('date_visee'), g.get('cree_le', '2026-11-02 10:00:00'), g.get('fin_le'), g.get('fin')))


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home')})
        self.env.start()
        seed_fictive(self.folder)
        self.ledger = seed_fictive_finances(self.folder)
        self.lot = finances_lot(SourceStore(self.ledger, self.folder / 'backups', variable=VARIABLE))
        self.server = McpServer(modules=[FinancesReads(), self.lot])

    def tearDown(self):
        self.env.stop()
        os.environ.pop(VARIABLE, None)
        os.environ.pop('UGOD_DB', None)
        self.temp.cleanup()

    def state(self, releves, objectifs=(), today=date(2026, 7, 5), historique=False):
        fill(self.ledger, releves, objectifs)
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            return points.state(con, today, historique=historique)

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

    def rows(self, sql):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute(sql).fetchall()


class TestAide(Base):
    def test_the_helper_builds_a_readable_base(self):
        fill(self.ledger, monthly('2026-01', [100, 200]) + [{**monthly('2026-03', [5])[0], 'illisible': True}])
        self.assertEqual(self.rows("SELECT count(*) FROM imports WHERE source='cobalt' AND format='pdf'"), [(3,)])
        self.assertEqual(self.rows('SELECT count(*) FROM points_releves'), [(2,)])
        self.assertEqual(self.rows('SELECT count(*) FROM soldes_releves'), [(3,)])


class TestMigration0006(unittest.TestCase):
    """A base stopped at 0005 with a Cobalt statement gets 0006: the goals table, nothing lost."""
    def test_upgrade_and_constraints(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            old = folder / 'migrations'
            old.mkdir()
            for path in sorted(FOLDERS['finances'].glob('000[1-5]_*.py')):
                (old / path.name).write_bytes(path.read_bytes())
            ledger = folder / 'database' / 'finances.db'
            migrate(ledger, folder / 'backups', old)
            with closing(sqlite3.connect(ledger)) as con, con:
                con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (2, ?, '…1001', 'carte')", ('a' * 64,))
                con.execute("INSERT INTO imports(fichier, empreinte, format, source, du, au, lignes, nouvelles) "
                            "VALUES ('c.pdf', ?, 'pdf', 'cobalt', '2026-01-01', '2026-01-31', 0, 0)", ('b' * 64,))
                con.execute("INSERT INTO points_releves(import_id, compte_bancaire_id, du, au, precedent, gagnes, utilises, nouveau, concorde) "
                            "VALUES (1, 1, '2026-01-01', '2026-01-31', 12000, 850, 0, 12850, 1)")
            env = {**os.environ, 'MONETA_HOME': str(folder)}
            env.pop('MONETA_FINANCES_DB', None)
            status = subprocess.run([str(ROOT / 'bin' / 'moneta-migrer'), '--base', 'finances'], env=env, capture_output=True, text=True).stdout
            self.assertIn('En attente : 0006 objectifs_points', status)
            done, backup = migrate(ledger, folder / 'backups', FOLDERS['finances'])
            self.assertEqual([(v, e) for v, _, e in done][0], (6, 'appliquée'))
            self.assertTrue(backup.is_file())
            module = __import__('importlib').import_module('moneta.migrations.finances.0006_objectifs_points')
            with closing(sqlite3.connect(ledger)) as con:
                self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertTrue(module.deja_appliquee(con))
                self.assertEqual(con.execute('SELECT gagnes, nouveau FROM points_releves').fetchall(), [(850, 12850)])
                self.assertIsNotNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='idx_objectif_points_en_vigueur'").fetchone())
                con.execute("INSERT INTO objectifs_points(cible) VALUES (100000)")
                for sql in ("INSERT INTO objectifs_points(cible) VALUES (120000)",  # a second goal in force
                            "INSERT INTO objectifs_points(cible, fin_le, fin) VALUES (0, '2099-01-01', 'change')",
                            "INSERT INTO objectifs_points(cible, fin_le, fin) VALUES (10000001, '2099-01-01', 'change')",
                            "INSERT INTO objectifs_points(cible, date_visee, fin_le, fin) VALUES (5, '2028-5-1', '2099-01-01', 'change')",
                            "INSERT INTO objectifs_points(cible, fin_le, fin) VALUES (5, '2099-01-01', 'efface')",
                            "INSERT INTO objectifs_points(cible, fin_le) VALUES (5, '2099-01-01')",
                            "INSERT INTO objectifs_points(cible, fin) VALUES (5, 'retire')",
                            "INSERT INTO objectifs_points(cible, date_visee, fin_le, fin) VALUES (5, '2028-19-39', '2099-01-01', 'change')"):
                    with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                        con.execute(sql)
                con.execute("INSERT INTO objectifs_points(cible, date_visee, fin_le, fin) VALUES (5, '2028-05-01', '2099-01-01', 'change')")
            self.assertEqual(migrate(ledger, folder / 'backups', FOLDERS['finances']), ([], None))


class TestSerie(Base):
    def test_balance_of_the_latest_concordant_statement(self):
        found = self.state(monthly('2026-01', [500, 600, 700], start=1_000), today=date(2026, 4, 20))
        self.assertEqual(found['solde'], {'points': 2_800, 'au': '2026-03-31'})
        self.assertEqual(found['manques'], {'trous': [], 'illisibles': [], 'mis_de_cote': [], 'ruptures': []})
        self.assertNotIn('dernier_mis_de_cote', found)

    def test_a_statement_set_aside_is_named_never_added(self):
        rows = monthly('2026-01', [500, 600, 700], start=1_000)
        rows[1] = {**rows[1], 'concorde': 0, 'precedent': 999_999, 'nouveau': 1}  # numbers that do not hold
        found = self.state(rows, historique=True)
        self.assertEqual(found['manques']['mis_de_cote'], [{'du': '2026-02-01', 'au': '2026-02-28'}])
        # Its numbers are not reliable: no break at its edges.
        self.assertEqual(found['manques']['ruptures'], [])
        self.assertEqual(found['historique'][1], {'du': '2026-02-01', 'au': '2026-02-28', 'statut': 'mis_de_cote'})
        self.assertNotIn('999999', json.dumps(found))

    def test_latest_set_aside_gives_the_previous_balance(self):
        rows = monthly('2026-01', [500, 600, 700], start=1_000)
        rows[2] = {**rows[2], 'concorde': 0}
        found = self.state(rows)
        self.assertEqual(found['solde'], {'points': 2_100, 'au': '2026-02-28'})
        self.assertEqual(found['dernier_mis_de_cote'], {'du': '2026-03-01', 'au': '2026-03-31', 'statut': 'mis_de_cote'})

    def test_a_missing_month_is_a_hole_never_smoothed(self):
        rows = monthly('2026-01', [500, 600, 700, 800], start=1_000)
        found = self.state([rows[0], rows[1], rows[3]], historique=True)
        self.assertEqual(found['manques']['trous'], [{'du': '2026-03-01', 'au': '2026-03-31'}])
        self.assertEqual([h['du'] for h in found['historique']], ['2026-04-01', '2026-02-01', '2026-01-01'])

    def test_an_unreadable_summary_is_not_a_missing_statement(self):
        rows = monthly('2026-01', [500, 600, 700], start=1_000)
        rows[1] = {**rows[1], 'illisible': True}
        found = self.state(rows, historique=True)
        self.assertEqual(found['manques']['illisibles'], [{'du': '2026-02-01', 'au': '2026-02-28'}])
        self.assertEqual(found['manques']['trous'], [])
        self.assertEqual(found['manques']['ruptures'], [])
        self.assertEqual(found['historique'][1]['statut'], 'illisible')

    def test_a_balance_that_does_not_follow_is_a_break(self):
        rows = monthly('2026-01', [500, 600], start=1_000)
        rows[1] = {**rows[1], 'precedent': 1_400, 'nouveau': 2_000}
        self.assertEqual(self.state(rows)['manques']['ruptures'], [{'fin': '2026-01-31', 'debut': '2026-02-01'}])

    def test_an_old_statement_imported_later_takes_its_place(self):
        rows = monthly('2026-01', [500, 600, 700], start=1_000)
        found = self.state([rows[2], rows[0], rows[1]], historique=True)
        self.assertEqual([h['du'] for h in found['historique']], ['2026-03-01', '2026-02-01', '2026-01-01'])
        self.assertEqual(found['manques']['trous'], [])

    def test_no_cobalt_statement_no_balance(self):
        found = self.state([])
        self.assertNotIn('solde', found)
        self.assertEqual(found['message'], 'Aucun relevé Cobalt importé : aucun solde de points connu.')

    def test_a_replaced_card_is_one_series(self):
        rows = monthly('2026-01', [500, 600, 700], start=1_000)
        rows[2] = {**rows[2], 'carte': 2}
        found = self.state(rows)
        self.assertEqual(found['solde']['points'], 2_800)
        self.assertEqual(found['manques']['ruptures'], [])

    def test_overlapping_statements_give_no_figure_but_keep_the_goal(self):
        rows = monthly('2026-01', [500, 600], start=1_000)
        rows.append({**rows[1], 'du': '2026-02-15', 'au': '2026-03-14', 'carte': 2})
        found = self.state(rows, [{'cible': 120_000}])
        self.assertIn('se chevauchent', found['message'])
        self.assertEqual(found['objectif']['cible'], 120_000)
        self.assertNotIn('solde', found)

    def test_no_statement_for_weeks_is_said(self):
        found = self.state(monthly('2026-01', [500, 600, 700]), today=date(2026, 10, 23))
        self.assertEqual(found['manques']['en_retard_depuis'], '2026-04-01')
        self.tearDown(); self.setUp()
        self.assertNotIn('en_retard_depuis', self.state(monthly('2026-01', [500]), today=date(2026, 2, 10))['manques'])

    def test_other_sources_are_never_read(self):
        rows = monthly('2026-01', [500], start=1_000) + [{**monthly('2026-02', [77_777], start=0)[0], 'source': 'folio_desjardins'}]
        found = self.state(rows, historique=True)
        self.assertEqual(found['solde'], {'points': 1_500, 'au': '2026-01-31'})
        self.assertNotIn('77777', json.dumps(found))

    def test_history_is_24_at_most_newest_first(self):
        found = self.state(monthly('2026-01', [100] * 30), today=date(2028, 7, 5), historique=True)
        self.assertEqual(len(found['historique']), 24)
        self.assertEqual(found['historique'][0]['du'], '2028-06-01')

    def test_goal_in_force_and_past_goals(self):
        goals = [{'cible': 100_000, 'cree_le': '2026-10-01 09:00:00', 'fin_le': '2026-11-02 10:00:00', 'fin': 'change'},
                 {'cible': 120_000, 'date_visee': '2028-05-01', 'cree_le': '2026-11-02 10:00:00'}]
        found = self.state(monthly('2026-01', [500]), goals, historique=True)
        self.assertEqual(found['objectif'], {'cible': 120_000, 'date_visee': '2028-05-01', 'depuis': '2026-11-02'})
        self.assertEqual(found['objectifs_passes'], [{'cible': 100_000, 'depuis': '2026-10-01', 'fin_le': '2026-11-02', 'fin': 'change'}])
        removed = [{**goals[1], 'fin_le': '2026-12-01 10:00:00', 'fin': 'retire'}]
        self.tearDown(); self.setUp()
        self.assertNotIn('objectif', self.state(monthly('2026-01', [500]), removed))


class TestAvancement(Base):
    # Six statements, January to June 2026, 2 000 earned each, ending at 40 000.
    SIX = staticmethod(lambda **kw: monthly('2026-01', [2_000] * 6, start=28_000, **kw))

    def test_six_months_steady_pace(self):
        found = self.state(self.SIX(), [{'cible': 100_000}])
        self.assertEqual(found['solde'], {'points': 40_000, 'au': '2026-06-30'})
        self.assertEqual(found['ecart'], 60_000)
        self.assertFalse(found['cible_atteinte'])
        self.assertEqual(found['rythme']['points_par_mois'], 2_000)
        self.assertEqual(len(found['rythme']['periodes']), 6)
        # 60 000 / 2 000 = 30 months after June 2026: December 2028.
        self.assertEqual(found['date_estimee'], '2028-12')

    def test_a_target_date_too_soon(self):
        found = self.state(self.SIX(), [{'cible': 100_000, 'date_visee': '2027-06-15'}])
        # June 2026 to June 2027: 12 months; 60 000 / 12 = 5 000 a month; December 2028 is after June 2027.
        self.assertEqual(found['date_visee'], {'mois_restants': 12, 'points_par_mois_necessaires': 5_000, 'atteignable': False})

    def test_a_reachable_target_date(self):
        found = self.state(self.SIX(), [{'cible': 100_000, 'date_visee': '2029-01-01'}])
        self.assertTrue(found['date_visee']['atteignable'])

    def test_two_statements_too_early(self):
        found = self.state(monthly('2026-01', [2_000, 2_000], start=36_000), [{'cible': 100_000}])
        self.assertEqual((found['solde']['points'], found['ecart']), (40_000, 60_000))
        self.assertFalse(found['rythme']['estimable'])
        self.assertIn('au moins 3', found['rythme']['raison'])
        self.assertNotIn('date_estimee', found)

    def test_target_already_reached(self):
        found = self.state(monthly('2026-01', [2_000] * 3, start=124_000), [{'cible': 120_000}])
        self.assertTrue(found['cible_atteinte'])
        self.assertEqual(found['ecart'], -10_000)
        self.assertNotIn('date_estimee', found)

    def test_negative_adjustment_lowers_the_pace(self):
        rows = monthly('2026-01', [2_000, 2_000, -600, 2_000, 2_000, 2_000], start=28_000)
        found = self.state(rows, [{'cible': 100_000}])
        # (5 × 2 000 − 600) / 6 = 1 566.67: shown 1 567; gap 100 000 − 37 400 = 62 600; ceil(62 600 / (9 400 / 6)) = 40 months.
        self.assertEqual(found['rythme']['points_par_mois'], 1_567)
        self.assertEqual(found['date_estimee'], '2029-10')

    def test_a_big_redemption_leaves_the_pace_alone(self):
        found = self.state(self.SIX(used={4: 20_000}), [{'cible': 100_000}])
        self.assertEqual(found['solde']['points'], 20_000)
        self.assertEqual(found['rythme']['points_par_mois'], 2_000)
        # 80 000 / 2 000 = 40 months after June 2026: October 2029.
        self.assertEqual(found['date_estimee'], '2029-10')

    def test_only_the_last_six_concordant(self):
        rows = monthly('2025-11', [9_000, 9_000] + [2_000] * 6, start=10_000)
        found = self.state(rows, [{'cible': 100_000}])
        self.assertEqual(found['rythme']['points_par_mois'], 2_000)
        self.assertEqual(found['rythme']['periodes'][0], {'du': '2026-01-01', 'au': '2026-01-31'})

    def test_set_aside_statement_left_out_of_the_pace(self):
        rows = monthly('2026-01', [2_000, 2_000, 9_000, 2_000], start=28_000)
        rows[2] = {**rows[2], 'concorde': 0}
        found = self.state(rows, [{'cible': 100_000}])
        self.assertEqual(found['rythme']['points_par_mois'], 2_000)
        self.assertEqual([p['du'] for p in found['rythme']['periodes']], ['2026-01-01', '2026-02-01', '2026-04-01'])

    def test_no_date_for_a_zero_or_negative_pace(self):
        found = self.state(monthly('2026-01', [0, 0, 0], start=1_000), [{'cible': 100_000, 'date_visee': '2028-01-01'}])
        self.assertNotIn('date_estimee', found)
        self.assertIn('nul ou négatif', found['rythme']['raison'])
        # 99 000 over the 22 months from March 2026 to January 2028: 4 500, but no word on reaching it.
        self.assertEqual(found['date_visee'], {**found['date_visee'], 'mois_restants': 22, 'points_par_mois_necessaires': 4_500})
        self.assertNotIn('atteignable', found['date_visee'])
        self.assertIn('raison', found['date_visee'])

    def test_target_reached_with_a_target_date(self):
        found = self.state(monthly('2026-01', [2_000] * 3, start=124_000), [{'cible': 120_000, 'date_visee': '2027-01-01'}])
        # March 2026 to January 2027: 10 months.
        self.assertEqual(found['date_visee'], {'atteignable': True, 'mois_restants': 10})

    def test_estimated_in_the_month_of_the_target_date(self):
        # December 2028 estimated (see test_six_months_steady_pace): its statement ends after December 1st, not after the 31st.
        self.assertFalse(self.state(self.SIX(), [{'cible': 100_000, 'date_visee': '2028-12-01'}])['date_visee']['atteignable'])
        self.tearDown(); self.setUp()
        self.assertTrue(self.state(self.SIX(), [{'cible': 100_000, 'date_visee': '2028-12-31'}])['date_visee']['atteignable'])

    def test_target_date_in_the_month_of_the_last_statement(self):
        found = self.state(self.SIX(), [{'cible': 100_000, 'date_visee': '2026-06-30'}])
        self.assertIn('trop proche', found['date_visee']['raison'])

    def test_calendar_months_left(self):
        rows = [{'du': '2026-11-15', 'au': '2026-12-14', 'precedent': 0, 'gagnes': 1_000, 'utilises': 0, 'nouveau': 1_000}]
        found = self.state(rows, [{'cible': 4_000, 'date_visee': '2027-03-01'}], today=date(2026, 12, 20))
        # December 2026 to March 2027: 3 months; 3 000 / 3 = 1 000.
        self.assertEqual(found['date_visee']['mois_restants'], 3)
        self.assertEqual(found['date_visee']['points_par_mois_necessaires'], 1_000)

    def test_a_pace_that_is_not_whole(self):
        rows = monthly('2026-01', [1_000, 1_000, 1_001], start=0)
        found = self.state(rows, [{'cible': 13_001}])
        # 3 001 / 3 = 1 000.33: shown 1 000; gap 10 000; 10 000 / (3 001 / 3) = 9.997 → 10 months after March: January 2027.
        self.assertEqual(found['rythme']['points_par_mois'], 1_000)
        self.assertEqual(found['date_estimee'], '2027-01')


class TestOutil(Base):
    def test_schema_is_closed(self):
        tools = {t['name']: t for t in self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']}
        schema = tools['points_japon']['inputSchema']
        self.assertEqual((schema['additionalProperties'], schema['properties']), (False, {'historique': {'type': 'boolean'}}))
        self.assertIn('Ne jamais deviner', tools['points_japon']['description'])
        self.fails('points_japon', {'autre': 1}, 'Arguments attendus')
        self.fails('points_japon', {'historique': 'oui'}, 'vrai ou faux')

    def test_no_statement(self):
        found = self.ok('points_japon')
        self.assertEqual(found['message'], 'Aucun relevé Cobalt importé : aucun solde de points connu.')
        self.assertNotIn('solde', found)
        self.assertTrue(any('Aucun chiffre deviné' in n for n in found['notes']))
        self.assertTrue(any('points gagnés' in n for n in found['notes']))

    def test_progress_fields_and_no_dollars(self):
        fill(self.ledger, TestAvancement.SIX(), [{'cible': 100_000, 'date_visee': '2027-06-15'}])
        found = self.ok('points_japon', {'historique': True})
        for key in ('solde', 'objectif', 'ecart', 'cible_atteinte', 'rythme', 'date_estimee', 'date_visee', 'manques', 'historique'):
            self.assertIn(key, found)
        text = json.dumps(found)
        for dollars in ('12345', '67890', '…1001', 'cobalt-', 'cents'):
            self.assertNotIn(dollars, text)

    def test_a_key_outside_the_whitelist_is_removed(self):
        planted = {'solde': {'points': 5, 'au': '2026-01-31', 'apercu': '…1001'}, 'fichier': 'x.pdf',
                   'historique': [{'du': '2026-01-01', 'au': '2026-01-31', 'description': 'Épicerie'}],
                   'manques': {'trous': [{'du': '2026-02-01', 'au': '2026-02-28', 'solde_fin_cents': 7}]}}
        with patch.object(mcp_finances, 'run', lambda action, argument=None: planted):
            found = self.ok('points_japon')
        self.assertEqual(found['solde'], {'points': 5, 'au': '2026-01-31'})
        self.assertEqual(found['historique'], [{'du': '2026-01-01', 'au': '2026-01-31'}])
        self.assertEqual(found['manques'], {'trous': [{'du': '2026-02-01', 'au': '2026-02-28'}]})
        self.assertNotIn('fichier', found)

    def test_never_opens_a_window(self):
        fill(self.ledger, monthly('2026-01', [500]))
        with FausseFenetre(reponse=False) as fenetre:
            self.ok('points_japon', {'historique': True})
        self.assertEqual(fenetre.nombre, 0)


class TestObjectifPreparer(Base):
    FUTURE = (date.today() + timedelta(days=400)).isoformat()

    def test_refused_at_once(self):
        for arguments, fragment in (({'cible': 0}, 'cible'), ({'cible': -5}, 'cible'), ({'cible': 1.5}, 'cible'),
                                    ({'cible': '100'}, 'cible'), ({'cible': True}, 'cible'), ({'cible': 10_000_001}, 'cible'),
                                    ({'cible': 5, 'date_visee': '2028-5-1'}, 'date_visee'),
                                    ({'cible': 5, 'date_visee': '20280501'}, 'date_visee'),
                                    ({'cible': 5, 'date_visee': 20280501}, 'date_visee'),
                                    ({'cible': 5, 'date_visee': date.today().isoformat()}, "aujourd'hui"),
                                    ({'cible': 5, 'date_visee': (date.today() - timedelta(days=1)).isoformat()}, "aujourd'hui")):
            self.fails('objectif_points_preparer', arguments, fragment)
        self.fails('objectif_points_retirer_preparer', {}, 'Aucun objectif en vigueur')
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_summary_and_window_line(self):
        fill(self.ledger, [], [{'cible': 100_000, 'cree_le': '2026-11-02 10:00:00'}])
        added = self.ok('objectif_points_preparer', {'cible': 120_000, 'date_visee': self.FUTURE})
        self.assertEqual(added['objectif'], f'120 000 points, visé le {self.FUTURE}')
        self.assertEqual(added['remplace'], '100 000 points (depuis 2026-11-02)')
        self.assertEqual(added['effet'], "Remplace l'objectif en vigueur; l'ancien reste dans l'historique")
        # The window line is the lot's own, from the title and the summary, as in the contract.
        self.assertEqual(self.lot.window(), [
            f"1. Objectif Japon — objectif : 120 000 points, visé le {self.FUTURE}, remplace : 100 000 points (depuis 2026-11-02), "
            "effet : Remplace l'objectif en vigueur; l'ancien reste dans l'historique"])
        self.ok('finances_lot_preparer')
        self.ok('finances_lot_vider')
        self.assertEqual(self.ok('objectif_points_preparer', {'cible': 90_000})['objectif'], '90 000 points, sans date visée')

    def test_fix_without_goal_and_same_goal(self):
        added = self.ok('objectif_points_preparer', {'cible': 120_000})
        self.assertEqual((added['objectif'], added['effet']), ('120 000 points, sans date visée', "Fixe l'objectif"))
        self.assertNotIn('remplace', added)
        self.ok('finances_lot_vider')
        fill(self.ledger, [], [{'cible': 120_000}])
        same = self.ok('objectif_points_preparer', {'cible': 120_000})
        self.assertFalse(same['ajoute'])
        self.assertIn('déjà en place', same['message'])
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_removal(self):
        fill(self.ledger, [], [{'cible': 120_000, 'date_visee': '2028-05-01', 'cree_le': '2026-11-02 10:00:00'}])
        added = self.ok('objectif_points_retirer_preparer')
        self.assertEqual(added['retire'], '120 000 points, visé le 2028-05-01 (depuis 2026-11-02)')
        self.assertEqual(added['effet'], "Plus d'objectif en vigueur; il reste dans l'historique.")
        self.assertTrue(self.lot.window()[0].startswith("1. Retirer l'objectif Japon — retire : 120 000 points, visé le 2028-05-01"))

    def test_goal_can_change_despite_overlapping_statements(self):
        rows = monthly('2026-01', [500, 600], start=1_000)
        fill(self.ledger, rows + [{**rows[1], 'du': '2026-02-15', 'au': '2026-03-14', 'carte': 2}], [{'cible': 100_000}])
        self.assertEqual(self.ok('objectif_points_preparer', {'cible': 120_000})['remplace'][:14], '100 000 points')
        self.assertTrue(self.ok('objectif_points_retirer_preparer')['ajoute'])

    def test_a_removal_never_moves_to_another_goal(self):
        fill(self.ledger, [], [{'cible': 100_000}])
        self.ok('objectif_points_preparer', {'cible': 120_000})
        self.ok('objectif_points_retirer_preparer')  # removes the 120 000 of step 1
        self.ok('finances_lot_retirer', {'numero': 1})
        # Prepared again without step 1, the removal would now aim at the 100 000: dropped, never retargeted.
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_steps_are_rehearsed_on_the_private_copy(self):
        self.ok('objectif_points_preparer', {'cible': 100_000})
        # Checked against the first step of the lot, not against the empty base.
        second = self.ok('objectif_points_preparer', {'cible': 120_000})
        self.assertEqual(second['remplace'][:14], '100 000 points')
        self.assertTrue(self.ok('objectif_points_retirer_preparer')['ajoute'])
        self.assertEqual(self.rows('SELECT count(*) FROM objectifs_points'), [(0,)])


class TestBoutEnBout(Base):
    def test_goal_through_the_lot_with_the_fake_mac(self):
        fill(self.ledger, TestAvancement.SIX())
        windows = []

        def mac(answer):
            def ask(tool, lines):
                windows.append((tool, lines))
                return answer
            return ask
        with patch.object(confirmation, 'AUTOMATIC', False), \
                patch.object(confirmation, 'ask', mac(False)):
            self.assertNotIn('objectif', self.ok('points_japon'))
            self.ok('objectif_points_preparer', {'cible': 100_000})
            code = self.ok('finances_lot_preparer')['code']
            self.assertEqual(self.rows('SELECT count(*) FROM objectifs_points'), [(0,)])  # no yes, nothing written
            self.fails('WR_SAVE_FINANCES_BD', {'code': code}, 'Non confirmé')
            self.assertEqual(self.rows('SELECT count(*) FROM objectifs_points'), [(0,)])
        with patch.object(confirmation, 'AUTOMATIC', False), \
                patch.object(confirmation, 'ask', mac(True)):
            self.ok('WR_SAVE_FINANCES_BD', {'code': self.ok('finances_lot_preparer')['code']})
            found = self.ok('points_japon')
        self.assertEqual(found['objectif']['cible'], 100_000)
        self.assertEqual(found['date_estimee'], '2028-12')
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[-1][0], 'WR_SAVE_FINANCES_BD — base Finances')
        self.assertEqual(self.rows("SELECT action, cible_type FROM audit_log WHERE cible_type='objectif_points'"),
                         [('fixer_objectif_points', 'objectif_points')])


if __name__ == '__main__':
    unittest.main()
