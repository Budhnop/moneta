"""Dépenses familiales (spec 004) sur bases fictives : migration 0007, budget par le lot Finances, état du budget.
Les valeurs attendues sont calculées à la main ici, jamais par le code testé (SC-004). Aucun nom réel : la conjointe d'Ugo
est le « titulaire additionnel » et ses cartes sont fictives (FR-005)."""
from calendar import monthrange
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from socle import confirmation
from outils_tests import FOLDERS, migrate
from moneta.outils.serveur import McpServer
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
from moneta.finances import budget
from outils_tests import SourceStore, seed_fictive, seed_fictive_finances

confirmation.AUTOMATIC = True  # fictitious data only: no dialog on the Mac

ROOT = Path(__file__).resolve().parents[1]
DOJO, PERSO = 1, 2  # comptes Dojo et Perso (migrations Finances 0001 et 0003)
# Fictitious bank accounts: a Perso card, a Perso chequing account, a Dojo chequing account.
ACCOUNTS = {'carte': ('…1001', PERSO, 'carte'), 'cheques': ('…2002', PERSO, 'banque'), 'dojo': ('…3003', DOJO, 'banque')}


def base_budget(ledger, operations=(), budgets=(), releves=()):
    """The aide de test of T002, on a base migrated to 0007. operations: (date, montant_cents, classe, categorie[, compte]),
    classe 'perso' (categorie: code perso), 'depense_dojo' (categorie: code T2125) or None; compte a key of ACCOUNTS, 'carte'
    by default. budgets: (categorie, debut, montant_cents or None[, remplace_le]), set in SQL. releves: (compte, du, au), each
    with an import and concordant dollar balances (or its own: compte, du, au, debut, fin), for the months the chain covers."""
    with closing(sqlite3.connect(ledger)) as con, con:
        ids = {}
        for n, (key, (preview, account, kind)) in enumerate(ACCOUNTS.items(), 1):
            ids[key] = con.execute('INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (?, ?, ?, ?)',
                                   (account, str(n) * 64, preview, kind)).lastrowid
        batch = con.execute("INSERT INTO imports(fichier, empreinte, format, lignes, nouvelles) VALUES ('ops.ofx', ?, 'ofx', 0, 0)",
                            ('f' * 64,)).lastrowid
        for n, operation in enumerate(operations, 1):
            day, cents, kind, code = operation[:4]
            account = ids[operation[4] if len(operation) > 4 else 'carte']
            number = con.execute('INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) '
                                 "VALUES (?, ?, ?, ?, ?, 'Opération fictive')", (account, batch, f'{n:064d}', day, cents)).lastrowid
            if kind == 'perso':
                con.execute("UPDATE operations SET classe='perso', categorie_perso_id=(SELECT id FROM categories_perso WHERE code=?) "
                            'WHERE id=?', (code, number))
            elif kind == 'depense_dojo':
                expense = con.execute('INSERT INTO depenses(compte_id, date, montant_cents, categorie_id, pct_deductible) '
                                      'SELECT ?, ?, ?, id, 100 FROM categories WHERE code=?', (PERSO, day, -cents, code)).lastrowid
                con.execute("UPDATE operations SET classe='depense_dojo', depense_id=? WHERE id=?", (expense, number))
        for budget in budgets:
            code, start, cents = budget[:3]
            con.execute('INSERT INTO budgets(categorie_perso_id, debut, montant_cents, remplace_le) '
                        'SELECT id, ?, ?, ? FROM categories_perso WHERE code=?', (start, cents, budget[3] if len(budget) > 3 else None, code))
        for n, (key, du, au, *balances) in enumerate(releves, 1):
            number = con.execute("INSERT INTO imports(fichier, empreinte, format, source, du, au, lignes, nouvelles) "
                                 "VALUES (?, ?, 'pdf', 'cobalt', ?, ?, 0, 0)", (f'releve-{n}.pdf', f'{n:064d}', du, au)).lastrowid
            con.execute('INSERT INTO soldes_releves(import_id, compte_bancaire_id, du, au, solde_debut_cents, solde_fin_cents) '
                        'VALUES (?, ?, ?, ?, ?, ?)', (number, ids[key], du, au, *(balances or (-100, -100))))
        return ids


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
        return reply['content'][0]['text']

    def rows(self, sql, *params):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute(sql, params).fetchall()


class TestAide(Base):
    def test_the_helper_builds_a_readable_base(self):
        base_budget(self.ledger, [('2026-03-04', -5_000, 'perso', 'epicerie'), ('2026-03-05', -2_000, 'depense_dojo', '8811'),
                                  ('2026-03-06', -700, None, None, 'cheques')],
                    [('epicerie', '2026-01', 90_000)], [('carte', '2026-03-01', '2026-03-31')])
        self.assertEqual(self.rows('SELECT classe, count(*) FROM operations GROUP BY classe ORDER BY classe'),
                         [(None, 1), ('depense_dojo', 1), ('perso', 1)])
        self.assertEqual(self.rows('SELECT debut, montant_cents FROM budgets'), [('2026-01', 90_000)])
        self.assertEqual(self.rows('SELECT du, au FROM soldes_releves'), [('2026-03-01', '2026-03-31')])


class TestMigration0007(unittest.TestCase):
    """A base stopped at 0006 with a Cobalt statement and operations gets 0007: two tables and a column, nothing rebuilt."""
    def test_upgrade_and_constraints(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            old = folder / 'migrations'
            old.mkdir()
            for path in sorted(FOLDERS['finances'].glob('000[1-6]_*.py')):
                (old / path.name).write_bytes(path.read_bytes())
            ledger = folder / 'database' / 'finances.db'
            migrate(ledger, folder / 'backups', old)
            with closing(sqlite3.connect(ledger)) as con, con:
                con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (2, ?, '…1001', 'carte')", ('a' * 64,))
                con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (1, ?, '…3003', 'banque')", ('d' * 64,))
                con.execute("INSERT INTO imports(fichier, empreinte, format, source, du, au, lignes, nouvelles) "
                            "VALUES ('c.pdf', ?, 'pdf', 'cobalt', '2026-01-01', '2026-01-31', 2, 2)", ('b' * 64,))
                con.execute("INSERT INTO soldes_releves(import_id, compte_bancaire_id, du, au, solde_debut_cents, solde_fin_cents) "
                            "VALUES (1, 1, '2026-01-01', '2026-01-31', -100, -600)")
                for n, cents in enumerate((-200, -300), 1):
                    con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) "
                                "VALUES (1, 1, ?, '2026-01-05', ?, 'Achat fictif')", (str(n) * 64, cents))
                pages = dict(con.execute("SELECT name, rootpage FROM sqlite_master WHERE type='table'").fetchall())
            env = {**os.environ, 'MONETA_HOME': str(folder)}
            env.pop('MONETA_FINANCES_DB', None)
            status = subprocess.run([str(ROOT / 'bin' / 'moneta-migrer'), '--base', 'finances'], env=env, capture_output=True, text=True).stdout
            self.assertIn('En attente : 0007 depenses_familiales', status)
            done, backup = migrate(ledger, folder / 'backups', FOLDERS['finances'])
            # 0008 (F2, D091) follows; it has its own test in test_banque.py.
            self.assertEqual([(v, e) for v, _, e in done], [(7, 'appliquée'), (8, 'appliquée')])
            self.assertTrue(backup.is_file())
            module = __import__('importlib').import_module('moneta.migrations.finances.0007_depenses_familiales')
            with closing(sqlite3.connect(ledger)) as con:
                con.execute('PRAGMA foreign_keys=ON')
                self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertTrue(module.deja_appliquee(con))
                after = dict(con.execute("SELECT name, rootpage FROM sqlite_master WHERE type='table'").fetchall())
                # No table rebuilt: every table of 0006 keeps its pages; two new ones.
                self.assertEqual({k: after[k] for k in pages}, pages)
                self.assertEqual(set(after) - set(pages), {'cartes_additionnelles', 'budgets'})
                self.assertEqual(con.execute('SELECT montant_cents, carte_additionnelle_id FROM operations ORDER BY id').fetchall(),
                                 [(-200, None), (-300, None)])
                self.assertEqual(con.execute('SELECT count(*) FROM soldes_releves').fetchone()[0], 1)
                for index in ('idx_carte_additionnelle_en_vigueur', 'idx_budget_en_vigueur'):
                    self.assertIsNotNone(con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index,)).fetchone())
                with con:
                    con.execute("INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, ?, '…4004')", ('c' * 64,))
                    con.execute("UPDATE operations SET carte_additionnelle_id=1 WHERE id=2")
                    con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2026-01', 90000)")
                    con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2026-06', NULL)")  # a removal
                    con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents, remplace_le) "
                                "VALUES (2, '2026-01', 80000, '2026-02-01 10:00:00')")  # a replaced line
                for sql in ("INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, 'court', '…4005')",
                            "INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, '" + 'c' * 64 + "', '…4005')",
                            "INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu, remplacee_le) "
                            "VALUES (1, '" + 'e' * 64 + "', '…4004', '2026-02-01')",
                            # A second card in force on the same account.
                            "INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, '" + 'e' * 64 + "', '…4005')",
                            "INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (99, '" + 'e' * 64 + "', '…4006')",
                            "UPDATE operations SET carte_additionnelle_id=99 WHERE id=1",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-1', 100)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-00', 100)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-13', 100)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, 'demain', 100)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-02', 0)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-02', -500)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-02', 10000001)",
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (99, '2026-02', 100)",
                            # Two lines not replaced for the same category and month of start.
                            "INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2026-01', 95000)"):
                    with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                        con.execute(sql)
                con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (3, '2026-02', 10000000)")
            self.assertEqual(migrate(ledger, folder / 'backups', FOLDERS['finances']), ([], None))


def covered(first, last, keys=('carte', 'cheques')):
    """Calendar-month statements of the Perso accounts from first to last (AAAA-MM), balances following each other."""
    out, (year, number) = [], map(int, first.split('-'))
    while f'{year:04d}-{number:02d}' <= last:
        end = monthrange(year, number)[1]
        out += [(key, f'{year:04d}-{number:02d}-01', f'{year:04d}-{number:02d}-{end:02d}') for key in keys]
        year, number = year + number // 12, number % 12 + 1
    return out


class TestEtat(Base):
    """finances/budget.py called directly, with the Mac's date given; every expected figure is worked out by hand."""
    NOV = date(2026, 11, 10)  # November has 30 days: 10 / 30 of a month

    def state(self, today, year, wanted=None, historique=False):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            return budget.state(con, today, year, wanted, historique=historique)

    def line(self, found, code):
        return next(c for c in found['categories'] if c['categorie'] == code)

    def test_a_single_month(self):
        base_budget(self.ledger, [('2026-10-03', -45_000, 'perso', 'epicerie'), ('2026-10-17', -30_012, 'perso', 'epicerie'),
                                  ('2026-09-30', -10_000, 'perso', 'epicerie'), ('2026-10-05', -27_550, 'perso', 'restaurants', 'cheques'),
                                  ('2026-10-09', -4_000, 'perso', 'loisirs')],
                    [('epicerie', '2026-01', 90_000), ('restaurants', '2026-01', 25_000), ('sante', '2026-01', 5_000)],
                    covered('2026-01', '2026-10'))
        found = self.state(self.NOV, 2026, 10)
        self.assertEqual(found['periode'], {'annee': 2026, 'mois': 10})
        # 450,00 + 300,12 = 750,12 of 900,00: 149,88 left, 83,35 % shown 83.
        self.assertEqual(self.line(found, 'epicerie'), {'categorie': 'epicerie', 'nom': 'Épicerie', 'depense': 750.12, 'budget': 900.0,
                                                        'ecart': 149.88, 'pourcentage': 83, 'depassee': False})
        # 275,50 of 250,00: 25,50 over, 110,2 % shown 110.
        self.assertEqual(self.line(found, 'restaurants'), {'categorie': 'restaurants', 'nom': 'Restaurants', 'depense': 275.5,
                                                           'budget': 250.0, 'ecart': -25.5, 'pourcentage': 110, 'depassee': True})
        # A budgeted category without any spending this month.
        self.assertEqual(self.line(found, 'sante'), {'categorie': 'sante', 'nom': 'Santé', 'depense': 0.0, 'budget': 50.0, 'ecart': 50.0,
                                                     'pourcentage': 0, 'depassee': False})
        # 1 025,62 of 1 200,00: 174,38 left; 85,47 % shown 85.
        self.assertEqual(found['total'], {'depense': 1025.62, 'budget': 1200.0, 'ecart': 174.38, 'pourcentage': 85})
        self.assertEqual(found['depassees'], ['restaurants'])
        self.assertEqual(found['sans_budget'], [{'categorie': 'loisirs', 'nom': 'Loisirs', 'depense': 40.0}])
        self.assertEqual(found['avertissements'], {})
        self.assertTrue(found['complet'])
        self.assertTrue(found['notes'])

    def test_half_up_percent_and_a_credit(self):
        base_budget(self.ledger, [('2026-10-03', -5_000, 'perso', 'restaurants'), ('2026-10-04', 7_000, 'perso', 'restaurants'),
                                  ('2026-10-05', -16_250, 'perso', 'epicerie')],
                    [('restaurants', '2026-01', 25_000), ('epicerie', '2026-01', 50_000)], covered('2026-01', '2026-10'))
        found = self.state(self.NOV, 2026, 10)
        # A refund lowers the spending, even below zero: −20,00 shown as is, −8 %.
        self.assertEqual((self.line(found, 'restaurants')['depense'], self.line(found, 'restaurants')['ecart'],
                          self.line(found, 'restaurants')['pourcentage']), (-20.0, 270.0, -8))
        # 162,50 / 500,00 = 32,5 %: half up, 33.
        self.assertEqual(self.line(found, 'epicerie')['pourcentage'], 33)

    def test_to_sort_is_told_never_spread(self):
        base_budget(self.ledger, [('2026-10-03', -1_000, None, None), ('2026-10-04', -2_500, None, None, 'cheques'),
                                  ('2026-10-05', 500, None, None), ('2026-09-05', -9_900, None, None),
                                  ('2026-10-06', -7_700, None, None, 'dojo'), ('2026-10-07', -4_000, 'perso', 'epicerie')],
                    [('epicerie', '2026-01', 90_000)], covered('2026-01', '2026-10'))
        found = self.state(self.NOV, 2026, 10)
        # Three Perso lines of October: 10,00 + 25,00 − 5,00 = 30,00 net; never in a category.
        self.assertEqual(found['avertissements'], {'a_classer': {'nombre': 3, 'montant': 30.0}})
        self.assertEqual(self.line(found, 'epicerie')['depense'], 40.0)
        self.assertFalse(found['complet'])

    def test_a_month_without_its_statement_is_incomplete(self):
        rows = [r for r in covered('2026-01', '2026-10') if not (r[0] == 'carte' and r[1] == '2026-10-01')]
        # A hole in the middle of October; September ends a day short (its last day not covered).
        rows = [r if r[1] != '2026-09-01' or r[0] != 'carte' else ('carte', '2026-09-01', '2026-09-29') for r in rows]
        rows += [('carte', '2026-10-01', '2026-10-14'), ('carte', '2026-10-20', '2026-10-31')]
        base_budget(self.ledger, [('2026-10-03', -1_000, 'perso', 'epicerie')], [('epicerie', '2026-01', 90_000)], rows)
        self.assertEqual(self.state(self.NOV, 2026, 10)['avertissements']['incomplet'],
                         [{'apercu': '…1001', 'mois': '2026-10', 'raison': 'relevé manquant'}])
        found = self.state(self.NOV, 2026, 9)
        self.assertEqual(found['avertissements']['incomplet'], [{'apercu': '…1001', 'mois': '2026-09', 'raison': 'relevé manquant'}])
        self.assertFalse(found['complet'])

    def test_balances_that_do_not_follow_mark_both_months(self):
        rows = [r for r in covered('2026-01', '2026-10') if not (r[0] == 'carte' and r[1] in ('2026-09-01', '2026-10-01'))]
        rows += [('carte', '2026-09-01', '2026-09-30', -100, -500), ('carte', '2026-10-01', '2026-10-31', -600, -100)]
        base_budget(self.ledger, [], [('epicerie', '2026-01', 90_000)], rows)
        broken = [{'apercu': '…1001', 'mois': m, 'raison': 'soldes qui ne se suivent pas'} for m in ('2026-09', '2026-10')]
        self.assertEqual(self.state(self.NOV, 2026, 10)['avertissements']['incomplet'], broken[1:])
        self.assertEqual(self.state(date(2026, 11, 1), 2026)['avertissements']['incomplet'], broken)

    def test_a_late_statement(self):
        rows = [r for r in covered('2026-01', '2026-10') if not (r[0] == 'carte' and r[1] >= '2026-09-01')]
        base_budget(self.ledger, [], [('epicerie', '2026-01', 90_000)], rows)
        # Nothing since September 1st, more than 35 days ago: September and October are late.
        self.assertEqual(self.state(self.NOV, 2026, 10)['avertissements']['incomplet'],
                         [{'apercu': '…1001', 'mois': '2026-10', 'raison': 'en retard'}])
        self.assertEqual([i['mois'] for i in self.state(self.NOV, 2026)['avertissements']['incomplet']], ['2026-09', '2026-10', '2026-11'])

    def test_year_to_date_adds_the_budget_of_each_month(self):
        spent = [(f'2026-{m:02d}-15', -95_000, 'perso', 'epicerie') for m in range(1, 13)]
        base_budget(self.ledger, spent, [('epicerie', '2026-01', 90_000), ('epicerie', '2026-07', 100_000)], covered('2026-01', '2026-12'))
        found = self.state(date(2027, 2, 10), 2026)
        self.assertEqual(found['periode'], {'annee': 2026, 'du': '2026-01', 'au': '2026-12'})
        # 6 × 900 + 6 × 1 000 = 11 400 of budget; 12 × 950 = 11 400 spent.
        self.assertEqual(self.line(found, 'epicerie'), {'categorie': 'epicerie', 'nom': 'Épicerie', 'depense': 11_400.0,
                                                        'budget': 11_400.0, 'ecart': 0.0, 'pourcentage': 100, 'depassee': False})
        self.assertTrue(found['complet'])

    def test_a_budget_from_june_counts_june_onward(self):
        base_budget(self.ledger, [('2026-03-10', -10_000, 'perso', 'restaurants'), ('2026-07-10', -20_000, 'perso', 'restaurants')],
                    [('restaurants', '2026-06', 25_000)], covered('2026-01', '2026-09'))
        found = self.state(date(2026, 10, 15), 2026)
        self.assertEqual(found['periode'], {'annee': 2026, 'du': '2026-01', 'au': '2026-10'})
        # June to October: 5 × 250 = 1 250 of budget, 200 spent; March goes to the spending without a budget.
        self.assertEqual(self.line(found, 'restaurants'), {'categorie': 'restaurants', 'nom': 'Restaurants', 'depense': 200.0,
                                                           'budget': 1250.0, 'ecart': 1050.0, 'pourcentage': 16, 'depassee': False,
                                                           'depuis': '2026-06'})
        self.assertEqual(found['sans_budget'], [{'categorie': 'restaurants', 'nom': 'Restaurants', 'depense': 100.0}])

    def test_a_removed_month_leaves_both_sides(self):
        base_budget(self.ledger, [('2026-03-10', -30_000, 'perso', 'epicerie'), ('2026-05-10', -60_000, 'perso', 'epicerie')],
                    [('epicerie', '2026-01', 90_000), ('epicerie', '2026-03', None), ('epicerie', '2026-05', 90_000)],
                    covered('2026-01', '2026-12'))
        found = self.state(date(2027, 2, 10), 2026)
        months = ['2026-01', '2026-02'] + [f'2026-{m:02d}' for m in range(5, 13)]
        # Ten months of 900; March and April out of both sides.
        self.assertEqual(self.line(found, 'epicerie'), {'categorie': 'epicerie', 'nom': 'Épicerie', 'depense': 600.0, 'budget': 9000.0,
                                                        'ecart': 8400.0, 'pourcentage': 7, 'depassee': False, 'mois_comptes': months})
        self.assertEqual(found['sans_budget'], [{'categorie': 'epicerie', 'nom': 'Épicerie', 'depense': 300.0}])

    def test_a_statement_across_two_months_counts_each_line_in_its_own_month(self):
        rows = [r for r in covered('2026-01', '2026-09') if r[0] != 'carte' or r[1] < '2026-09-01']
        rows += [('carte', '2026-09-01', '2026-10-07'), ('carte', '2026-10-08', '2026-11-07'), ('cheques', '2026-10-01', '2026-10-31')]
        base_budget(self.ledger, [('2026-10-28', -4_000, 'perso', 'epicerie'), ('2026-11-03', -6_000, 'perso', 'epicerie')],
                    [('epicerie', '2026-01', 90_000)], rows)
        self.assertEqual(self.line(self.state(self.NOV, 2026, 10), 'epicerie')['depense'], 40.0)
        self.assertEqual(self.line(self.state(self.NOV, 2026, 11), 'epicerie')['depense'], 60.0)

    def test_only_perso_spending_of_both_cards(self):
        base_budget(self.ledger, [('2026-10-01', -1_000, 'perso', 'epicerie'), ('2026-10-02', -2_000, 'perso', 'epicerie'),
                                  ('2026-10-03', -5_000, 'depense_dojo', '8811'), ('2026-10-04', 500_000, 'perso', 'revenus', 'cheques'),
                                  ('2026-10-05', -20_000, 'perso', 'virement_interne', 'cheques'),
                                  ('2026-10-06', 32_000, 'perso', 'depot_dojo', 'dojo'), ('2026-10-07', -7_000, 'perso', 'epicerie', 'dojo')],
                    [('epicerie', '2026-01', 90_000)], covered('2026-01', '2026-10'))
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("INSERT INTO cartes_additionnelles(compte_bancaire_id, empreinte, apercu) VALUES (1, ?, '…4004')", ('c' * 64,))
            con.execute("UPDATE operations SET carte_additionnelle_id=1 WHERE date IN ('2026-10-02', '2026-10-03')")
        found = self.state(self.NOV, 2026, 10)
        # 10,00 + 20,00 of both holders; the dojo expense (even on the additional card), income, transfers and the Dojo out.
        self.assertEqual(self.line(found, 'epicerie')['depense'], 30.0)
        self.assertEqual(found['sans_budget'], [])

    def test_the_month_in_progress(self):
        base_budget(self.ledger, [('2026-11-05', -10_000, 'perso', 'restaurants')],
                    [('epicerie', '2026-01', 90_000), ('restaurants', '2026-01', 25_000)], covered('2026-01', '2026-10'))
        found = self.state(self.NOV, 2026, 11)
        # 900 × 10/30 = 300,00; 250 × 10/30 = 83,33; 1 150 × 10/30 = 383,33.
        self.assertEqual(found['avertissements'], {'mois_en_cours': {
            'jours_ecoules': 10, 'jours': 30, 'budget_au_prorata': 383.33,
            'par_categorie': [{'categorie': 'epicerie', 'budget_au_prorata': 300.0}, {'categorie': 'restaurants', 'budget_au_prorata': 83.33}]}})
        # 100 spent: above the pro rata, not above the whole budget.
        self.assertEqual(found['depassees'], [])
        self.assertFalse(found['complet'])

    def test_year_to_date_of_this_year_counts_this_month_whole(self):
        spent = [(f'2026-{m:02d}-02', -91_000, 'perso', 'epicerie') for m in range(1, 11)]
        spent += [(f'2026-{m:02d}-02', -24_000, 'perso', 'restaurants') for m in range(1, 10)] + [('2026-10-02', -24_000, 'perso', 'restaurants')]
        base_budget(self.ledger, spent, [('epicerie', '2026-01', 90_000), ('restaurants', '2026-01', 25_000)], covered('2026-01', '2026-09'))
        found = self.state(date(2026, 10, 10), 2026)
        self.assertEqual(found['periode'], {'annee': 2026, 'du': '2026-01', 'au': '2026-10'})
        # October counts its whole budget: 10 × 900 = 9 000 against 9 100 spent; 10 × 250 = 2 500 against 2 400.
        self.assertEqual((self.line(found, 'epicerie')['budget'], self.line(found, 'epicerie')['depense']), (9000.0, 9100.0))
        self.assertEqual(found['depassees'], ['epicerie'])
        # Restaurants: 240 in October is above 250 × 10/31 = 80,65, yet not over the year's budget.
        self.assertEqual(found['avertissements']['mois_en_cours']['par_categorie'][1], {'categorie': 'restaurants', 'budget_au_prorata': 80.65})
        # 1 150 × 10/31 = 370,97.
        self.assertEqual(found['avertissements']['mois_en_cours']['budget_au_prorata'], 370.97)
        self.assertFalse(found['complet'])

    def test_no_budget(self):
        base_budget(self.ledger, [('2026-10-03', -1_000, 'perso', 'epicerie'), ('2026-10-04', -500, None, None)], [],
                    covered('2026-01', '2026-10'))
        found = self.state(self.NOV, 2026, 10)
        self.assertEqual(found['message'], 'Aucun budget fixé.')
        self.assertEqual(found['sans_budget'], [{'categorie': 'epicerie', 'nom': 'Épicerie', 'depense': 10.0}])
        self.assertEqual(found['avertissements'], {'a_classer': {'nombre': 1, 'montant': 5.0}})
        self.assertNotIn('categories', found)

    def test_history_of_the_year_only(self):
        base_budget(self.ledger, [], [('epicerie', '2026-03', 80_000), ('epicerie', '2026-06', 90_000),
                                      ('epicerie', '2027-04', 95_000, '2027-04-02 10:00:00'), ('epicerie', '2027-04', 100_000),
                                      ('epicerie', '2028-01', 110_000), ('restaurants', '2027-02', 25_000), ('restaurants', '2027-05', None)],
                    covered('2026-01', '2027-11'))
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("UPDATE budgets SET cree_le='2027-04-01 09:00:00'")
        found = self.state(date(2027, 12, 15), 2027, historique=True)
        self.assertEqual(found['historique'], [
            {'categorie': 'epicerie', 'debut': '2026-06', 'montant': 900.0, 'action': 'fixe', 'cree_le': '2027-04-01'},
            {'categorie': 'epicerie', 'debut': '2027-04', 'montant': 950.0, 'action': 'fixe', 'cree_le': '2027-04-01',
             'remplace_le': '2027-04-02'},
            {'categorie': 'epicerie', 'debut': '2027-04', 'montant': 1000.0, 'action': 'fixe', 'cree_le': '2027-04-01'},
            {'categorie': 'restaurants', 'debut': '2027-02', 'montant': 250.0, 'action': 'fixe', 'cree_le': '2027-04-01'},
            {'categorie': 'restaurants', 'debut': '2027-05', 'montant': None, 'action': 'retrait', 'cree_le': '2027-04-01'}])
        # 3 × 900 + 9 × 1 000 for 2027: the line of 2028 does not act on 2027.
        self.assertEqual(self.line(found, 'epicerie')['budget'], 11_700.0)
        self.assertNotIn('historique', self.state(date(2027, 12, 15), 2027))

    def test_history_leaves_out_a_line_replaced_from_january(self):
        base_budget(self.ledger, [], [('epicerie', '2026-06', 90_000), ('epicerie', '2027-01', 100_000)], covered('2026-01', '2027-11'))
        found = self.state(date(2027, 12, 15), 2027, historique=True)
        self.assertEqual([h['debut'] for h in found['historique']], ['2027-01'])

    def test_an_inactive_category_is_never_dropped(self):
        base_budget(self.ledger, [('2026-10-03', -1_000, 'perso', 'cadeaux'), ('2026-10-04', -2_000, 'perso', 'loisirs')],
                    [('loisirs', '2026-01', 5_000)], covered('2026-01', '2026-10'))
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("UPDATE categories_perso SET actif=0 WHERE code IN ('cadeaux', 'loisirs')")
        found = self.state(self.NOV, 2026, 10)
        self.assertEqual(self.line(found, 'loisirs')['depense'], 20.0)
        self.assertEqual(found['sans_budget'], [{'categorie': 'cadeaux', 'nom': 'Cadeaux et dons', 'depense': 10.0}])

    def test_no_perso_statement_is_said_and_every_reason_named(self):
        # Essai dans Claude Desktop (2026-10-24): an empty Perso account gave no warning, and complet false gave no reason.
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("INSERT INTO budgets(categorie_perso_id, debut, montant_cents) VALUES (2, '2026-01', 90000)")
        found = self.state(self.NOV, 2026, 10)
        self.assertEqual(found['avertissements'], {'aucun_releve': budget.NO_STATEMENT})
        self.assertEqual(found['raisons_incomplet'], ['aucun relevé du compte Perso'])
        self.assertFalse(found['complet'])
        found = self.state(self.NOV, 2026)
        self.assertEqual(found['raisons_incomplet'], ['aucun relevé du compte Perso', 'mois en cours'])
        self.tearDown(); self.setUp()
        base_budget(self.ledger, [('2026-10-03', -1_000, None, None)], [('epicerie', '2026-01', 90_000)],
                    [r for r in covered('2026-01', '2026-10') if r != ('carte', '2026-10-01', '2026-10-31')])
        found = self.state(self.NOV, 2026)
        self.assertEqual(found['raisons_incomplet'], ['opérations à classer', 'relevés manquants, en retard ou soldes qui ne se suivent '
                                                      'pas', 'mois en cours'])
        self.tearDown(); self.setUp()
        base_budget(self.ledger, [], [('epicerie', '2026-01', 90_000)], covered('2026-01', '2026-10'))
        found = self.state(self.NOV, 2026, 10)
        self.assertTrue(found['complet'])
        self.assertNotIn('raisons_incomplet', found)

    def test_always_computed_again(self):
        base_budget(self.ledger, [('2026-10-03', -1_000, 'perso', 'epicerie')], [('epicerie', '2026-01', 90_000)], covered('2026-01', '2026-10'))
        self.assertEqual(self.line(self.state(self.NOV, 2026, 10), 'epicerie')['depense'], 10.0)
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description, classe, categorie_perso_id) "
                        "SELECT 1, 1, ?, '2026-10-20', -2000, 'Plus tard', 'perso', id FROM categories_perso WHERE code='epicerie'", ('z' * 64,))
        self.assertEqual(self.line(self.state(self.NOV, 2026, 10), 'epicerie')['depense'], 30.0)


def month(shift=0, today=None):
    """AAAA-MM, shift months after the current month of the Mac."""
    today = today or date.today()
    n = today.year * 12 + today.month - 1 + shift
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


class Mac:
    """The fake Mac: records its windows (Enregistrer only: a read opens none, spec 002, R11) and answers as told."""
    def __init__(self, ask=True):
        self.asks, self.ask_answer = [], ask

    def ask(self, title, lines):
        self.asks.append((title, lines))
        return self.ask_answer

    def __enter__(self):
        self.patches = [patch.object(confirmation, 'AUTOMATIC', False), patch.object(confirmation, 'ask', self.ask)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class TestBudgetPreparer(Base):
    def test_refused_at_once(self):
        for arguments, fragment in (({'categorie': 'epicerie', 'montant': 0}, 'Montant'), ({'categorie': 'epicerie', 'montant': -5}, 'Montant'),
                                    ({'categorie': 'epicerie', 'montant': 1.005}, 'Montant'),
                                    ({'categorie': 'epicerie', 'montant': 100_000.01}, 'Montant'),
                                    ({'categorie': 'epicerie', 'montant': '900'}, 'Montant'),
                                    ({'categorie': 'revenus', 'montant': 900}, 'categorie'),
                                    ({'categorie': 'virement_interne', 'montant': 900}, 'categorie'),
                                    ({'categorie': 'depot_dojo', 'montant': 900}, 'categorie'),
                                    ({'categorie': 'epicerie', 'montant': 900, 'debut': '2025-12'}, 'debut'),
                                    ({'categorie': 'epicerie', 'montant': 900, 'debut': '2026-1'}, 'debut'),
                                    ({'categorie': 'epicerie', 'montant': 900, 'debut': '2026-13'}, 'debut'),
                                    ({'categorie': 'epicerie', 'montant': 900, 'debut': month(13)}, 'debut')):
            self.fails('budget_preparer', arguments, fragment)
        self.fails('budget_retirer_preparer', {'categorie': 'revenus'}, 'categorie')
        self.fails('budget_retirer_preparer', {'categorie': 'epicerie', 'debut': month(13)}, 'debut')
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_a_first_budget_opens_no_window_and_starts_this_month(self):
        with Mac() as mac:
            added = self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 900})
            self.ok('budget_preparer', {'categorie': 'restaurants', 'montant': 250, 'debut': month(12)})
        self.assertEqual(mac.asks, [])
        self.assertEqual((added['budget'], added['effet']), (f'Épicerie : 900,00 $ par mois dès {month()}', 'Fixe le budget'))
        self.assertNotIn('remplace', added)
        # The window line is the lot's own, from the title and the summary, as in the contract.
        self.assertEqual(self.lot.window(), [f'1. Budget — budget : Épicerie : 900,00 $ par mois dès {month()}, effet : Fixe le budget',
                                             f'2. Budget — budget : Restaurants : 250,00 $ par mois dès {month(12)}, effet : Fixe le budget'])
        self.ok('finances_lot_preparer')
        self.assertEqual(self.rows('SELECT count(*) FROM budgets'), [(0,)])

    def test_a_kept_budget_is_read_without_a_window(self):
        base_budget(self.ledger, budgets=[('epicerie', '2026-06', 80_000), ('epicerie', month(3), 100_000)])
        with Mac() as mac:
            added = self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 900})
        self.assertEqual(added['budget'], f'Épicerie : 900,00 $ par mois dès {month()}')
        self.assertEqual(added['remplace'], '800,00 $ (dès 2026-06)')
        self.assertEqual(added['ensuite'], f'1 000,00 $ dès {month(3)}')
        self.assertEqual(added['effet'], f"Change le budget à partir de {month()}; les mois d'avant gardent l'ancien")
        self.assertEqual(mac.asks, [])
        self.assertEqual(self.lot.window()[0], f'1. Budget — budget : Épicerie : 900,00 $ par mois dès {month()}, remplace : 800,00 $ '
                                               f"(dès 2026-06), ensuite : 1 000,00 $ dès {month(3)}, effet : Change le budget à partir "
                                               f"de {month()}; les mois d'avant gardent l'ancien")

    def test_a_start_in_another_year(self):
        base_budget(self.ledger, budgets=[('epicerie', '2026-06', 80_000)])
        with Mac() as mac:
            self.assertEqual(self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 1000, 'debut': '2027-02'})['remplace'],
                             '800,00 $ (dès 2026-06)')
        self.assertEqual(mac.asks, [])

    def test_the_same_amount_is_already_in_place(self):
        base_budget(self.ledger, budgets=[('epicerie', '2026-06', 90_000)])
        with Mac() as mac:
            same = self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 900})
        self.assertFalse(same['ajoute'])
        self.assertIn('déjà en place', same['message'])
        self.assertEqual(mac.asks, [])
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_removal_is_prepared_without_a_window(self):
        with Mac() as mac:
            self.fails('budget_retirer_preparer', {'categorie': 'epicerie'}, f'Aucun budget en vigueur pour epicerie en {month()}')
        self.assertEqual(mac.asks, [])
        base_budget(self.ledger, budgets=[('epicerie', '2026-06', 80_000)])
        self.ok('finances_lot_vider')  # the lot's private copy was taken before this budget
        with Mac() as mac:
            removed = self.ok('budget_retirer_preparer', {'categorie': 'epicerie'})
        self.assertEqual(mac.asks, [])
        self.assertEqual((removed['retire'], removed['a_partir_de']), ('Épicerie : 800,00 $ (dès 2026-06)', month()))
        self.assertEqual(removed['effet'], f"Plus de budget pour cette catégorie à partir de {month()}; les mois d'avant le gardent.")
        self.assertTrue(self.lot.window()[0].startswith('1. Retirer un budget — retire : Épicerie : 800,00 $ (dès 2026-06)'))

    def test_steps_are_checked_against_the_lot_before_them(self):
        self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 900})
        second = self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 950})
        self.assertEqual(second['remplace'], f'900,00 $ (dès {month()})')
        self.assertTrue(self.ok('budget_retirer_preparer', {'categorie': 'epicerie', 'debut': month(1)})['ajoute'])
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 3)
        self.assertEqual(self.rows('SELECT count(*) FROM budgets'), [(0,)])

    def test_schemas_are_closed(self):
        tools = {t['name']: t for t in self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']}
        schema = tools['budget_preparer']['inputSchema']
        self.assertEqual((schema['additionalProperties'], schema['required']), (False, ['categorie', 'montant']))
        self.assertEqual(schema['properties']['categorie']['enum'],
                         ['logement', 'epicerie', 'restaurants', 'transport', 'sante', 'assurances', 'telecom', 'loisirs',
                          'vetements', 'cadeaux', 'frais_bancaires'])
        self.assertIn('jamais proposé', tools['budget_preparer']['description'])
        self.assertEqual(tools['budget_retirer_preparer']['inputSchema']['required'], ['categorie'])
        self.fails('budget_preparer', {'categorie': 'epicerie', 'montant': 900, 'autre': 1}, 'Arguments attendus')


class TestBudgetBoutEnBout(Base):
    def test_two_budgets_through_the_lot_with_the_fake_mac(self):
        with Mac(ask=False) as mac:
            self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 900})
            self.ok('budget_preparer', {'categorie': 'restaurants', 'montant': 250})
            code = self.ok('finances_lot_preparer')['code']
            self.assertEqual(self.rows('SELECT count(*) FROM budgets'), [(0,)])  # no yes, nothing written
            self.fails('WR_SAVE_FINANCES_BD', {'code': code}, 'Non confirmé')
        self.assertEqual(self.rows('SELECT count(*) FROM budgets'), [(0,)])
        with Mac() as mac:
            self.ok('WR_SAVE_FINANCES_BD', {'code': self.ok('finances_lot_preparer')['code']})
        self.assertEqual([title for title, _ in mac.asks], ['WR_SAVE_FINANCES_BD — base Finances'])
        # Spec 002 (T019): why the window opens first, then a blank line and the server's summary.
        self.assertEqual(mac.asks[0][1][0], 'Pourquoi cette fenêtre :')
        self.assertEqual(mac.asks[0][1][mac.asks[0][1].index('') + 1:], [f'1. Budget — budget : Épicerie : 900,00 $ par mois dès {month()}, effet : Fixe le budget',
                                          f'2. Budget — budget : Restaurants : 250,00 $ par mois dès {month()}, effet : Fixe le budget'])
        self.assertEqual(self.rows('SELECT p.code, b.debut, b.montant_cents FROM budgets b JOIN categories_perso p '
                                   'ON p.id=b.categorie_perso_id ORDER BY b.id'),
                         [('epicerie', month(), 90_000), ('restaurants', month(), 25_000)])
        self.assertEqual(self.rows("SELECT action FROM audit_log WHERE cible_type='budget' ORDER BY id"), [('fixer_budget',), ('fixer_budget',)])


if __name__ == '__main__':
    unittest.main()
