"""Factures liées aux dépenses (D063), sur un Drive et une base fictifs : boîte « à classer », aperçu, déplacement à
l'enregistrement, tout ou rien, fichiers changés ou détournés refusés."""
import base64
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch

from moneta.outils import factures
from socle import confirmation
from moneta.outils.serveur import McpServer
from moneta.outils.mcp_finances import VARIABLE, FinancesReads, finances_lot
from outils_tests import SourceStore, seed_fictive_finances

confirmation.AUTOMATIC = True  # fictitious data only: no dialog on the Mac

TODAY = date.today().isoformat()


def png(seed):
    """A small valid PNG, different for each seed."""
    raw = b''.join(b'\x00' + bytes([seed % 256, 80, 160]) * 8 for _ in range(8))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 8, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def scalar(db, sql, *params):
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as con:
        return con.execute(sql, params).fetchone()[0]


class TestFactures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.drive = self.folder / 'drive'
        self.inbox = self.drive / 'DojoD' / '05 Administration' / 'Factures' / 'à classer'
        self.inbox.mkdir(parents=True)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.folder / 'home'), 'MONETA_DRIVE_DOSSIER': str(self.drive)})
        self.env.start()
        self.ledger = seed_fictive_finances(self.folder)
        self.server = McpServer(modules=[FinancesReads(), finances_lot(SourceStore(self.ledger, self.folder / 'b', variable=VARIABLE))])

    def tearDown(self):
        self.env.stop()
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
        return self.call('WR_SAVE_FINANCES_BD', {'code': self.ok('finances_lot_preparer')['code']})

    def drop(self, name, seed=1):
        (self.inbox / name).write_bytes(png(seed))

    def expense(self, name, **more):
        return {'date': TODAY, 'montant': 45.99, 'categorie': '8811', 'fournisseur': 'Canadian Tire', 'facture': name, **more}

    def test_migration_adds_the_column(self):
        self.assertEqual(scalar(self.ledger, "SELECT count(*) FROM pragma_table_info('depenses') WHERE name='facture'"), 1)
        self.assertEqual(scalar(self.ledger, "SELECT count(*) FROM sqlite_master WHERE name='idx_depenses_facture'"), 1)

    def test_inbox_preview_and_link(self):
        self.drop('IMG_0001.png')
        self.assertEqual([r['nom'] for r in self.ok('factures_a_classer')['factures']], ['IMG_0001.png'])
        seen = self.call('facture_voir', {'nom': 'IMG_0001.png'})
        image = [c for c in seen['content'] if c['type'] == 'image']
        self.assertEqual(len(image), 1)
        self.assertEqual(image[0]['mimeType'], 'image/jpeg')
        self.assertTrue(base64.b64decode(image[0]['data']).startswith(b'\xff\xd8'))

        added = self.ok('depense_preparer', self.expense('IMG_0001.png'))
        stored = f'{TODAY[:4]}/{TODAY}_45.99.png'  # date and amount only, no free text (F4, D091)
        self.assertIn(f'déplacée dans « DojoD/05 Administration/Factures/{stored} »', added['facture'])
        self.assertNotIn('piece', added['manque'])
        # Nothing moves before the commit.
        self.assertTrue((self.inbox / 'IMG_0001.png').exists())
        self.assertFalse(self.commit()['isError'])
        self.assertFalse((self.inbox / 'IMG_0001.png').exists())
        self.assertEqual((self.drive / 'DojoD' / '05 Administration' / 'Factures' / stored).read_bytes(), png(1))
        self.assertEqual(scalar(self.ledger, 'SELECT facture FROM depenses'), stored)
        # Without details the path stays on the Mac; the receipt counts as the receipt.
        row = self.ok('finances_depenses', {})['depenses'][0]
        self.assertTrue(row['facture_liee'])
        self.assertNotIn('facture', row)
        self.assertNotIn('piece', row['manque'])
        # The linked receipt can be seen again (behind « Autoriser »).
        self.assertTrue(any(c['type'] == 'image' for c in self.call('facture_voir', {'depense': 1})['content']))
        # Same day and amount: the next one gets a rank.
        self.drop('IMG_0002.png', 2)
        self.assertIn(f'{TODAY}_45.99-2.png', self.ok('depense_preparer', self.expense('IMG_0002.png'))['facture'])

    def test_completion_links_once(self):
        self.ok('depense_preparer', {'date': TODAY, 'montant': 12.5, 'categorie': '8811'})
        self.assertFalse(self.commit()['isError'])
        self.drop('scan.png')
        changed = self.ok('depense_completer_preparer', {'id': 1, 'facture': 'scan.png'})
        self.assertIn(f'{TODAY}_12.50.png', changed['changements']['facture']['apres'])
        self.assertFalse(self.commit()['isError'])
        self.drop('autre.png', 3)
        self.fails('depense_completer_preparer', {'id': 1, 'facture': 'autre.png'}, 'déjà liée')

    def test_a_receipt_is_seen_without_autoriser_and_turns_the_signal_on(self):
        """T036 (spec 002, R11, E10): a receipt is written by its supplier, someone else's text. Shown without a window,
        to be sorted or linked alike, and marked: the signal is written, so the expense prepared next opens the window."""
        from socle import signal_texte
        from outils_tests import FausseFenetre
        self.drop('IMG_0001.png')
        self.ok('depense_preparer', self.expense('IMG_0001.png'))
        self.assertFalse(self.commit()['isError'])
        self.assertFalse(signal_texte.chemin().exists())
        with FausseFenetre(reponse=False) as fenetre:
            linked = self.call('facture_voir', {'depense': 1})
        self.assertFalse(linked['isError'], linked)
        self.assertEqual(fenetre.nombre, 0)
        self.assertTrue(any(c['type'] == 'image' for c in linked['content']))
        self.assertIn('texte_externe', linked['structuredContent'])
        self.assertEqual(signal_texte.etat(), 'recent')
        signal_texte.chemin().unlink()
        self.drop('IMG_0002.png', 2)
        with FausseFenetre(reponse=False) as fenetre:
            waiting = self.call('facture_voir', {'nom': 'IMG_0002.png'})
        self.assertEqual(fenetre.nombre, 0)
        self.assertIn('texte_externe', waiting['structuredContent'])
        self.assertEqual(signal_texte.etat(), 'recent')

    def test_same_receipt_twice_in_a_lot(self):
        self.drop('IMG_0001.png')
        self.ok('depense_preparer', self.expense('IMG_0001.png'))
        self.fails('depense_preparer', self.expense('IMG_0001.png', montant=10), 'déjà dans le lot')

    def test_changed_file_is_refused_and_nothing_moves(self):
        self.drop('IMG_0001.png')
        self.ok('depense_preparer', self.expense('IMG_0001.png'))
        self.drop('IMG_0001.png', 9)
        reply = self.commit()
        self.assertTrue(reply['isError'])
        self.assertIn('a changé', reply['content'][0]['text'])
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM depenses'), 0)
        self.assertTrue((self.inbox / 'IMG_0001.png').exists())

    def test_a_failed_move_puts_the_others_back(self):
        self.drop('a.png', 1)
        self.drop('b.png', 2)
        self.ok('depense_preparer', self.expense('a.png'))
        second = self.ok('depense_preparer', self.expense('b.png', montant=20))['facture']
        # Something appears at the second destination after the recap: the whole lot is refused.
        taken = self.drive / 'DojoD' / '05 Administration' / 'Factures' / second.split('« DojoD/05 Administration/Factures/')[1].rstrip(' »')
        taken.parent.mkdir(exist_ok=True)
        taken.write_bytes(b'x')
        self.assertTrue(self.commit()['isError'])
        self.assertTrue((self.inbox / 'a.png').exists() and (self.inbox / 'b.png').exists())
        self.assertEqual(scalar(self.ledger, 'SELECT count(*) FROM depenses'), 0)

    def test_names_and_links_are_refused(self):
        for bad in ('../x.png', 'a/b.png', '.cache.png', 'notes.txt', ''):
            self.fails('depense_preparer', self.expense(bad), 'Nom de facture invalide')
        outside = self.folder / 'ailleurs'
        outside.mkdir()
        (outside / 'IMG_0001.png').write_bytes(png(1))
        self.inbox.rmdir()
        self.inbox.symlink_to(outside)
        self.assertTrue(self.call('factures_a_classer')['isError'])
        self.assertTrue(self.call('depense_preparer', self.expense('IMG_0001.png'))['isError'])

    def test_stored_path_shape(self):
        self.assertTrue(factures.valid_stored('2026/2026-10-06_45.99.jpg'))
        self.assertTrue(factures.valid_stored('2026/2026-10-06_45.99-2.jpg'))
        self.assertTrue(factures.valid_stored('2026/2026-10-06_canadian-tire_45.99.jpg'))  # filed before F4
        self.assertFalse(factures.valid_stored('2026/2026-10-06__45.99.jpg'))
        self.assertFalse(factures.valid_stored('2025/2026-10-06_canadian-tire_45.99.jpg'))
        self.assertFalse(factures.valid_stored('../2026-10-06_x_1.00.jpg'))



PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
       b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 72]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n')


def possede_gps(jpg):
    """Le pointeur GPS (0x8825) de l'IFD0, dans un Exif du JPG."""
    position = 2
    while jpg[position + 1] != 0xda:
        taille = int.from_bytes(jpg[position + 2:position + 4], 'big')
        charge = jpg[position + 4:position + 2 + taille]
        if jpg[position + 1] == 0xe1 and charge.startswith(b'Exif\0\0'):
            tiff = charge[6:]
            ordre = '<' if tiff[:2] == b'II' else '>'
            debut = struct.unpack_from(ordre + 'I', tiff, 4)[0]
            for i in range(struct.unpack_from(ordre + 'H', tiff, debut)[0]):
                if struct.unpack_from(ordre + 'H', tiff, debut + 2 + 12 * i)[0] == 0x8825:
                    return True
        position += 2 + taille
    return False


def segments(jpg):
    position, marqueurs = 2, []
    while jpg[position + 1] != 0xda:
        marqueurs.append(jpg[position + 1])
        position += 2 + int.from_bytes(jpg[position + 2:position + 4], 'big')
    return marqueurs


class TestDepot(unittest.TestCase):
    setUp = TestFactures.setUp
    tearDown = TestFactures.tearDown

    def sips_brut(self):
        import subprocess
        source = Path(__file__).with_name('references') / 'facture-fictive-gps.heic'
        sortie = self.folder / 'brut.jpg'
        subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', str(source), '--out', str(sortie)], capture_output=True, check=True, timeout=60)
        return sortie.read_bytes()

    vrai_jpeg = sips_brut

    def test_illisible_refuse(self):
        for data in (b'\xff\xd8\xff\xd9', b'%PDF-1.4\n%%EOF', b'\x89PNG\r\n\x1a\nfaux'):
            with self.assertRaises(factures.ToolError) as cm:
                factures.deposer(data)
            self.assertEqual(str(cm.exception), factures.UNREADABLE)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_collision_sans_ligne_refusee(self):
        from datetime import datetime
        from socle import journal_ecritures as journal
        with patch.object(factures, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 31, 12, 0, 0)
            factures.deposer(png(1))
            self.assertEqual(factures.deposer(png(2))['nom'], '2026-10-31_120000-2.png')
        self.assertEqual({r['resultat'] for r in journal.du_jour(date.today())}, {'enregistre'})

    def test_copies_anciennes_purgees(self):
        import os
        with patch.object(factures, '_temp', return_value=str(self.folder)):
            vieille, recente, autre = (self.folder / n for n in ('smith-depot-a', 'smith-depot-b', 'smith-facture-c'))
            for dossier in (vieille, recente, autre):
                dossier.mkdir()
            os.utime(vieille, (1, 1))
            os.utime(autre, (1, 1))
            factures.purge_deposit_copies()
            self.assertEqual((vieille.exists(), recente.exists(), autre.exists()), (False, True, True))

    def test_types_copies_journal_et_suite(self):
        from socle import journal_ecritures as journal
        before = self.ledger.read_bytes()
        for data, ext in ((png(1), '.png'), (PDF, '.pdf'), (self.vrai_jpeg(), '.jpg')):
            result = factures.deposer(data)
            path = self.inbox / result['nom']
            self.assertTrue(result['depose'])
            self.assertFalse(result['converti'])
            self.assertEqual(path.suffix, ext)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(factures.waiting(result['nom'])[0], result['nom'])
        self.assertEqual(before, self.ledger.read_bytes())
        self.assertEqual(journal.go_du_jour(date.today()), 0)
        rows = journal.du_jour(date.today())
        self.assertTrue(all(r['origine'] == 'app' and r['resultat'] == 'enregistre' for r in rows))
        self.assertTrue(factures.preview(name=next(self.inbox.glob('*.png')).name).startswith(b'\xff\xd8'))

    def test_refus_et_liens(self):
        for data in (b'', b'faux jpg', b'x' * (factures.MAX_BYTES + 1), b'0000ftypavif'):
            with self.assertRaises(factures.ToolError):
                factures.deposer(data)
        self.assertEqual(list(self.inbox.iterdir()), [])
        self.inbox.rmdir()
        self.inbox.symlink_to(self.folder)
        with self.assertRaises(factures.ToolError):
            factures.deposer(png(1))
        self.assertEqual(list(self.folder.glob('*.png')), [])

    def test_rangs_et_journal_avant_creation(self):
        from datetime import datetime
        from socle import journal_ecritures as journal
        real = journal.intention
        def intention(*args, **kwargs):
            self.assertFalse((self.inbox / args[3][0]['resume'].split('/')[-1]).exists())
            return real(*args, **kwargs)
        with patch.object(factures, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 31, 12, 0, 0)
            with patch.object(journal, 'intention', side_effect=intention):
                first = factures.deposer(png(1))
            results = [first] + [factures.deposer(png(i)) for i in range(2, 10)]
            self.assertEqual(results[1]['nom'], '2026-10-31_120000-2.png')
            with self.assertRaises(factures.ToolError):
                factures.deposer(png(10))
        self.assertEqual(len(list(self.inbox.iterdir())), 9)
        self.assertEqual((self.inbox / first['nom']).read_bytes(), png(1))

    def test_journal_et_ecriture_en_echec(self):
        from socle import journal_ecritures as journal
        with patch.object(journal, 'intention', side_effect=journal.JournalIndisponible):
            with self.assertRaises(factures.ToolError): factures.deposer(png(1))
        self.assertEqual(list(self.inbox.iterdir()), [])
        with patch.object(journal, 'intention', return_value='12345678'), patch.object(factures.os, 'fsync', side_effect=OSError):
            with self.assertRaises(factures.ToolError): factures.deposer(png(1))
        self.assertEqual(list(self.inbox.iterdir()), [])
        from types import SimpleNamespace
        from decimal import Decimal
        with self.assertRaises(journal.JournalIndisponible):
            journal.intention('intrus', None, SimpleNamespace(mode='go', raisons=[], montant=Decimal(0)), [])

    def test_segments_et_orientation(self):
        segment = lambda marker, payload: b'\xff' + bytes([marker]) + struct.pack('>H', len(payload) + 2) + payload
        exif = b'Exif\0\0II' + struct.pack('<HIHHHIHHI', 42, 8, 1, 0x112, 3, 1, 6, 0, 0)
        icc = segment(0xe2, b'ICC_PROFILE\0fictif')
        scan = segment(0xc0, b'\x08\x00\x01\x00\x01\x01\x01\x11\x00') + segment(0xda, b'\x01\x01\x00\x00\x3f\x00') + b'pixels\xff\x00\xff\xd0'
        jpg = b'\xff\xd8' + segment(0xe1, exif) + segment(0xe1, b'GPS fictif XMP') + icc + segment(0xeb, b'JP GPS C2PA') + segment(0xfe, b'GPS note') + segment(0xe0, b'JFXX GPS') + scan + segment(0xed, b'GPS IPTC') + b'\xff\xd9'
        clean = factures._without_gps(jpg)
        self.assertNotIn(b'GPS', clean)
        self.assertIn(icc, clean)
        self.assertEqual(clean.count(b'\xff\xe1'), 1)
        length = int.from_bytes(clean[4:6], 'big')
        self.assertEqual(factures._orientation(clean[6:4 + length]), 6)
        for orientation in (1, 2, 8, 9):
            payload = b'Exif\0\0MM' + struct.pack('>HIHHHIHHI', 42, 8, 1, 0x112, 3, 1, orientation, 0, 0)
            cleaned = factures._without_gps(b'\xff\xd8' + segment(0xe1, payload) + scan + b'\xff\xd9')
            self.assertEqual(cleaned.count(b'\xff\xe1'), int(2 <= orientation <= 8))
        self.assertEqual(clean[6:4 + length], exif)
        for bad in (b'', jpg[:-1], jpg + b'intrus', b'\xff\xd8\xff\xe1\x00\x01', b'\xff\xd8\xff\xd9'):
            with self.assertRaises(factures.ToolError): factures._without_gps(bad)
        self.assertEqual(factures._without_gps(b'\xff\xd8' + scan + b'\xff\xd9'), b'\xff\xd8' + scan + b'\xff\xd9')

    def test_conversion_refusee(self):
        import subprocess
        heic = Path(__file__).with_name('references').joinpath('facture-fictive-gps.heic').read_bytes()
        with patch.object(factures, 'DEPOT_PROFILE', self.folder / 'absent'):
            with self.assertRaises(factures.ToolError): factures.deposer(heic)
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('sips', 60)):
            with patch.object(factures.subprocess, 'run', side_effect=error):
                with self.assertRaises(factures.ToolError): factures.deposer(heic)
        with patch.object(factures.subprocess, 'run', return_value=subprocess.CompletedProcess([], 13, b'', b'secret')):
            with self.assertRaises(factures.ToolError) as cm: factures.deposer(heic)
            self.assertNotIn('secret', str(cm.exception))
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_conversion_commandes_dimensions_et_taille(self):
        import subprocess
        heic = b'0000ftypheic'
        jpg = b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00pixels\xff\xd9'
        commandes = []
        def run(cmd, **kw):
            commandes.append(cmd)
            self.assertEqual(cmd[:2], ['/usr/bin/sandbox-exec', '-D'])
            self.assertEqual(kw['timeout'], 60)
            self.assertNotIn('-Z', cmd)
            self.assertEqual(cmd[3:6], ['-f', str(factures.DEPOT_PROFILE), '/usr/bin/sips'])
            self.assertEqual(Path(cmd[2].removeprefix('TEMP=')), Path(cmd[-1]).parent.parent)
            if '--out' in cmd:
                self.assertIn('85', cmd)
                Path(cmd[-1]).write_bytes(jpg)
                return subprocess.CompletedProcess(cmd, 0, b'', b'')
            return subprocess.CompletedProcess(cmd, 0, b'pixelWidth: 800\npixelHeight: 600\n', b'')
        with patch.object(factures.subprocess, 'run', side_effect=run):
            result = factures.deposer(heic)
        self.assertTrue(result['converti'])
        self.assertEqual(len(commandes), 3)
        self.assertFalse(Path(commandes[1][-1]).exists())
        self.assertEqual((self.inbox / result['nom']).read_bytes(), jpg)
        def dimensions_changees(cmd, **kw):
            result = run(cmd, **kw)
            if '-g' in cmd and cmd[-1].endswith('.jpg'):
                result.stdout = b'pixelWidth: 600\npixelHeight: 800\n'
            return result
        with patch.object(factures.subprocess, 'run', side_effect=dimensions_changees):
            with self.assertRaises(factures.ToolError): factures.deposer(heic)
        jpg = b'\xff\xd8\xff' + b'x' * factures.MAX_BYTES
        with patch.object(factures.subprocess, 'run', side_effect=run):
            with self.assertRaises(factures.ToolError): factures.deposer(heic)
        self.assertEqual(len(list(self.inbox.iterdir())), 1)

    def test_heic_reel_fictif_sans_gps(self):
        heic = Path(__file__).with_name('references').joinpath('facture-fictive-gps.heic').read_bytes()
        result = factures.deposer(heic)
        self.assertTrue(result['converti'])
        jpg = (self.inbox / result['nom']).read_bytes()
        self.assertTrue(possede_gps(self.sips_brut()))  # sinon ce test ne prouverait rien
        self.assertFalse(possede_gps(jpg))
        self.assertEqual(factures._without_gps(jpg), jpg)
        self.assertEqual([m for m in segments(jpg) if 0xe0 <= m <= 0xef or m == 0xfe].count(0xe1), 1)
        self.assertEqual(factures._orientation(jpg[6:4 + int.from_bytes(jpg[4:6], 'big')]), 6)
        self.assertTrue(factures.preview(name=result['nom']).startswith(b'\xff\xd8'))


if __name__ == '__main__':
    unittest.main()
