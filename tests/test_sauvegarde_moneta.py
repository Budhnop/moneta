"""La nuit de Moneta (spec 013 de Smith, T063, R10, EF-015) : sa propre image chiffrée vers Drive et PRO-HD1, sur le moteur
socle.offsite_backup, avec les pièces; Smith garde dojo.db et le journal commun, sans pièces.

L'interface que T064 doit tenir, fixée par ce test (moneta.scripts.sauvegarde_quotidienne, comme celle de Smith) :
- KEYCHAIN_SERVICE = 'ca.dojod.moneta.sauvegarde', keychain_passphrase() : jamais la phrase de Smith (agent-smith-sauvegarde).
- KINDS : quotidiennes/moneta-…, mensuelles/moneta-mensuelle-…; REGISTRY private/sauvegarde-pieces.txt de Moneta.
- local_copies(today) : backups/database/daily/finances_<jour>.db, vérifiée, en 600.
- piece_sources() : les factures de Drive (DojoD/05 Administration/Factures) et imports/ de Moneta.
- stage(folder, data_home, chosen, note) : donnees/finances.db, csv/finances/, pieces/, MANIFESTE.txt; ni dojo.db ni journal.
- create_backup(destination, passphrase, *, data_home=None, kind, chosen, note), offsite(destination), to_disk(destination, disk,
  today), retention(today, destination), main() avec MONETA_SAUVEGARDE_DESTINATION et MONETA_SAUVEGARDE_DISQUE; journal
  private/sauvegarde-quotidienne.log (« OK hors Mac : », « OK disque : »).
- Le modèle de tâche de nuit outils/ca.dojod.moneta.sauvegarde.plist, non installé : 21 h 15, quinze minutes après Smith (21 h).
La nuit fictive complète lance aussi celle de Smith (MONETA_TESTS_SMITH), avec une phrase de passe fictive.
"""
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outils_tests import FOLDERS, SMITH
from socle import offsite_backup
from socle.migrations import migrate

ROOT = Path(__file__).resolve().parents[1]
PASSPHRASE = 'phrase-de-test-fictive'
SERVICE = 'ca.dojod.moneta.sauvegarde'
FACTURES = ('DojoD', '05 Administration', 'Factures')


def module():
    from moneta.scripts import sauvegarde_quotidienne
    return sauvegarde_quotidienne


class Base(unittest.TestCase):
    def setUp(self):
        self.nuit = module()
        self.temp = tempfile.TemporaryDirectory(prefix='moneta-nuit-')
        self.root = Path(self.temp.name).resolve()
        self.home, self.drive = self.root / 'moneta', self.root / 'drive'
        (self.home / 'database').mkdir(parents=True, mode=0o700)
        migrate(self.home / 'database' / 'finances.db', self.root / 'migration', FOLDERS['finances'])
        (self.home / 'database' / 'finances.db').chmod(0o600)
        self.receipts = self.drive.joinpath(*FACTURES)
        (self.receipts / 'à classer').mkdir(parents=True)
        (self.receipts / '2026').mkdir()
        (self.receipts / '2026' / '2026-10-06_fournisseur_50.00.pdf').write_bytes(b'%PDF facture 1')
        (self.home / 'imports' / 'releves').mkdir(parents=True)
        (self.home / 'imports' / 'releves' / 'releve-fictif.pdf').write_bytes(b'%PDF releve')
        (self.home / 'imports' / '.DS_Store').write_bytes(b'x')
        self.destination = self.drive / 'DojoD' / '05 Administration' / 'Sauvegardes'
        self.destination.mkdir(parents=True)
        self.disk = self.root / 'Volumes' / 'PRO-HD1' / '00_BackupSmith' / 'Moneta'
        self.disk.mkdir(parents=True)
        self.env = patch.dict(os.environ, {'MONETA_HOME': str(self.home), 'MONETA_DRIVE_DOSSIER': str(self.drive),
                                           'MONETA_SAUVEGARDE_DESTINATION': str(self.destination),
                                           'MONETA_SAUVEGARDE_DISQUE': str(self.disk)})
        self.env.start()
        # Les dossiers de test sont tous sur le disque du Mac : ici, ils comptent pour un autre disque.
        self.device = patch('socle.offsite_backup.same_device', return_value=False)
        self.device.start()

    def tearDown(self):
        self.device.stop()
        self.env.stop()
        self.temp.cleanup()

    def log(self):
        return self.nuit.log_path().read_text(encoding='utf-8')


class TestNoms(Base):
    def test_noms_de_moneta(self):
        self.assertEqual(self.nuit.KEYCHAIN_SERVICE, SERVICE)
        self.assertEqual(self.nuit.KINDS, {'quotidienne': ('quotidiennes', 'moneta-'),
                                           'mensuelle': ('mensuelles', 'moneta-mensuelle-')})
        self.assertEqual(self.nuit.log_path(), self.home / 'private' / 'sauvegarde-quotidienne.log')
        self.assertEqual(self.nuit.REGISTRY, 'sauvegarde-pieces.txt')

    def test_modele_de_tache_quinze_minutes_apres_smith(self):
        modele = ROOT / 'outils' / 'ca.dojod.moneta.sauvegarde.plist'
        reglage = plistlib.loads(modele.read_bytes())
        self.assertEqual(reglage['Label'], 'ca.dojod.moneta.sauvegarde')
        programme = Path(reglage['ProgramArguments'][0])
        self.assertTrue(programme.is_absolute())
        self.assertEqual(programme.parts[-3:], ('moneta', 'bin', 'moneta-sauvegarde-quotidienne'))
        self.assertTrue(os.access(ROOT / 'bin' / 'moneta-sauvegarde-quotidienne', os.X_OK))
        self.assertEqual(reglage['StartCalendarInterval'], {'Hour': 21, 'Minute': 15})  # Smith : 21 h
        self.assertLessEqual({'MONETA_SAUVEGARDE_DESTINATION', 'MONETA_SAUVEGARDE_DISQUE'}, set(reglage['EnvironmentVariables']))


class TestPhraseDePasse(Base):
    def test_absente_refus_clair_jamais_celle_de_smith(self):
        services, vrai = [], subprocess.run

        def security(commande, *args, **kwargs):
            if commande[:2] != ['/usr/bin/security', 'find-generic-password']:
                return vrai(commande, *args, **kwargs)
            services.append(commande[commande.index('-s') + 1])
            return subprocess.CompletedProcess(commande, 44, '', 'introuvable')
        with patch('socle.offsite_backup.subprocess.run', side_effect=security):
            with self.assertRaisesRegex(RuntimeError, SERVICE):
                self.nuit.keychain_passphrase()
            self.assertEqual(self.nuit.main(), 1)
        self.assertEqual(set(services), {SERVICE})
        self.assertRegex(self.log(), f'ERREUR hors Mac : .*{SERVICE}')
        self.assertEqual(list(self.destination.rglob('*.dmg')), [])
        self.assertFalse((self.home / 'private' / self.nuit.REGISTRY).exists())


class TestCopiesLocales(Base):
    def test_une_copie_verifiee_de_finances_par_jour(self):
        faites = self.nuit.local_copies(date(2026, 11, 3))
        copie = self.home / 'backups' / 'database' / 'daily' / 'finances_2026-11-03.db'
        self.assertEqual(faites, [copie])
        self.assertEqual(copie.stat().st_mode & 0o777, 0o600)


class TestPieces(Base):
    def test_sources_et_fichiers_ordinaires_seulement(self):
        self.assertEqual(self.nuit.piece_sources(), {'factures': self.receipts, 'imports': self.home / 'imports'})
        (self.receipts / '2026' / 'lien.pdf').symlink_to(self.receipts / '2026' / '2026-10-06_fournisseur_50.00.pdf')
        (self.home / 'imports' / 'ailleurs').symlink_to(self.root, target_is_directory=True)
        trouve = offsite_backup.pieces(self.nuit.piece_sources())
        self.assertEqual(sorted(trouve), ['factures/2026/2026-10-06_fournisseur_50.00.pdf', 'imports/releves/releve-fictif.pdf'])


class TestContenu(Base):
    def test_base_csv_pieces_et_manifeste_sans_rien_de_smith(self):
        (self.root / 'dojo.db').write_bytes(b'dojo')
        contenu = self.root / 'contenu'
        contenu.mkdir()
        choisies = offsite_backup.pieces(self.nuit.piece_sources())
        fichiers = self.nuit.stage(contenu, self.home, choisies, ' (toutes)')
        self.assertIn('donnees/finances.db', fichiers)
        self.assertEqual((contenu / 'donnees' / 'finances.db').stat().st_mode & 0o777, 0o600)
        self.assertTrue(list((contenu / 'csv' / 'finances').glob('*.csv')))
        self.assertEqual(sorted(k for k in fichiers if k.startswith('pieces/')),
                         ['pieces/factures/2026/2026-10-06_fournisseur_50.00.pdf', 'pieces/imports/releves/releve-fictif.pdf'])
        manifeste = (contenu / 'MANIFESTE.txt').read_text(encoding='utf-8')
        self.assertIn('Moneta', manifeste)
        self.assertIn('Migrations de finances.db : 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008', manifeste)
        self.assertIn('Pièces (factures, relevés) : 2 (toutes)', manifeste)
        self.assertFalse((contenu / 'donnees' / 'dojo.db').exists())
        self.assertFalse((contenu / 'journal-ecritures').exists())


class TestImages(Base):
    """create_backup est remplacé par un faux qui garde ce que chaque image tiendrait."""

    def setUp(self):
        super().setUp()
        self.faites, self.today = [], date(2026, 11, 3)
        self.faux = patch.object(self.nuit, 'create_backup', self.fausse_image)
        self.faux.start()
        self.cle = patch.object(self.nuit, 'keychain_passphrase', return_value=PASSPHRASE)
        self.cle.start()

    def tearDown(self):
        self.cle.stop()
        self.faux.stop()
        super().tearDown()

    def fausse_image(self, destination, passphrase, *, kind, chosen, note):
        dossier, prefixe = self.nuit.KINDS[kind]
        image = Path(destination) / dossier / f'{prefixe}{self.today}_211500.dmg'
        image.parent.mkdir(exist_ok=True)
        image.write_bytes(b'image')
        self.faites.append((kind, sorted(chosen), note))
        return image, 5

    def nuit_du(self, jour):
        self.today = jour
        with patch.object(self.nuit, 'date') as faux:
            faux.today.return_value = jour
            faux.fromisoformat = date.fromisoformat
            self.nuit.offsite(str(self.destination))

    def test_premiere_du_mois_complete_puis_nouvelles_seulement(self):
        self.nuit_du(date(2026, 11, 1))
        self.nuit_du(date(2026, 11, 2))
        (self.receipts / '2026' / '2026-11-02_fournisseur_12.00.pdf').write_bytes(b'%PDF facture 2')
        self.nuit_du(date(2026, 11, 3))
        tout = ['factures/2026/2026-10-06_fournisseur_50.00.pdf', 'imports/releves/releve-fictif.pdf']
        self.assertEqual(self.faites, [('mensuelle', tout, ' (toutes)'),
                                       ('quotidienne', [], ' (nouvelles depuis la dernière image)'),
                                       ('quotidienne', ['factures/2026/2026-11-02_fournisseur_12.00.pdf'],
                                        ' (nouvelles depuis la dernière image)')])
        self.assertIn('OK hors Mac : mensuelles/moneta-mensuelle-2026-11-01_211500.dmg (5 fichiers vérifiés, 2 pièce(s))',
                      self.log())
        # Une pièce changée depuis son image est reprise; un registre perdu les reprend toutes, sans mal.
        (self.home / 'imports' / 'releves' / 'releve-fictif.pdf').write_bytes(b'%PDF releve corrige')
        self.nuit_du(date(2026, 11, 4))
        self.assertEqual(self.faites[-1][1], ['imports/releves/releve-fictif.pdf'])
        (self.home / 'private' / self.nuit.REGISTRY).unlink()
        self.nuit_du(date(2026, 11, 5))
        self.assertEqual(len(self.faites[-1][1]), 3)

    def test_pieces_illisibles_jamais_de_mensuelle(self):
        with patch.object(self.nuit, 'pieces', side_effect=PermissionError('Drive fermé')):
            self.nuit_du(date(2026, 12, 1))
        self.assertEqual(self.faites, [('quotidienne', [], ' (pièces illisibles ce soir)')])
        self.assertIn('la mensuelle attend', self.log())
        self.assertFalse((self.home / 'private' / self.nuit.REGISTRY).exists())
        self.nuit_du(date(2026, 12, 2))
        self.assertEqual(self.faites[-1][0], 'mensuelle')


class TestRetention(Base):
    def image(self, jour, mensuelle=False, notee=True, dossier=None):
        dossier = dossier or self.destination
        sous = dossier / ('mensuelles' if mensuelle else 'quotidiennes')
        sous.mkdir(exist_ok=True)
        nom = f"moneta-{'mensuelle-' if mensuelle else ''}{jour}_211500.dmg"
        (sous / nom).write_bytes(f'image {jour}'.encode())
        if notee:
            self.nuit.log(f'OK hors Mac : {sous.name}/{nom} (5 fichiers vérifiés, 0 pièce(s))')
        return nom

    def noms(self, sous, dossier=None):
        chemin = (dossier or self.destination) / sous
        return sorted(p.name for p in chemin.iterdir()) if chemin.is_dir() else []

    def test_quotidiennes_d_avant_la_mensuelle_et_images_de_smith_intouchees(self):
        today = date(2026, 11, 3)
        for recul in (40, 10, 1):
            self.image(today - timedelta(days=recul))
        (self.destination / 'quotidiennes' / 'agent-smith-2026-09-24_210000.dmg').write_bytes(b'image de Smith')
        for jour in (date(2025, 6, 1), date(2026, 1, 1), date(2026, 11, 1)):
            self.image(jour, mensuelle=True)
        self.nuit.retention(today, str(self.destination))
        self.assertEqual(self.noms('quotidiennes'), ['agent-smith-2026-09-24_210000.dmg', 'moneta-2026-11-02_211500.dmg'])
        self.assertEqual(self.noms('mensuelles'), ['moneta-mensuelle-2026-01-01_211500.dmg',
                                                   'moneta-mensuelle-2026-11-01_211500.dmg'])

    def test_copies_en_clair_gardees_sans_image_verifiee(self):
        vieille = self.home / 'backups' / 'database' / 'daily' / 'finances_2026-09-01.db'
        vieille.parent.mkdir(parents=True)
        vieille.write_bytes(b'copie')
        self.nuit.retention(date(2026, 11, 3), str(self.destination))
        self.assertTrue(vieille.exists())
        self.assertIn('ATTENTION rétention', self.log())
        self.image(date(2026, 11, 1), mensuelle=True)
        self.nuit.retention(date(2026, 11, 3), str(self.destination))
        self.assertFalse(vieille.exists())


class TestDisque(Base):
    """copy_to_disk est remplacé par un faux qui copie les octets; la vraie est essayée plus bas avec hdiutil."""

    def setUp(self):
        super().setUp()
        self.copiees = []
        self.faux = patch.object(self.nuit, 'copy_to_disk', self.fausse_copie)
        self.faux.start()
        self.cle = patch.object(self.nuit, 'keychain_passphrase', return_value=PASSPHRASE)
        self.cle.start()

    def tearDown(self):
        self.cle.stop()
        self.faux.stop()
        super().tearDown()

    def fausse_copie(self, image, dossier, passphrase, hdiutil='/usr/bin/hdiutil'):
        dossier.mkdir(exist_ok=True)
        shutil.copyfile(image, dossier / image.name)
        self.copiees.append(image.name)
        return 5

    image = TestRetention.image

    def test_disque_absent_puis_rattrapage_dans_l_ordre(self):
        jours = [date(2026, 10, 30), date(2026, 10, 31)]
        for jour in jours:
            self.image(jour)
        absent = self.root / 'Volumes' / 'ABSENT' / 'Moneta'
        self.nuit.to_disk(self.destination, absent, date(2026, 10, 31))
        self.assertIn('ATTENTION disque : absent', self.log())
        self.assertIn('2 image(s) en attente', self.log())
        self.assertFalse(absent.parent.exists())
        self.nuit.to_disk(self.destination, self.disk, date(2026, 10, 31))
        self.assertEqual(self.copiees, [f'moneta-{j}_211500.dmg' for j in jours])
        self.assertIn('OK disque : quotidiennes/moneta-2026-10-30_211500.dmg (5 fichiers vérifiés)', self.log())
        self.nuit.to_disk(self.destination, self.disk, date(2026, 10, 31))
        self.assertIn('OK disque : à jour (2 image(s) déjà sur le disque)', self.log())

    def test_main_disque_absent_reste_vert(self):
        os.environ['MONETA_SAUVEGARDE_DISQUE'] = str(self.root / 'Volumes' / 'ABSENT' / 'Moneta')
        with patch.object(self.nuit, 'offsite'), patch.object(self.nuit, 'retention'):
            self.assertEqual(self.nuit.main(), 0)
        self.assertIn('ATTENTION disque : absent', self.log())
        self.assertNotIn('ERREUR', self.log())

    def test_main_sans_destination_est_une_erreur(self):
        del os.environ['MONETA_SAUVEGARDE_DESTINATION']
        with patch.object(self.nuit, 'retention'):
            self.assertEqual(self.nuit.main(), 1)
        self.assertIn('ERREUR hors Mac : MONETA_SAUVEGARDE_DESTINATION absente', self.log())


class TestErreurDisqueLisible(Base):
    """Spec 018, T029 : un refus du système de fichiers du disque (Errno 45...) est écrit avec sa cause, pas seulement « ERREUR disque »."""

    def test_oserror_du_disque_porte_sa_cause_au_journal(self):
        nom = TestRetention.image(self, date(2026, 10, 31))
        refus = OSError(45, 'Operation not supported')
        with patch('socle.offsite_backup.private_copy', side_effect=refus), \
                patch.object(self.nuit, 'keychain_passphrase', return_value=PASSPHRASE), \
                patch.object(self.nuit, 'offsite'), patch.object(self.nuit, 'retention'):
            self.assertEqual(self.nuit.main(), 1)
        ligne = next(l for l in self.log().splitlines() if 'ERREUR disque' in l)
        self.assertIn('opération refusée par la destination (errno 45, Operation not supported)', ligne)
        self.assertIn(nom, ligne)
        self.assertEqual(list(self.disk.rglob('*.dmg')) + list(self.disk.rglob('.partiel-*')), [])


def smith_nuit(smith_home, drive, destination, disque):
    """La nuit de Smith, lancée par son propre code (MONETA_TESTS_SMITH) sur ses données fictives, phrase de passe fictive."""
    code = ('import sys\nfrom unittest.mock import patch\nfrom scripts import sauvegarde_quotidienne as d\n'
            f'with patch.object(d, "keychain_passphrase", return_value={PASSPHRASE!r}), '
            'patch("socle.offsite_backup.same_device", return_value=False):\n    sys.exit(d.main())\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('UGOD_', 'MONETA_'))}
    env.update(UGOD_HOME=str(smith_home), UGOD_DRIVE_DOSSIER=str(drive), UGOD_SAUVEGARDE_DESTINATION=str(destination),
               UGOD_SAUVEGARDE_DISQUE=str(disque), PYTHONPATH=str(SMITH / 'assistant'), PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([str(SMITH / '.venv' / 'bin' / 'python'), '-c', code], cwd=SMITH / 'assistant', env=env,
                          capture_output=True, text=True, timeout=300)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('hdiutil'), 'hdiutil requis')
class TestNuitFictive(Base):
    """Une nuit complète : Smith à 21 h, puis Moneta; deux images vérifiées sur Drive et deux sur le PRO-HD1 fictifs."""

    def setUp(self):
        super().setUp()
        from outils_tests import seed_database
        self.smith = self.root / 'smith'
        (self.smith / 'database').mkdir(parents=True, mode=0o700)
        shutil.move(seed_database(self.root / 'graine'), self.smith / 'database' / 'dojo.db')
        journal = self.smith / 'journal-ecritures'  # le journal commun, à sa place des données fictives (R2)
        journal.mkdir(mode=0o700)
        (journal / '2026-11.jsonl').write_text('{"id": "0123abcd", "agent": "moneta"}\n', encoding='utf-8')
        self.disque_smith = self.disk.parent / 'Smith'
        self.disque_smith.mkdir()

    def images(self, dossier, prefixe):
        return sorted(p for p in Path(dossier).rglob(f'{prefixe}*.dmg'))

    def contenu(self, image):
        with offsite_backup.mounted(image, PASSPHRASE, 'hdiutil') as point:
            return {p.relative_to(point).as_posix() for p in point.rglob('*') if p.is_file()}

    def test_deux_images_pieces_chez_moneta_seulement(self):
        fait = smith_nuit(self.smith, self.drive, self.destination, self.disque_smith)
        self.assertEqual(fait.returncode, 0, fait.stdout + fait.stderr)
        with patch.object(self.nuit, 'keychain_passphrase', return_value=PASSPHRASE):
            self.assertEqual(self.nuit.main(), 0, self.log())
        smith_images, moneta_images = self.images(self.destination, 'agent-smith-'), self.images(self.destination, 'moneta-')
        self.assertEqual((len(smith_images), len(moneta_images)), (1, 1))
        self.assertEqual([p.name for p in self.images(self.disque_smith, 'agent-smith-')], [smith_images[0].name])
        self.assertEqual([p.name for p in self.images(self.disk, 'moneta-')], [moneta_images[0].name])
        self.assertIn('OK disque : ', (self.smith / 'private' / 'sauvegarde-quotidienne.log').read_text(encoding='utf-8'))
        self.assertIn('OK disque : ', self.log())

        chez_smith, chez_moneta = self.contenu(smith_images[0]), self.contenu(moneta_images[0])
        self.assertIn('donnees/dojo.db', chez_smith)
        self.assertIn('journal-ecritures/2026-11.jsonl', chez_smith)
        self.assertFalse([f for f in chez_smith if f.startswith('pieces/') or 'finances' in f], chez_smith)
        self.assertIn('donnees/finances.db', chez_moneta)
        self.assertFalse([f for f in chez_moneta if 'dojo' in f or f.startswith('journal-ecritures/')], chez_moneta)
        # Chaque pièce dans une seule image, celle de Moneta : aucune sautée ni en double.
        pieces = sorted(f for f in chez_smith | chez_moneta if f.startswith('pieces/'))
        self.assertEqual(pieces, ['pieces/factures/2026/2026-10-06_fournisseur_50.00.pdf', 'pieces/imports/releves/releve-fictif.pdf'])
        self.assertTrue((self.home / 'private' / self.nuit.REGISTRY).is_file())
        self.assertFalse((self.smith / 'private' / 'sauvegarde-pieces.txt').exists())


if __name__ == '__main__':
    unittest.main()
