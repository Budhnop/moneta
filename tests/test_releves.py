"""Relevés PDF (spec 003) sur des relevés fictifs : contrôle commun, soldes au cent près, refus sans montant ni description.
Le profil fictif n'est enregistré que par les tests; le vrai registre reste vide jusqu'au premier vrai relevé."""
from cache_swift import compile_ou_cache
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from moneta.finances.banque import RefusBanque, sha
from moneta.finances.releves import controle, extraction
from moneta.finances.releves.profils import REGISTRE, registre
from socle import confirmation
from outils_tests import FausseFenetre
from moneta.outils.serveur import McpServer
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
from releves_fictifs import ADDITIONAL_NAME, COBALT, FOLIO, MARGE, as_extracted, layout, pdf
from outils_tests import SourceStore, seed_fictive, seed_fictive_finances
from test_banque import ofx, statement, transaction

confirmation.AUTOMATIC = True  # fictitious data only: no dialog on the Mac
SOURCE = Path(__file__).resolve().parents[1] / 'moneta' / 'releve' / 'main.swift'

CHEQUING, SAVINGS, CARD, MARGIN = '815-30000-0123456-EOP', '815-30000-0123456-ES1', '3700-000000-09876', '815-30000-0123456-MC1'
JAN = ('2026-01-01', '2026-01-31')


def bank(numero=CHEQUING, debut=100_000, lignes=None, **extra):
    lignes = [('2026-01-05', 'Café Démo CANARI', -450, 'operation'), ('2026-01-15', 'Paie fictive', 250_000, 'operation')] \
        if lignes is None else lignes
    return {'numero': numero, 'type': 'banque', 'debut': debut, 'lignes': lignes, **extra}


def card(debut=-20_000, lignes=None, **extra):
    lignes = [('2026-01-03', 'Épicerie Exemple', -8_250, 'achat'), ('2026-01-10', 'Paiement merci', 20_000, 'paiement'),
              ('2026-01-12', 'Remboursement Démo', 1_500, 'credit')] if lignes is None else lignes
    return {'numero': CARD, 'type': 'carte', 'debut': debut, 'lignes': lignes, **extra}


SECTION, OTHER = '3700-000000-81111', '3700-000000-82222'  # fictitious additional cards (spec 004): …1111 and …2222


def section(numero=SECTION, lignes=None):
    lignes = [('2026-01-08', 'Librairie Exemple', -2_000, 'achat'), ('2026-01-20', 'Frais carte additionnelle', -312, 'frais')] \
        if lignes is None else lignes
    return {'numero': numero, 'lignes': lignes}


def read(marker, comptes, period=JAN, **extra):
    return controle.read(as_extracted(layout(marker, *period, comptes, **extra)), [FOLIO, COBALT, MARGE])


class TestControle(unittest.TestCase):
    def refused(self, fragment, function, *args, **kwargs):
        with self.assertRaises(RefusBanque) as caught:
            function(*args, **kwargs)
        message = str(caught.exception)
        self.assertIn(fragment, message)
        # Refusals reach the model: never an amount nor a description (D057).
        for leak in ('CANARI', '4,50', '450', '1 000', '2 500', 'Café'):
            self.assertNotIn(leak, message)
        return message

    def test_the_real_registry_is_empty_until_a_first_real_statement(self):
        self.assertEqual(REGISTRE, [])
        with patch.object(confirmation, 'fictitious', return_value=False):
            self.assertEqual(registre(), [])
            self.refused("aucune mise en page de relevé n'est encore définie", controle.read,
                         as_extracted(layout('FOLIO EXEMPLE', *JAN, [bank()])))
        # On fictitious data only, the profiles of the tests' fictitious layout are there.
        self.assertEqual([p.SOURCE for p in registre()], ['folio_desjardins', 'cobalt', 'marge_desjardins'])

    def test_a_statement_is_read_with_masked_accounts_and_checked_balances(self):
        data = read('FOLIO EXEMPLE', [bank(), bank(SAVINGS, debut=5_000, lignes=[])])
        self.assertEqual((data['source'], data['du'], data['au']), ('folio_desjardins', *JAN))
        first, second = data['comptes']
        self.assertEqual((first['apercu'], first['type'], second['apercu']), ('…3456 EOP', 'banque', '…3456 ES1'))
        self.assertEqual((first['solde_debut_cents'], first['solde_fin_cents']), (100_000, 349_550))
        self.assertEqual([(l['date'], l['montant_cents'], l['genre']) for l in first['lignes']],
                         [('2026-01-05', -450, 'operation'), ('2026-01-15', 250_000, 'operation')])
        self.assertNotIn('numero', first)
        self.assertEqual((second['solde_debut_cents'], second['solde_fin_cents'], second['lignes']), (5_000, 5_000, []))
        self.assertIsNone(data['points'])

    def test_one_cent_off_refuses_the_whole_statement(self):
        for wrong in (349_551, 349_549):
            message = self.refused('Soldes qui ne concordent pas pour le compte …3456 ES1', read, 'FOLIO EXEMPLE',
                                   [bank(), bank(SAVINGS, debut=5_000, lignes=[('2026-01-02', 'Dépôt', 100_000, 'operation')],
                                             fin=105_000 + (wrong - 349_550))])
            self.assertIn('Relevé entier refusé', message)
        self.refused('Soldes qui ne concordent pas pour le compte …3456 EOP', read, 'FOLIO EXEMPLE', [bank(fin=349_551)])

    def test_a_statement_across_pages_is_read_whole(self):
        many = [(f'2026-01-{n % 28 + 1:02d}', f'Commerce fictif {n}', -(n + 1) * 10, 'operation') for n in range(120)]
        data = read('FOLIO EXEMPLE', [bank(debut=1_000_000, lignes=many)])
        self.assertEqual(len(data['comptes'][0]['lignes']), 120)
        self.assertGreater(len(as_extracted(layout('FOLIO EXEMPLE', *JAN, [bank(lignes=many)]))), 2)

    def test_same_day_same_amount_operations_are_two_with_ranks(self):
        twice = [('2026-01-05', 'Café Démo', -450, 'operation'), ('2026-01-05', 'Café Démo', -450, 'operation')]
        lines = read('FOLIO EXEMPLE', [bank(lignes=twice)])['comptes'][0]['lignes']
        self.assertEqual([l['rang'] for l in lines], [1, 2])
        self.assertNotEqual(lines[0]['cle'], lines[1]['cle'])
        # The key leaves the description out: the same operation reads differently in a PDF and in an OFX.
        other = read('FOLIO EXEMPLE', [bank(lignes=[('2026-01-05', 'CAFE DEMO #12', -450, 'operation')])])['comptes'][0]['lignes']
        self.assertEqual(other[0]['cle'], lines[0]['cle'])

    def test_an_operation_outside_the_period_or_against_its_genre_is_refused(self):
        self.refused('hors de la période', read, 'FOLIO EXEMPLE', [bank(lignes=[('2026-02-01', 'Plus tard', -100, 'operation')])])
        self.refused('contraire à son genre', read, 'CARTE EXEMPLE', [card(lignes=[('2026-01-03', 'Achat', 500, 'achat')])])
        self.refused('Genre', read, 'CARTE EXEMPLE', [card(lignes=[('2026-01-03', 'Achat', -500, 'operation')])])

    def test_a_card_balance_is_a_debt(self):
        data = read('CARTE EXEMPLE', [card()])
        account = data['comptes'][0]
        self.assertEqual((account['type'], account['solde_debut_cents'], account['solde_fin_cents']), ('carte', -20_000, -6_750))
        self.assertEqual([l['genre'] for l in account['lignes']], ['achat', 'paiement', 'credit'])

    def test_points_that_agree_or_not_never_stop_the_operations(self):
        self.assertEqual(read('CARTE EXEMPLE', [card()], points=(12_000, 850, 2_000, 10_850))['points'],
                         {'precedent': 12_000, 'gagnes': 850, 'utilises': 2_000, 'nouveau': 10_850, 'concorde': True})
        off = read('CARTE EXEMPLE', [card()], points=(12_000, 850, 2_000, 10_851))
        self.assertFalse(off['points']['concorde'])
        self.assertEqual(len(off['comptes'][0]['lignes']), 3)

    # Spec 004 : la section de la carte additionnelle est lue avec le compte de la carte ------------------------------------

    def test_an_additional_section_is_read_with_the_card_and_the_whole_statement_balances(self):
        data = read('CARTE EXEMPLE', [card()], additionnelles=[section()])
        self.assertNotIn('titulaire_additionnel', data)
        self.assertEqual(len(data['comptes']), 1)
        account = data['comptes'][0]
        self.assertEqual(account['additionnelles'], [{'empreinte': sha('compte|370000000081111'), 'apercu': '…1111'}])
        # One list for both holders, in the order of the statement; only the additional card's lines carry its preview.
        self.assertEqual([(l['date'], l['montant_cents'], l.get('carte')) for l in account['lignes']],
                         [('2026-01-03', -8_250, None), ('2026-01-10', 20_000, None), ('2026-01-12', 1_500, None),
                          ('2026-01-08', -2_000, '…1111'), ('2026-01-20', -312, '…1111')])
        # Opening balance + every line of both sections = closing balance.
        self.assertEqual((account['solde_debut_cents'], account['solde_fin_cents']), (-20_000, -9_062))
        self.assertNotIn(ADDITIONAL_NAME, repr(data))
        self.assertNotIn('Zoé', repr(data))
        self.assertNotIn('81111', repr(data))

    def test_a_statement_that_balances_only_without_the_section_is_refused(self):
        # The closing balance printed as if the section were not there: the whole statement is refused.
        message = self.refused('Soldes qui ne concordent pas pour le compte …9876', read, 'CARTE EXEMPLE', [card(fin=-6_750)],
                               additionnelles=[section()])
        self.assertIn('Relevé entier refusé', message)
        self.refused('Soldes qui ne concordent pas', read, 'CARTE EXEMPLE', [card(fin=-9_061)], additionnelles=[section()])

    def test_the_lines_of_a_section_are_checked_like_the_others(self):
        for lines, fragment in (([('2026-02-01', 'Plus tard', -100, 'achat')], 'hors de la période'),
                                ([('2026-01-03', 'Achat', 500, 'achat')], 'contraire à son genre'),
                                ([('2026-01-03', 'Achat', -500, 'operation')], 'Genre')):
            self.refused(fragment, read, 'CARTE EXEMPLE', [card()], additionnelles=[section(lignes=lines)])

    def test_a_reading_in_the_old_shape_is_refused(self):
        # Fresh review: a profile written to the old contract of 003 must not lose a section that nets to zero.
        data = {'source': 'cobalt', 'du': JAN[0], 'au': JAN[1], 'titulaire_additionnel': True, 'points': None,
                'comptes': [{'numero': CARD, 'type': 'carte', 'solde_debut_cents': -100, 'solde_fin_cents': -100, 'lignes': []}]}
        self.refused('ancien contrat', controle.verify, data, 'cobalt')
        self.refused('ancien contrat', controle.verify, {**data, 'titulaire_additionnel': False}, 'cobalt')

    def test_ranks_count_every_line_of_the_card(self):
        twice = ('2026-01-05', 'Café Démo', -450, 'achat')
        lines = read('CARTE EXEMPLE', [card(lignes=[twice])], additionnelles=[section(lignes=[twice])])['comptes'][0]['lignes']
        # One purchase per holder, same day and amount: two operations, two keys.
        self.assertEqual([(l['rang'], l.get('carte')) for l in lines], [(1, None), (2, '…1111')])
        self.assertNotEqual(lines[0]['cle'], lines[1]['cle'])

    def test_a_section_is_refused_outside_a_cobalt_card(self):
        self.refused('Section additionnelle inattendue', read, 'FOLIO EXEMPLE', [bank()], additionnelles=[section(lignes=[])])
        self.refused('Section additionnelle inattendue', read, 'MARGE EXEMPLE',
                     [{'numero': MARGIN, 'type': 'marge', 'debut': -100, 'lignes': []}], additionnelles=[section(lignes=[])])
        self.refused('Section additionnelle inattendue', read, 'CARTE EXEMPLE', [bank(lignes=[])], additionnelles=[section(lignes=[])])

    def test_sections_two_at_most_distinct_and_numbered(self):
        three = [section(lignes=[]), section(OTHER, lignes=[]), section('3700-000000-83333', lignes=[])]
        self.refused('Plus de deux sections additionnelles', read, 'CARTE EXEMPLE', [card()], additionnelles=three)
        self.assertEqual(len(read('CARTE EXEMPLE', [card()], additionnelles=three[:2])['comptes'][0]['additionnelles']), 2)
        self.refused('même aperçu', read, 'CARTE EXEMPLE', [card()],
                     additionnelles=[section(lignes=[]), section('3700-000009-91111', lignes=[])])
        self.refused('même aperçu', read, 'CARTE EXEMPLE', [card()], additionnelles=[section('3700-000001-09876', lignes=[])])
        self.refused('sans numéro de carte lisible', read, 'CARTE EXEMPLE', [card()], additionnelles=[section('AB1', lignes=[])])
        # The fictitious profile always answers the new shape: a list, empty when there is no section.
        self.assertEqual(read('CARTE EXEMPLE', [card()])['comptes'][0]['additionnelles'], [])

    def test_more_than_one_profile_or_none_is_refused(self):
        pages = as_extracted(layout('FOLIO EXEMPLE', *JAN, [bank()]))
        self.refused("plus d'un profil", controle.read, pages, [FOLIO, FOLIO])
        self.refused('carte American Express Cobalt', controle.read, pages, [COBALT])

    def test_a_profile_that_trips_refuses_without_guessing(self):
        class Broken:
            SOURCE = 'folio_desjardins'

            def reconnait(self, pages):
                return True

            def lire(self, pages):
                raise KeyError('colonne')
        self.refused('Mise en page inattendue pour un relevé folio Desjardins', controle.read,
                     as_extracted(layout('FOLIO EXEMPLE', *JAN, [bank()])), [Broken()])

    def test_limits(self):
        self.refused('Période du relevé invalide', read, 'FOLIO EXEMPLE', [bank(lignes=[])], period=('2026-02-01', '2026-01-01'))
        twins = [bank(), bank('815-30000-9993456-EOP', lignes=[])]
        self.refused('même aperçu', read, 'FOLIO EXEMPLE', twins)


class TestExtractionCheck(unittest.TestCase):
    """The program's reply is checked before any profile reads it."""
    def test_valid_reply(self):
        reply = {'pages': [{'numero': 1, 'largeur': 612, 'hauteur': 792, 'lignes': [{'x': 40, 'y': 60, 'texte': 'Bonjour'}]}]}
        self.assertEqual(extraction.check(reply)[0]['lignes'][0]['texte'], 'Bonjour')

    def test_invalid_replies_are_refused(self):
        page = {'numero': 1, 'largeur': 612, 'hauteur': 792, 'lignes': []}
        for reply in ({'erreur': 'PDF chiffré ou protégé par mot de passe'}, [], {'pages': []}, {'pages': [dict(page, numero=2)]},
                      {'pages': [dict(page, lignes=[{'x': -1, 'y': 0, 'texte': 'a'}])]},
                      {'pages': [dict(page, lignes=[{'x': 1, 'y': 0, 'texte': ''}])]},
                      {'pages': [dict(page, lignes=[{'x': 1.5, 'y': 0, 'texte': 'a'}])]},
                      {'pages': [page] * 51}):
            with self.assertRaises(RefusBanque):
                extraction.check(reply)

    def test_missing_program_or_not_a_pdf(self):
        with self.assertRaisesRegex(RefusBanque, 'moneta-construire-releve'):
            extraction.pages(b'%PDF-1.4')



@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'PDFKit : macOS avec swiftc')
class TestImportPdf(unittest.TestCase):
    """The whole path on fictitious statements: a real PDF, read by moneta-releve (built in a temporary folder) in the
    finances command, prepared in the lot, written by WR_SAVE_FINANCES_BD."""
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.program = Path(cls.build.name) / 'moneta-releve'
        compile_ou_cache(SOURCE, cls.program, ['-O'])

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home')})
        self.env.start()
        self.imports = self.folder / 'home' / 'imports'
        self.imports.mkdir(parents=True)
        (self.folder / 'home' / 'apps').mkdir()
        shutil.copy(self.program, self.folder / 'home' / 'apps' / 'moneta-releve')
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

    def commit(self):
        return self.ok('WR_SAVE_FINANCES_BD', {'code': self.ok('finances_lot_preparer')['code']})

    def scalar(self, sql, *params):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute(sql, params).fetchone()[0]

    def rows(self, sql, *params):
        with closing(sqlite3.connect(f'file:{self.ledger}?mode=ro', uri=True)) as con:
            return con.execute(sql, params).fetchall()

    def drop(self, name, marker, comptes, period=JAN, **extra):
        (self.imports / name).write_bytes(pdf(layout(marker, *period, comptes, **extra)))
        return name

    def folio(self, name='janvier.pdf', links=None, **extra):
        self.drop(name, 'FOLIO EXEMPLE', [bank(), bank(SAVINGS, debut=5_000, lignes=[('2026-01-20', 'Dépôt inscriptions', 32_000, 'operation')])],
                  **extra)
        return self.ok('releve_import_preparer', {'fichier': name, 'comptes': links or {'…3456 EOP': 'Perso', '…3456 ES1': 'Dojo'}})

    def test_a_pdf_statement_is_imported_through_the_lot_and_the_summary_shows_no_line(self):
        self.fails('releve_import_preparer', {'fichier': self.drop('janvier.pdf', 'FOLIO EXEMPLE', [bank()])}, 'pas encore liés')
        started = time.monotonic()
        prepared = self.folio()
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(prepared['ajoute'])
        text = repr(prepared)
        for leak in ('CANARI', 'Café', 'Dépôt inscriptions', '0123456'):
            self.assertNotIn(leak, text)
        self.assertIn("'source': 'folio_desjardins'", text)
        self.assertIn("'solde_controle': 'ok'", text)
        self.assertEqual(self.scalar('SELECT count(*) FROM operations'), 0)
        self.commit()
        self.assertEqual(self.rows('SELECT format, source, du, au, lignes, nouvelles FROM imports'),
                         [('pdf', 'folio_desjardins', '2026-01-01', '2026-01-31', 3, 3)])
        self.assertEqual(self.rows('SELECT b.apercu, b.type, s.du, s.au, s.solde_debut_cents, s.solde_fin_cents FROM soldes_releves s '
                                   'JOIN comptes_bancaires b ON b.id=s.compte_bancaire_id ORDER BY b.apercu'),
                         [('…3456 EOP', 'banque', *JAN, 100_000, 349_550), ('…3456 ES1', 'banque', *JAN, 5_000, 37_000)])
        self.assertEqual(self.scalar("SELECT count(*) FROM operations WHERE description LIKE '%CANARI%'"), 1)
        # The same file again adds nothing, and says so.
        again = self.ok('releve_import_preparer', {'fichier': 'janvier.pdf'})
        self.assertFalse(again['ajoute'])

    def test_a_pdf_after_an_ofx_of_the_same_days_adds_only_what_is_missing(self):
        (self.imports / 'janvier.ofx').write_bytes(ofx(statement(CHEQUING, [transaction('F1', '20260105', '-4.50', 'CAFE DEMO #12')])))
        self.ok('releve_import_preparer', {'fichier': 'janvier.ofx', 'comptes': {'…3456 EOP': 'Perso'}})
        self.commit()
        self.drop('janvier.pdf', 'FOLIO EXEMPLE', [bank(lignes=[('2026-01-05', 'Café Démo', -450, 'operation'),
                                                                 ('2026-01-05', 'Café Démo', -450, 'operation'),
                                                                 ('2026-01-15', 'Paie fictive', 250_000, 'operation')])])
        prepared = self.ok('releve_import_preparer', {'fichier': 'janvier.pdf'})
        self.assertIn("'nouvelles': 2", repr(prepared))
        self.assertIn("'doublons': 1", repr(prepared))
        self.commit()
        self.assertEqual(self.rows("SELECT date, montant_cents FROM operations ORDER BY id"),
                         [('2026-01-05', -450), ('2026-01-05', -450), ('2026-01-15', 250_000)])

    def test_next_month_adds_and_an_overlapping_period_is_refused(self):
        self.folio()
        self.commit()
        self.drop('fevrier.pdf', 'FOLIO EXEMPLE', [bank(debut=349_550, lignes=[('2026-02-02', 'Loyer fictif', -90_000, 'operation')])],
                  period=('2026-02-01', '2026-02-28'))
        self.ok('releve_import_preparer', {'fichier': 'fevrier.pdf'})
        self.commit()
        self.assertEqual(self.scalar('SELECT count(*) FROM soldes_releves'), 3)
        self.drop('chevauche.pdf', 'FOLIO EXEMPLE', [bank(debut=0, lignes=[])], period=('2026-01-15', '2026-02-10'))
        self.fails('releve_import_preparer', {'fichier': 'chevauche.pdf'}, 'chevauche un relevé déjà importé')

    def test_a_statement_without_operations_is_kept_for_its_balances(self):
        self.drop('vide.pdf', 'FOLIO EXEMPLE', [bank(debut=12_345, lignes=[])])
        prepared = self.ok('releve_import_preparer', {'fichier': 'vide.pdf', 'comptes': {'…3456 EOP': 'Perso'}})
        self.assertTrue(prepared['ajoute'])
        self.commit()
        self.assertEqual(self.rows('SELECT solde_debut_cents, solde_fin_cents FROM soldes_releves'), [(12_345, 12_345)])
        self.assertEqual(self.scalar('SELECT count(*) FROM operations'), 0)

    def test_an_ignored_account_is_checked_never_kept_never_asked_again_and_can_be_linked_later(self):
        self.folio(links={'…3456 EOP': 'Perso', '…3456 ES1': 'ignore'})
        self.commit()
        self.assertEqual(self.rows('SELECT apercu, type FROM comptes_ignores'), [('…3456 ES1', 'banque')])
        self.assertEqual(self.rows('SELECT apercu FROM comptes_bancaires'), [('…3456 EOP',)])
        self.assertEqual(self.scalar('SELECT count(*) FROM soldes_releves'), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM operations WHERE description LIKE 'Dépôt%'"), 0)
        # Its balances are still checked: a wrong one refuses the whole statement.
        self.drop('faux.pdf', 'FOLIO EXEMPLE', [bank(debut=349_550, lignes=[]), bank(SAVINGS, debut=1, lignes=[], fin=2)],
                  period=('2026-02-01', '2026-02-28'))
        self.fails('releve_import_preparer', {'fichier': 'faux.pdf'}, '…3456 ES1')
        # Next month: not asked again; then linked to Dojo on demand.
        self.drop('fevrier.pdf', 'FOLIO EXEMPLE', [bank(debut=349_550, lignes=[]), bank(SAVINGS, debut=37_000, lignes=[])],
                  period=('2026-02-01', '2026-02-28'))
        self.assertIn("'lien': 'ignoré'", repr(self.ok('releve_import_preparer', {'fichier': 'fevrier.pdf'})))
        self.ok('finances_lot_vider')
        self.ok('releve_import_preparer', {'fichier': 'fevrier.pdf', 'comptes': {'…3456 ES1': 'Dojo'}})
        self.commit()
        self.assertEqual(self.rows('SELECT count(*) FROM comptes_ignores'), [(0,)])
        self.assertEqual(self.rows("SELECT c.nom FROM comptes_bancaires b JOIN comptes c ON c.id=b.compte_id WHERE b.apercu='…3456 ES1'"),
                         [('Dojo',)])

    def test_ignore_works_for_an_ofx_too(self):
        (self.imports / 'sept.ofx').write_bytes(ofx(statement(CHEQUING, [transaction('F1', '20260905', '-4.50', 'CAFE')]),
                                                    statement(SAVINGS, [transaction('F2', '20260906', '-1.00', 'FRAIS')])))
        self.ok('releve_import_preparer', {'fichier': 'sept.ofx', 'comptes': {'…3456 EOP': 'Perso', '…3456 ES1': 'ignore'}})
        self.commit()
        self.assertEqual(self.rows('SELECT date FROM operations'), [('2026-09-05',)])

    def test_a_file_changed_after_the_recap_is_not_imported(self):
        self.folio()
        code = self.ok('finances_lot_preparer')['code']
        self.drop('janvier.pdf', 'FOLIO EXEMPLE', [bank(lignes=[]), bank(SAVINGS, debut=5_000, lignes=[])])
        self.fails('WR_SAVE_FINANCES_BD', {'code': code}, 'a changé')
        self.assertEqual(self.scalar('SELECT count(*) FROM imports'), 0)

    def test_refusals_reach_the_model_without_amounts(self):
        self.drop('faux.pdf', 'FOLIO EXEMPLE', [bank(fin=1)])
        text = self.fails('releve_import_preparer', {'fichier': 'faux.pdf', 'comptes': {'…3456 EOP': 'Perso'}}, 'Soldes qui ne concordent pas')
        for leak in ('CANARI', '4,50', '3 495'):
            self.assertNotIn(leak, text)
        (self.imports / 'scan.pdf').write_bytes(pdf([[]]))
        self.fails('releve_import_preparer', {'fichier': 'scan.pdf'}, 'sans texte')
        (self.imports / 'faux2.pdf').write_bytes(b'OFX pas un PDF')
        self.fails('releve_import_preparer', {'fichier': 'faux2.pdf'}, "n'est pas un PDF")
        (self.imports / 'gros.pdf').write_bytes(b'%PDF-1.4\n' + b' ' * 10_000_000)
        self.fails('releve_import_preparer', {'fichier': 'gros.pdf'}, 'trop gros (10 Mo au plus)')
        (self.imports / 'releve.csv').write_text('date,montant\n')
        self.fails('releve_import_preparer', {'fichier': 'releve.csv'}, 'CSV pas pris en charge')
        (self.imports / 'releve.txt').write_text('texte')
        self.fails('releve_import_preparer', {'fichier': 'releve.txt'}, 'Format accepté : .pdf, .ofx ou .qfx')
        self.fails('releve_import_preparer', {'fichier': '../finances.db'}, 'Nom de fichier invalide')


    # Récit 2 : cartes, marge, points -------------------------------------------------------------------------------------

    def cobalt(self, name='cobalt-janvier.pdf', lignes=None, debut=-20_000, links=None, **extra):
        self.drop(name, 'CARTE EXEMPLE', [card(debut=debut, lignes=lignes)], **extra)
        return self.ok('releve_import_preparer', {'fichier': name, **({'comptes': links} if links else {})})

    def categories(self, apercu):
        return self.rows("SELECT o.date, o.montant_cents, coalesce(p.code, o.classe) FROM operations o JOIN comptes_bancaires b "
                         "ON b.id=o.compte_bancaire_id LEFT JOIN categories_perso p ON p.id=o.categorie_perso_id "
                         "WHERE b.apercu=? ORDER BY o.date, o.id", apercu)

    def test_a_card_statement_classifies_its_payment_by_python_and_keeps_its_points(self):
        prepared = self.cobalt(links={'…9876': 'Perso'}, points=(12_000, 850, 2_000, 10_850),
                               lignes=[('2026-01-03', 'Restaurant Tokyo Démo USD 12,00', -1_650, 'achat'),
                                       ('2026-01-10', 'Paiement merci', 20_000, 'paiement'),
                                       ('2026-01-12', 'Remboursement Démo', 1_500, 'credit'),
                                       ('2026-01-31', 'Intérêts', -312, 'interets')])
        text = repr(prepared)
        self.assertIn("'classees_d_office': {'frais_bancaires': 1, 'virement_interne': 1}", text)
        self.assertIn("'points': {'precedent': 12000, 'gagnes': 850, 'utilises': 2000, 'nouveau': 10850, 'concorde': True}", text)
        self.assertIn("'a_classer': 2", text)
        self.assertNotIn('Tokyo', text)
        self.commit()
        self.assertEqual(self.rows('SELECT type FROM comptes_bancaires'), [('carte',)])
        # The purchase in US dollars is kept as billed in Canadian dollars; the refund lowers spending, never an income.
        self.assertEqual(self.categories('…9876'), [('2026-01-03', -1_650, None), ('2026-01-10', 20_000, 'virement_interne'),
                                                    ('2026-01-12', 1_500, None), ('2026-01-31', -312, 'frais_bancaires')])
        self.assertEqual(self.rows('SELECT precedent, gagnes, utilises, nouveau, concorde FROM points_releves'), [(12_000, 850, 2_000, 10_850, 1)])
        self.assertEqual(self.rows('SELECT solde_debut_cents, solde_fin_cents FROM soldes_releves'), [(-20_000, -462)])

    def test_points_that_do_not_agree_are_set_aside_and_the_operations_still_come_in(self):
        prepared = self.cobalt(links={'…9876': 'Perso'}, points=(12_000, 850, 2_000, 9_999))
        self.assertIn('les points ne concordent pas', repr(prepared))
        self.commit()
        self.assertEqual(self.rows('SELECT concorde FROM points_releves'), [(0,)])
        self.assertEqual(self.scalar('SELECT count(*) FROM operations'), 3)

    def test_the_bank_side_of_a_card_payment_is_matched_in_either_order_once(self):
        payment = [('2026-01-06', 'VIREMENT PAREIL MONTANT', -20_000, 'operation'),
                   ('2026-01-09', 'PAIEMENT CARTE FICTIVE', -20_000, 'operation'),
                   ('2026-01-25', 'Achat même montant trop tard', -20_000, 'operation')]
        # Bank first: its outflow stays to sort until the card statement comes, then one only is matched.
        self.drop('folio.pdf', 'FOLIO EXEMPLE', [bank(lignes=payment)])
        self.ok('releve_import_preparer', {'fichier': 'folio.pdf', 'comptes': {'…3456 EOP': 'Perso'}})
        self.commit()
        self.assertEqual([c for _, _, c in self.categories('…3456 EOP')], [None, None, None])
        prepared = self.cobalt(links={'…9876': 'Perso'})
        self.assertIn("'rapprochees_deja_importees': 1", repr(prepared))
        self.commit()
        # The closest one only (three days before, rather than four); the late one is out of the five days.
        self.assertEqual([c for _, _, c in self.categories('…3456 EOP')], [None, 'virement_interne', None])
        # The other order, on the next month: the card first, then the bank line is matched at its import.
        self.cobalt('cobalt-fevrier.pdf', debut=-6_750, lignes=[('2026-02-10', 'Paiement merci', 6_750, 'paiement')],
                    period=('2026-02-01', '2026-02-28'))
        self.commit()
        self.drop('folio-fevrier.pdf', 'FOLIO EXEMPLE', [bank(debut=289_550, lignes=[('2026-02-07', 'PAIEMENT CARTE FICTIVE', -6_750, 'operation')])],
                  period=('2026-02-01', '2026-02-28'))
        prepared = self.ok('releve_import_preparer', {'fichier': 'folio-fevrier.pdf'})
        self.assertIn("'classees_d_office': {'virement_interne': 1}", repr(prepared))
        self.commit()
        self.assertEqual(self.categories('…3456 EOP')[-1], ('2026-02-07', -6_750, 'virement_interne'))
        # Neither side counts as spending or income.
        overview = self.ok('banque_resume', {'annee': 2026})
        self.assertNotIn('virement_interne', [line['categorie'] for line in overview['par_categorie']])
        self.assertIn('virement_interne', [line['categorie'] for line in overview['hors_budget']])

    def test_a_margin_statement(self):
        self.drop('marge.pdf', 'MARGE EXEMPLE', [{'numero': MARGIN, 'type': 'marge', 'debut': -100_000,
                                                  'lignes': [('2026-01-04', 'Avance', -50_000, 'avance'),
                                                             ('2026-01-20', 'Paiement reçu', 30_000, 'paiement'),
                                                             ('2026-01-31', 'Intérêts', -812, 'interets')]}])
        prepared = self.ok('releve_import_preparer', {'fichier': 'marge.pdf', 'comptes': {'…3456 MC1': 'Perso'}})
        self.assertIn("'classees_d_office': {'frais_bancaires': 1, 'virement_interne': 2}", repr(prepared))
        self.commit()
        self.assertEqual(self.rows('SELECT type FROM comptes_bancaires'), [('marge',)])
        self.assertEqual([c for _, _, c in self.categories('…3456 MC1')], ['virement_interne', 'virement_interne', 'frais_bancaires'])

    # Spec 004 : carte additionnelle de la Cobalt ---------------------------------------------------------------------------

    def family(self, name='cobalt-janvier.pdf', sections=None, links=None, period=JAN, debut=-20_000, lignes=None, **extra):
        """A Cobalt statement with additional sections, dropped and prepared: the raw reply (a question is an error)."""
        self.drop(name, 'CARTE EXEMPLE', [card(debut=debut, lignes=lignes)], period=period,
                  additionnelles=[section()] if sections is None else sections, **extra)
        reply = self.call('releve_import_preparer', {'fichier': name, **({'comptes': links} if links else {})})
        text = reply['content'][0]['text']
        self.assertNotIn('Zoé', text)
        self.assertNotIn('Librairie', text)
        return reply

    def holders(self):
        return self.rows("SELECT o.date, o.montant_cents, a.apercu, coalesce(p.code, o.classe) FROM operations o "
                         "LEFT JOIN cartes_additionnelles a ON a.id=o.carte_additionnelle_id "
                         "LEFT JOIN categories_perso p ON p.id=o.categorie_perso_id ORDER BY o.date, o.id")

    def test_the_first_section_asks_the_usual_question_then_imports_through_the_lot(self):
        # A statement refused before spec 004 left no trace: dropped again, it is asked about like any first statement.
        self.assertEqual(self.scalar('SELECT count(*) FROM imports'), 0)
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("INSERT INTO regles(motif, cible) VALUES ('librairie', 'loisirs')")
        asked = self.family(links={'…9876': 'Perso'})
        self.assertTrue(asked['isError'])
        question = asked['content'][0]['text']
        self.assertIn('Section de carte additionnelle (…1111)', question)
        self.assertIn('comptes = {"…1111": "additionnel"}', question)
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)
        # An unknown account and the section are asked in the same question.
        both = self.family()['content'][0]['text']
        self.assertIn('…9876', both)
        self.assertIn('Section de carte additionnelle (…1111)', both)
        prepared = self.family(links={'…9876': 'Perso', '…1111': 'additionnel'})['structuredContent']
        self.assertTrue(prepared['ajoute'])
        line = prepared['comptes'][0]
        self.assertEqual((line['apercu'], line['section_additionnelle'], line['lignes'], line['nouvelles']), ('…9876', 'lue', 5, 5))
        self.assertEqual(line['rattachement'], 'carte additionnelle …1111 : titulaire additionnel')
        # The card's totals, both holders together; never a line, never a total per holder.
        self.assertEqual((prepared['entrees'], prepared['sorties']), (215.0, 105.62))
        self.assertEqual(prepared['classees_par_regle'], {'loisirs': 1})
        self.assertEqual(prepared['classees_d_office'], {'frais_bancaires': 1, 'virement_interne': 1})
        self.assertEqual(prepared['a_classer'], 2)
        for leak in ('Épicerie', 'Remboursement', "'carte':", 'titulaire_', 'par_titulaire', 'cartes'):
            self.assertNotIn(leak, repr(prepared))
        self.assertEqual(self.scalar('SELECT count(*) FROM cartes_additionnelles'), 0)
        windows = []

        def mac(answer):
            def ask(tool, lines):
                windows.append(lines)
                return answer
            return ask
        with patch.object(confirmation, 'AUTOMATIC', False), patch.object(confirmation, 'ask', mac(False)):
            self.fails('WR_SAVE_FINANCES_BD', {'code': self.ok('finances_lot_preparer')['code']}, 'Non confirmé')
        self.assertEqual((self.scalar('SELECT count(*) FROM cartes_additionnelles'), self.scalar('SELECT count(*) FROM operations')), (0, 0))
        with patch.object(confirmation, 'AUTOMATIC', False), patch.object(confirmation, 'ask', mac(True)):
            self.commit()
        self.assertNotIn('Zoé', repr(windows))
        self.assertEqual(self.rows('SELECT a.apercu, b.apercu, a.remplacee_le FROM cartes_additionnelles a '
                                   'JOIN comptes_bancaires b ON b.id=a.compte_bancaire_id'), [('…1111', '…9876', None)])
        self.assertEqual(self.rows("SELECT action, details FROM audit_log WHERE cible_type='carte_additionnelle'"),
                         [('rattacher_carte_additionnelle', '…1111 -> …9876')])
        # Into the Perso account with the card of Ugo; the holder kept on each line for the check only; the usual classing.
        self.assertEqual(self.holders(), [('2026-01-03', -8_250, None, None), ('2026-01-08', -2_000, '…1111', 'loisirs'),
                                          ('2026-01-10', 20_000, None, 'virement_interne'), ('2026-01-12', 1_500, None, None),
                                          ('2026-01-20', -312, '…1111', 'frais_bancaires')])
        self.assertEqual(self.rows("SELECT DISTINCT c.nom FROM operations o JOIN comptes_bancaires b ON b.id=o.compte_bancaire_id "
                                   "JOIN comptes c ON c.id=b.compte_id"), [('Perso',)])

    def test_the_next_statement_passes_a_third_card_is_refused_and_a_new_card_replaces(self):
        self.family(links={'…9876': 'Perso', '…1111': 'additionnel'})
        self.commit()
        # The same card next month: no question.
        feb = ('2026-02-01', '2026-02-28')
        self.assertFalse(self.family('fevrier.pdf', [section(lignes=[('2026-02-08', 'Achat', -700, 'achat')])], period=feb, debut=-9_062,
                                     lignes=[])['isError'])
        self.commit()
        mar = ('2026-03-01', '2026-03-31')
        other = [section(OTHER, lignes=[('2026-03-08', 'Achat', -900, 'achat')])]
        refused = self.family('mars.pdf', other, period=mar, debut=-9_762, lignes=[])
        self.assertTrue(refused['isError'])
        self.assertIn('troisième carte', refused['content'][0]['text'])
        self.assertIn('…2222', refused['content'][0]['text'])
        self.fails('releve_import_preparer', {'fichier': 'mars.pdf', 'comptes': {'…2222': 'additionnel'}}, 'déjà rattachée')
        self.assertEqual((self.scalar('SELECT count(*) FROM cartes_additionnelles'), self.ok('finances_lot_voir')['etapes']), (1, 0))
        replaced = self.ok('releve_import_preparer', {'fichier': 'mars.pdf', 'comptes': {'…2222': 'remplace'}})
        self.assertEqual(replaced['comptes'][0]['rattachement'], 'carte additionnelle …2222 : remplace …1111')
        self.commit()
        self.assertEqual(self.rows('SELECT apercu, remplacee_le IS NOT NULL FROM cartes_additionnelles ORDER BY id'),
                         [('…1111', 1), ('…2222', 0)])
        self.assertEqual(self.rows("SELECT action, details FROM audit_log WHERE cible_type='carte_additionnelle' ORDER BY id"),
                         [('rattacher_carte_additionnelle', '…1111 -> …9876'), ('remplacer_carte_additionnelle', '…1111 -> …2222')])
        # The month after: a late line of the old card and the new card, without a question.
        apr = ('2026-04-01', '2026-04-30')
        late = self.family('avril.pdf', [section(lignes=[('2026-04-02', 'Tardif', -100, 'achat')]),
                                         section(OTHER, lignes=[('2026-04-09', 'Achat', -200, 'achat')])],
                           period=apr, debut=-10_662, lignes=[])
        self.assertFalse(late['isError'], late)
        self.commit()
        self.assertEqual([h[2] for h in self.holders()[-4:]], ['…1111', '…2222', '…1111', '…2222'])

    def test_answers_that_do_not_fit_a_section_are_refused_with_a_reason(self):
        for links, fragment in (({'…9876': 'Perso', '…1111': 'remplace'}, 'carte additionnelle à remplacer'),
                                ({'…9876': 'Perso', '…1111': 'Perso'}, 'additionnel ou remplace'),
                                ({'…9876': 'Perso', '…1111': 'ignore'}, 'additionnel ou remplace'),
                                ({'…9876': 'Perso', '…1111': 'Dojo'}, 'additionnel ou remplace'),
                                ({'…9876': 'additionnel', '…1111': 'additionnel'}, 'seulement pour une carte additionnelle'),
                                ({'…9876': 'remplace'}, 'seulement pour une carte additionnelle'),
                                ({'…9876': 'Dojo', '…1111': 'additionnel'}, 'compte Perso'),
                                ({'…9876': 'ignore', '…1111': 'additionnel'}, 'compte Perso')):
            text = self.family(links=links)['content'][0]['text']
            self.assertIn(fragment, text, links)
        two = [section(lignes=[]), section(OTHER, lignes=[])]
        self.assertIn("plus d'un aperçu nouveau", self.family(sections=two, links={'…9876': 'Perso', '…1111': 'additionnel',
                                                                                  '…2222': 'additionnel'})['content'][0]['text'])
        self.assertEqual(self.ok('finances_lot_voir')['etapes'], 0)

    def test_a_preview_is_either_a_bank_account_or_an_additional_card(self):
        self.drop('janvier.pdf', 'FOLIO EXEMPLE', [bank()])
        self.ok('releve_import_preparer', {'fichier': 'janvier.pdf', 'comptes': {'…3456 EOP': 'Perso'}})
        self.commit()
        clash = [section('815-30000-9993456-EOP', lignes=[])]
        self.assertIn('…3456 EOP', self.family(sections=clash, links={'…9876': 'Perso', '…3456 EOP': 'additionnel'})['content'][0]['text'])
        self.assertEqual(self.scalar('SELECT count(*) FROM cartes_additionnelles'), 0)
        self.ok('finances_lot_vider')
        self.family(links={'…9876': 'Perso', '…1111': 'additionnel'})
        self.commit()
        # The other way: an account whose preview is already an additional card.
        self.drop('folio.pdf', 'FOLIO EXEMPLE', [bank(numero='815-30000-0001111', lignes=[])], period=('2026-02-01', '2026-02-28'))
        self.fails('releve_import_preparer', {'fichier': 'folio.pdf', 'comptes': {'…1111': 'Perso'}}, 'carte additionnelle')

    def test_a_family_month_end_to_end_with_the_fake_mac(self):
        # Spec 004, quickstart §3 (T023): the import with the additional card, two budgets, one Enregistrer; the state of
        # the budget opens no window any more (spec 002, R11).
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("INSERT INTO regles(motif, cible) VALUES ('librairie', 'loisirs')")
        asks = []
        with patch.object(confirmation, 'AUTOMATIC', False), \
                patch.object(confirmation, 'ask', lambda title, lines: asks.append(lines) or True):
            self.assertTrue(self.family(links={'…9876': 'Perso'})['isError'])
            imported = self.family(links={'…9876': 'Perso', '…1111': 'additionnel'})['structuredContent']
            self.assertNotIn('budget', repr(imported))  # the import recap never speaks of the budget (FR-017)
            self.ok('budget_preparer', {'categorie': 'loisirs', 'montant': 100, 'debut': '2026-01'})
            self.ok('budget_preparer', {'categorie': 'epicerie', 'montant': 900, 'debut': '2026-01'})
            self.commit()
            found = self.ok('budget_etat', {'annee': 2026, 'mois': 1})
        self.assertEqual(len(asks), 1)
        # The additional card's purchase counts in the family's spending; the card's own purchase is still to sort.
        self.assertEqual([(c['categorie'], c['depense'], c['budget']) for c in found['categories']],
                         [('epicerie', 0.0, 900.0), ('loisirs', 20.0, 100.0)])
        self.assertEqual(found['avertissements']['a_classer'], {'nombre': 2, 'montant': 67.5})
        text = repr(found)
        for leak in ('Librairie', 'Épicerie Exemple', 'Zoé', '…1111', 'operations', 'description'):
            self.assertNotIn(leak, text)

    def test_same_purchase_on_both_cards_is_two_operations_and_points_count_once(self):
        twice = ('2026-01-05', 'Café Démo', -450, 'achat')
        reply = self.family(sections=[section(lignes=[twice])], lignes=[twice], links={'…9876': 'Perso', '…1111': 'additionnel'},
                            points=(12_000, 850, 0, 12_850))
        self.assertIn("'points': {'precedent': 12000, 'gagnes': 850, 'utilises': 0, 'nouveau': 12850, 'concorde': True}",
                      repr(reply['structuredContent']))
        self.commit()
        self.assertEqual([(h[1], h[2]) for h in self.holders()], [(-450, None), (-450, '…1111')])
        again = self.ok('releve_import_preparer', {'fichier': 'cobalt-janvier.pdf'})
        self.assertFalse(again['ajoute'])
        self.assertEqual(self.rows('SELECT precedent, gagnes, utilises, nouveau FROM points_releves'), [(12_000, 850, 0, 12_850)])
        self.assertEqual(self.ok('points_japon')['solde'], {'points': 12_850, 'au': '2026-01-31'})

    def test_a_card_linked_to_perso_is_read_without_a_window(self):
        # Spec 002, R11 (replaces D050 here): amounts and a card's descriptions are read like the Dojo's.
        self.cobalt(links={'…9876': 'Perso'})
        self.commit()
        with FausseFenetre(reponse=False) as fenetre:
            found = self.ok('banque_operations', {'compte': 'Perso'})
            self.ok('banque_a_classer', {})
        self.assertEqual(fenetre.nombre, 0)
        self.assertEqual({o['nature'] for o in found['operations']}, {'carte'})


    # Récit 3 : relevés manquants -----------------------------------------------------------------------------------------

    def test_missing_statements_are_told_at_import_and_on_demand_without_amounts(self):
        self.folio(links={'…3456 EOP': 'Perso', '…3456 ES1': 'ignore'})
        self.commit()
        self.drop('avril.pdf', 'FOLIO EXEMPLE', [bank(debut=777_777, lignes=[]), bank(SAVINGS, debut=1, lignes=[])],
                  period=('2026-04-01', '2026-04-30'))
        prepared = self.ok('releve_import_preparer', {'fichier': 'avril.pdf'})
        self.assertIn("'manquants': {'…3456 EOP': ['2026-02-01 au 2026-03-31']}", repr(prepared))
        self.commit()
        found = self.ok('releves_manquants')
        self.assertFalse(found['complet'])
        self.assertEqual([(a['apercu'], a['trous']) for a in found['comptes']], [('…3456 EOP', [{'du': '2026-02-01', 'au': '2026-03-31'}])])
        text = repr(found)
        for amount in ('777', '3495', '349550'):
            self.assertNotIn(amount, text)
        self.assertNotIn('ES1', text)
        self.assertEqual(self.ok('releves_manquants', {'compte': 'Dojo'})['comptes'], [])
        self.fails('releves_manquants', {'compte': 'Commun'}, 'compte')


    # Suites de la relecture neuve ------------------------------------------------------------------------------------------

    def test_an_ofx_after_a_pdf_of_the_same_days_adds_only_what_is_missing(self):
        self.drop('janvier.pdf', 'FOLIO EXEMPLE', [bank(lignes=[('2026-01-05', 'Café Démo', -450, 'operation'),
                                                                 ('2026-01-15', 'Paie fictive', 250_000, 'operation')])])
        self.ok('releve_import_preparer', {'fichier': 'janvier.pdf', 'comptes': {'…3456 EOP': 'Perso'}})
        self.commit()
        (self.imports / 'janvier.ofx').write_bytes(ofx(statement(CHEQUING, [
            transaction('F1', '20260105', '-4.50', 'CAFE DEMO #12'), transaction('F2', '20260105', '-4.50', 'CAFE DEMO #12'),
            transaction('F3', '20260115', '2500.00', 'PAIE')])))
        prepared = self.ok('releve_import_preparer', {'fichier': 'janvier.ofx'})
        self.assertIn("'nouvelles': 1", repr(prepared))
        self.commit()
        self.assertEqual(self.rows('SELECT date, montant_cents FROM operations ORDER BY date, id'),
                         [('2026-01-05', -450), ('2026-01-05', -450), ('2026-01-15', 250_000)])

    def test_an_ofx_covers_the_period_it_declares(self):
        lines = transaction('F1', '20260105', '-4.50', 'CAFE')
        text = ofx(statement(CHEQUING, [lines])).replace(b'<DTSTART>20260901<DTEND>20260930', b'<DTSTART>20260101<DTEND>20260131')
        (self.imports / 'janvier.ofx').write_bytes(text)
        self.ok('releve_import_preparer', {'fichier': 'janvier.ofx', 'comptes': {'…3456 EOP': 'Perso'}})
        self.commit()
        self.assertEqual(self.rows('SELECT du, au FROM imports'), [('2026-01-01', '2026-01-31')])
        self.drop('fevrier.pdf', 'FOLIO EXEMPLE', [bank(debut=0, lignes=[])], period=('2026-02-01', '2026-02-28'))
        prepared = self.ok('releve_import_preparer', {'fichier': 'fevrier.pdf'})
        self.assertNotIn('manquants', prepared)

    def test_the_same_statement_with_other_balances_is_refused(self):
        self.folio()
        self.commit()
        self.drop('corrige.pdf', 'FOLIO EXEMPLE', [bank(debut=100_001), bank(SAVINGS, debut=5_000, lignes=[])])
        text = self.fails('releve_import_preparer', {'fichier': 'corrige.pdf'}, "déjà importé pour le compte …3456 EOP avec d'autres soldes")
        self.assertNotIn('1 000', text)

    def test_an_old_payment_never_takes_an_old_outflow_and_matches_are_shown_before_the_click(self):
        self.drop('folio.pdf', 'FOLIO EXEMPLE', [bank(lignes=[('2026-01-09', 'PAIEMENT CARTE', -20_000, 'operation')])])
        self.ok('releve_import_preparer', {'fichier': 'folio.pdf', 'comptes': {'…3456 EOP': 'Perso'}})
        self.commit()
        prepared = self.cobalt(links={'…9876': 'Perso'})
        self.assertIn("'rapprochements': ['2026-01-09 …3456 EOP → …9876']", repr(prepared))
        self.commit()
        # Ugo sets the bank line back to sort: a later import of another account never pairs the old payment with it again.
        with closing(sqlite3.connect(self.ledger)) as con, con:
            con.execute("UPDATE operations SET classe=NULL, categorie_perso_id=NULL WHERE compte_bancaire_id="
                        "(SELECT id FROM comptes_bancaires WHERE apercu='…3456 EOP')")
        self.drop('marge.pdf', 'MARGE EXEMPLE', [{'numero': MARGIN, 'type': 'marge', 'debut': -100, 'lignes': []}])
        prepared = self.ok('releve_import_preparer', {'fichier': 'marge.pdf', 'comptes': {'…3456 MC1': 'Perso'}})
        self.assertNotIn('rapprochements', prepared)
        self.assertNotIn('classees_d_office', prepared)

    def test_fees_of_a_dojo_card_wait_for_their_t2125_line(self):
        self.cobalt(links={'…9876': 'Dojo'}, lignes=[('2026-01-10', 'Paiement merci', 20_000, 'paiement'),
                                                    ('2026-01-31', 'Frais annuels', -12_000, 'frais')])
        self.commit()
        self.assertEqual([c for _, _, c in self.categories('…9876')], ['virement_interne', None])


if __name__ == '__main__':
    unittest.main()
