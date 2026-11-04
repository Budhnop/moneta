"""bin/moneta-demenager et moneta.scripts.demenager (spec 013 de Smith, T061, récit 6, contrat demenagement) sur des données fictives.

Chaque test part d'un donnees/ fictif complet, fait ici dans un dossier temporaire hors de ~/_PersonalAgents : Smith avec sa base
Finances migrée, ses copies, ses relevés, ses rapports, son registre des pièces, son lecteur compilé et son journal, plus ce qui ne
bouge jamais (dojo.db, exports du Dojo, copies de lecture). Les données réelles ne sont jamais lues.

L'interface que T062 doit tenir, fixée par ce test :
- inventaire(donnees) : [{'de', 'vers', 'taille', 'sha256'}], chemins relatifs à donnees/, sans contenu; le lecteur
  smith/apps/agent-smith-releve n'y est jamais.
- appliquer(donnees, sondes, go, crochet=None) et retour(donnees, sondes, go, crochet=None) : go(recapitulatif) rend le mot tapé
  par Ugo (« déménager », « retour »); Refus avant tout changement; Bloque(chemins) quand une remise en place échoue.
- Sondes(processus, lot_finances, uid) : les processus en cours (libellés), l'âge en secondes de chaque étape du lot Finances,
  le propriétaire attendu. processus_en_cours(lignes) lit les lignes de `ps` et rend les libellés.
- crochet(etape) est appelé avant chaque étape : sauvegarde, copie, empreintes, bascule (une fois par renommage), note, et
  remise (avant chaque renommage défait). Une exception du crochet est un échec injecté.
- repeter(donnees) : aller, écriture fictive, retour, aller, et chaque refus, sur un dossier fictif seulement.
La sauvegarde d'avant le retour va dans moneta/backups/database/avant-retour/, comme celle de l'aller dans avant-moneta/.
"""
from contextlib import closing
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outils_tests import FOLDERS, SMITH
from socle.migrations import migrate

ROOT = Path(__file__).resolve().parents[1]
REEL = Path.home() / '_PersonalAgents' / 'donnees'
MOT_ALLER, MOT_RETOUR = 'déménager', 'retour'
ETAPES = {'sauvegarde', 'copie', 'empreintes', 'bascule', 'note', 'remise'}
# Une ligne du journal commun écrite par Smith 1.1.0 avant le déménagement (R2).
LIGNE = {'id': 'c0ffee11', 'heure': '2026-11-02T09:00:00', 'type': 'intention', 'agent': 'smith', 'outil': 'WR_SAVE_DOJO_BD',
         'lot': 'dojo', 'mode': 'go', 'raisons': [], 'etapes': [], 'montant': '0.00', 'origine': 'desktop'}

# Ce qui part de donnees/smith/ (data-model, Inventaire du déménagement), vers donnees/.
BOUGE = {
    'smith/database/finances.db': 'moneta/database/finances.db',
    'smith/backups/database/smith-finances/finances-2026-10-30.db': 'moneta/backups/database/moneta/finances-2026-10-30.db',
    'smith/backups/database/daily/finances_2026-11-01.db': 'moneta/backups/database/daily/finances_2026-11-01.db',
    'smith/imports/releve-2026-10.pdf': 'moneta/imports/releve-2026-10.pdf',
    'smith/exports/rapport-finances-2025.html': 'moneta/exports/rapport-finances-2025.html',
    'smith/private/sauvegarde-pieces.txt': 'moneta/private/sauvegarde-pieces.txt',
    'smith/journal-ecritures/2026-10.jsonl': 'journal-ecritures/2026-10.jsonl',
    'smith/journal-ecritures/2026-11.jsonl': 'journal-ecritures/2026-11.jsonl',
}
# Ce qui reste où il est, quoi qu'il arrive.
RESTE = (
    'smith/database/dojo.db',
    'smith/backups/database/daily/dojo_2026-11-01.db',
    'smith/backups/database/smith/dojo-2026-10-30.db',
    'smith/backups/database/mcp-lecture-finances/lecture.db',
    'smith/exports/export-2026-10-01_120000/membres.csv',
    'smith/exports/cartes-2026-10.pdf',
    'smith/apps/agent-smith-releve',
    'smith/private/sauvegarde-quotidienne.log',
)


def sha(chemin):
    return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()


def ecrire(chemin, contenu):
    chemin.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    chemin.write_bytes(contenu)
    chemin.chmod(0o600)


def construire(donnees, migrations=FOLDERS['finances']):
    """Un donnees/ fictif complet; migrations : le dossier des migrations Finances appliquées à la base."""
    donnees = Path(donnees)
    smith = donnees / 'smith'
    base = smith / 'database' / 'finances.db'
    base.parent.mkdir(parents=True, mode=0o700)
    with tempfile.TemporaryDirectory() as copies:
        migrate(base, Path(copies), migrations)
    with closing(sqlite3.connect(base)) as con, con:
        con.execute("INSERT INTO audit_log (action, resultat) VALUES ('essai fictif', 'ok')")
    base.chmod(0o600)
    for relatif in BOUGE:
        if relatif.endswith('.db') and relatif != 'smith/database/finances.db':
            ecrire(donnees / relatif, base.read_bytes())
    ecrire(smith / 'database' / 'dojo.db', b'SQLite format 3\x00 dojo fictif')
    ecrire(smith / 'imports' / 'releve-2026-10.pdf', b'%PDF-1.4 releve fictif')
    ecrire(smith / 'exports' / 'rapport-finances-2025.html', '<html>Rapport fictif 2025</html>'.encode())
    ecrire(smith / 'private' / 'sauvegarde-pieces.txt', b'imports/releve-2026-10.pdf\n')
    for mois in ('2026-10', '2026-11'):
        ligne = {'id': f'{mois}-1', 'type': 'resultat', 'etat': 'ok', 'quand': f'{mois}-01T10:00:00'}
        ecrire(smith / 'journal-ecritures' / f'{mois}.jsonl', (json.dumps(ligne) + '\n').encode())
    for relatif in RESTE:
        if not (donnees / relatif).exists():
            ecrire(donnees / relatif, f'fictif : {relatif}'.encode())
    (smith / 'apps' / 'agent-smith-releve').chmod(0o700)
    for dossier, _, _ in os.walk(donnees):
        Path(dossier).chmod(0o700)
    return donnees


def contenu(base):
    with closing(sqlite3.connect(f'file:{base}?mode=ro', uri=True)) as con:
        return list(con.iterdump())


def ecriture_fictive(base, action='écrit chez Moneta'):
    with closing(sqlite3.connect(base)) as con, con:
        con.execute('INSERT INTO audit_log (action, resultat) VALUES (?, ?)', (action, 'ok'))


def actions(base):
    with closing(sqlite3.connect(f'file:{base}?mode=ro', uri=True)) as con:
        return [a for (a,) in con.execute('SELECT action FROM audit_log ORDER BY id')]


def module():
    from moneta.scripts import demenager
    return demenager


class Base(unittest.TestCase):
    def setUp(self):
        self.d = module()
        self.monde = Path(tempfile.mkdtemp(prefix='moneta-demenager-'))
        self.addCleanup(shutil.rmtree, self.monde, True)
        self.modele = construire(self.monde / 'modele')
        self.donnees = self.copie()
        self.empreintes = {relatif: sha(self.donnees / relatif) for relatif in BOUGE}
        self.restes = {relatif: sha(self.donnees / relatif) for relatif in RESTE}

    def copie(self, nom='donnees'):
        cible = self.monde / nom
        shutil.rmtree(cible, ignore_errors=True)
        shutil.copytree(self.modele, cible, symlinks=True)
        return cible

    def sondes(self, **changes):
        valeurs = dict(processus=lambda: [], lot_finances=lambda: [], uid=os.getuid())
        valeurs.update(changes)
        return self.d.Sondes(**valeurs)

    def aller(self, donnees=None, sondes=None, crochet=None, mot=MOT_ALLER):
        vus = []

        def go(recapitulatif):
            vus.append(recapitulatif)
            return mot
        resultat = self.d.appliquer(donnees or self.donnees, sondes or self.sondes(), go, crochet=crochet)
        self.assertEqual(len(vus), 1, 'un seul récapitulatif, avant le go')
        return resultat

    def retour(self, donnees=None, sondes=None, crochet=None, mot=MOT_RETOUR):
        return self.d.retour(donnees or self.donnees, sondes or self.sondes(), lambda recapitulatif: mot, crochet=crochet)

    def arbre(self, donnees=None):
        """{chemin relatif: (sha256, mode)} de tout donnees/, hors des sauvegardes faites par le déménagement."""
        donnees = donnees or self.donnees
        trouve = {}
        for dossier, dossiers, fichiers in os.walk(donnees):
            dossiers[:] = [n for n in dossiers if n not in ('avant-moneta', 'avant-retour')]
            for nom in fichiers:
                chemin = Path(dossier) / nom
                trouve[chemin.relative_to(donnees).as_posix()] = (sha(chemin), stat.S_IMODE(chemin.lstat().st_mode))
        return trouve

    def originaux(self, donnees=None):
        """Les empreintes des fichiers gardés dans smith/private/avant-moneta/, avec leur mode."""
        dossier = (donnees or self.donnees) / 'smith' / 'private' / 'avant-moneta'
        return [(sha(p), stat.S_IMODE(p.stat().st_mode)) for p in sorted(dossier.rglob('*')) if p.is_file()]

    def assertAvant(self, donnees=None, empreintes=None):
        donnees, empreintes = donnees or self.donnees, empreintes or self.empreintes
        for de, vers in BOUGE.items():
            self.assertEqual(sha(donnees / de), empreintes[de], de)
            self.assertFalse((donnees / vers).exists(), vers)
        self.assertFalse((donnees / 'moneta' / '.passage').exists())
        self.assertRestes(donnees)

    def assertFait(self, donnees=None, empreintes=None):
        donnees, empreintes = donnees or self.donnees, empreintes or self.empreintes
        for de, vers in BOUGE.items():
            self.assertFalse((donnees / de).exists(), de)
            self.assertEqual(sha(donnees / vers), empreintes[de], vers)
            self.assertEqual(stat.S_IMODE((donnees / vers).stat().st_mode), 0o600, vers)
        for dossier in {(donnees / vers).parent for vers in BOUGE.values()}:
            self.assertEqual(stat.S_IMODE(dossier.stat().st_mode), 0o700, dossier)
        self.assertFalse((donnees / 'moneta' / '.passage').exists())
        self.assertTrue(set(empreintes.values()) <= {s for s, _ in self.originaux(donnees)})
        self.assertTrue((donnees / 'moneta' / 'private' / 'demenagement.json').is_file())
        self.assertRestes(donnees)

    def assertRestes(self, donnees):
        for relatif, empreinte in self.restes.items():
            self.assertEqual(sha(donnees / relatif), empreinte, relatif)
        self.assertFalse((donnees / 'moneta' / 'apps' / 'agent-smith-releve').exists())


class TestInventaire(Base):
    def test_ce_qui_bouge_et_rien_d_autre(self):
        trouve = self.d.inventaire(self.donnees)
        self.assertEqual({(f['de'], f['vers']) for f in trouve}, set(BOUGE.items()))
        for fichier in trouve:
            self.assertEqual(set(fichier), {'de', 'vers', 'taille', 'sha256'})
            self.assertEqual(fichier['sha256'], self.empreintes[fichier['de']])
            self.assertEqual(fichier['taille'], (self.donnees / fichier['de']).stat().st_size)

    def test_sans_contenu(self):
        # --inventaire : chemins, tailles et empreintes, jamais une ligne des fichiers; rien ne change.
        avant = self.arbre()
        fait = lancer('--inventaire', self.donnees)
        self.assertEqual(fait.returncode, 0, fait.stdout + fait.stderr)
        for relatif in BOUGE:
            self.assertIn(relatif, fait.stdout)
            self.assertIn(self.empreintes[relatif], fait.stdout)
        for secret in ('Rapport fictif', 'releve fictif', 'essai fictif', 'dojo fictif', '"etat"'):
            self.assertNotIn(secret, fait.stdout + fait.stderr)
        self.assertNotIn('agent-smith-releve', fait.stdout)
        self.assertEqual(self.arbre(), avant)
        self.assertAvant()


class TestAllerRetour(Base):
    def test_aller(self):
        avant = contenu(self.donnees / 'smith' / 'database' / 'finances.db')
        self.aller()
        self.assertFait()
        # Sauvegarde vérifiée de finances.db avant tout, dans smith/backups/database/avant-moneta/.
        copies = list((self.donnees / 'smith' / 'backups' / 'database' / 'avant-moneta').glob('*.db'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(contenu(copies[0]), avant)
        self.assertEqual(stat.S_IMODE(copies[0].stat().st_mode), 0o600)
        # Originaux gardés en 600.
        self.assertEqual({m for _, m in self.originaux()}, {0o600})
        # La note : date, commits, chaque empreinte; en 600.
        note_chemin = self.donnees / 'moneta' / 'private' / 'demenagement.json'
        self.assertEqual(stat.S_IMODE(note_chemin.stat().st_mode), 0o600)
        note = json.loads(note_chemin.read_text(encoding='utf-8'))
        self.assertLessEqual({'date', 'commit_smith', 'commit_moneta', 'empreintes'}, set(note))
        self.assertEqual(note['empreintes'], {vers: self.empreintes[de] for de, vers in BOUGE.items()})

    def test_aller_retour_aller_empreintes_identiques(self):
        self.aller()
        self.assertFait()
        self.retour()
        self.assertAvant()
        self.aller()
        self.assertFait()
        # Les originaux du premier aller restent tels quels.
        gardes = [s for s, _ in self.originaux()]
        for empreinte in self.empreintes.values():
            self.assertGreaterEqual(gardes.count(empreinte), 2)

    def test_l_ecriture_faite_chez_moneta_revient(self):
        self.aller()
        ecriture_fictive(self.donnees / 'moneta' / 'database' / 'finances.db')
        with (self.donnees / 'journal-ecritures' / '2026-11.jsonl').open('a', encoding='utf-8') as journal:
            journal.write(json.dumps({'id': 'm-1', 'agent': 'moneta', 'type': 'resultat', 'etat': 'ok'}) + '\n')
        apres = {de: sha(self.donnees / vers) for de, vers in BOUGE.items()}
        self.retour()
        self.assertAvant(empreintes=apres)
        self.assertEqual(actions(self.donnees / 'smith' / 'database' / 'finances.db')[-1], 'écrit chez Moneta')
        self.assertIn('"agent": "moneta"', (self.donnees / 'smith' / 'journal-ecritures' / '2026-11.jsonl').read_text())
        # Le retour fait sa propre sauvegarde vérifiée; les originaux d'avant-moneta n'ont pas changé.
        self.assertEqual(len(list((self.donnees / 'moneta' / 'backups' / 'database' / 'avant-retour').glob('*.db'))), 1)
        self.assertEqual(sorted(s for s, _ in self.originaux()), sorted(self.empreintes.values()))
        self.aller()
        self.assertFait(empreintes=apres)
        self.assertEqual(actions(self.donnees / 'moneta' / 'database' / 'finances.db')[-1], 'écrit chez Moneta')


class TestRefus(Base):
    """Chaque refus du contrat, avant tout changement : l'arbre entier est identique, sauvegarde comprise."""

    def refuse(self, motif, faire=None, etat=None):
        avant = self.arbre()
        sauvegardes = sorted(p for p in self.donnees.rglob('*') if 'avant-' in p.as_posix())
        with self.assertRaisesRegex(self.d.Refus, motif):
            (faire or self.aller)()
        self.assertEqual(self.arbre(), avant)
        self.assertEqual(sorted(p for p in self.donnees.rglob('*') if 'avant-' in p.as_posix()), sauvegardes)

    def test_processus_en_cours(self):
        for libelle in ('Claude Desktop', 'app Smith', 'serveur Smith', 'serveur Moneta', 'tâche de nuit'):
            with self.subTest(libelle):
                self.refuse(libelle, lambda: self.aller(sondes=self.sondes(processus=lambda: [libelle])))

    def test_lot_finances(self):
        self.refuse('lot', lambda: self.aller(sondes=self.sondes(lot_finances=lambda: [3 * 3600])))
        self.refuse('30 minutes', lambda: self.aller(sondes=self.sondes(lot_finances=lambda: [60])))

    def test_migration_en_attente(self):
        partielles = self.monde / 'migrations-0007'
        partielles.mkdir()
        for chemin in sorted(FOLDERS['finances'].glob('000[1-7]_*.py')):
            shutil.copy(chemin, partielles)
        self.modele = construire(self.monde / 'modele-0007', partielles)
        self.donnees = self.copie('donnees-0007')
        self.refuse('[Mm]igration')

    def test_cible_deja_presente_ou_absente(self):
        ecrire(self.donnees / 'moneta' / 'database' / 'finances.db', b'deja la')
        self.refuse('finances.db')
        (self.donnees / 'moneta' / 'database' / 'finances.db').unlink()
        # Retour sans aller : la base de Moneta est absente.
        self.refuse('finances.db', self.retour)

    def test_lien(self):
        (self.donnees / 'smith' / 'imports' / 'lien.pdf').symlink_to(self.donnees / 'smith' / 'database' / 'dojo.db')
        self.refuse('lien')

    def test_proprietaire(self):
        self.refuse('propriétaire', lambda: self.aller(sondes=self.sondes(uid=os.getuid() + 1)))

    def test_sans_le_mot_exact(self):
        for mot in ('oui', 'Déménager', ' déménager', 'go', ''):
            with self.subTest(mot):
                self.refuse('', lambda: self.aller(mot=mot))
        self.aller()
        avant = self.arbre()
        with self.assertRaises(self.d.Refus):
            self.retour(mot='oui')
        self.assertEqual(self.arbre(), avant)

    def test_retour_avec_processus(self):
        self.aller()
        self.refuse('serveur Moneta', lambda: self.retour(sondes=self.sondes(processus=lambda: ['serveur Moneta'])))

    def test_base_changee_pendant_la_copie(self):
        # Après la sauvegarde : rien de déplacé, le passage effacé, la base garde l'écriture venue pendant la copie.
        base = self.donnees / 'smith' / 'database' / 'finances.db'

        def crochet(etape):
            if etape == 'copie':
                ecriture_fictive(base, 'pendant la copie')
        with self.assertRaisesRegex(self.d.Refus, 'finances.db'):
            self.aller(crochet=crochet)
        self.assertEqual(actions(base)[-1], 'pendant la copie')
        self.assertAvant(empreintes={**self.empreintes, 'smith/database/finances.db': sha(base)})


class TestEchecs(Base):
    """Un échec injecté à chaque étape laisse avant ou fait, jamais entre les deux."""

    class Injecte(Exception):
        pass

    def etapes(self, faire):
        vues = []
        faire(lambda etape: vues.append(etape))
        return vues

    def test_etapes_nommees(self):
        vues = self.etapes(lambda crochet: self.aller(crochet=crochet))
        self.assertLessEqual(set(vues), ETAPES)
        self.assertLessEqual({'sauvegarde', 'copie', 'empreintes', 'bascule', 'note'}, set(vues))
        self.assertNotIn('remise', vues)
        self.assertGreaterEqual(vues.count('bascule'), len(BOUGE))
        self.assertLess(vues.index('sauvegarde'), vues.index('copie'))
        self.assertLess(max(i for i, e in enumerate(vues) if e in ('copie', 'empreintes')), vues.index('bascule'))

    def injecter(self, preparer, faire, avant, fait):
        nombre = len(self.etapes(lambda crochet: (preparer(), faire(crochet))))
        for rang in range(nombre):
            with self.subTest(rang=rang):
                preparer()
                appels = []

                def crochet(etape):
                    appels.append(etape)
                    if len(appels) == rang + 1:
                        raise self.Injecte(etape)
                with self.assertRaises(Exception):
                    faire(crochet)
                self.assertNotIn('remise', appels[:rang + 1])
                try:
                    avant()
                except (AssertionError, FileNotFoundError):  # un fichier absent de sa place d'avant : ce doit être fait
                    fait()

    def test_aller(self):
        self.injecter(lambda: setattr(self, 'donnees', self.copie()), lambda crochet: self.aller(crochet=crochet),
                      self.assertAvant, self.assertFait)

    def test_retour(self):
        def preparer():
            self.donnees = self.copie()
            self.aller()
        self.injecter(preparer, lambda crochet: self.retour(crochet=crochet), self.assertAvant, self.assertFait)

    def test_remise_impossible_dite_avec_la_liste_exacte(self):
        vues = self.etapes(lambda crochet: self.aller(donnees=self.copie('essai'), crochet=crochet))
        rangs = [i for i, e in enumerate(vues) if e == 'bascule'][1:]
        for rang in rangs:
            with self.subTest(rang=rang):
                self.donnees = self.copie()
                appels = []

                def crochet(etape):
                    appels.append(etape)
                    if len(appels) == rang + 1 or etape == 'remise':
                        raise self.Injecte(etape)
                with self.assertRaises(self.d.Bloque) as pris:
                    self.aller(crochet=crochet)
                self.assertIn('remise', appels)
                self.assertTrue(pris.exception.chemins)
                for chemin in pris.exception.chemins:
                    self.assertIn(str(chemin), str(pris.exception))
                    self.assertTrue((self.donnees / chemin).exists(), chemin)


class TestRevueT074(Base):
    """Les constats P1 de la revue T074 (docs/29_REVUE_013.md de Smith), chacun avec son essai."""

    def plan(self, donnees=None, sens=None):
        return self.d._plan(donnees or self.donnees, sens or self.d.ALLER)

    # R1 : refus refaits après le mot; aucun renommage n'écrase.
    def test_r1_cible_apparue_pendant_le_mot(self):
        avant = self.arbre()
        intrus = self.donnees / 'moneta' / 'imports' / 'releve-2026-10.pdf'

        def go(recapitulatif):
            ecrire(intrus, b'apparu pendant le mot')
            return MOT_ALLER
        with self.assertRaisesRegex(self.d.Refus, 'releve-2026-10.pdf déjà présent'):
            self.d.appliquer(self.donnees, self.sondes(), go)
        self.assertEqual(intrus.read_bytes(), b'apparu pendant le mot')
        apres = self.arbre()
        del apres['moneta/imports/releve-2026-10.pdf']
        self.assertEqual(apres, avant)

    def test_r1_ligne_ajoutee_au_journal_commun_pendant_le_mot(self):
        ecrire(self.donnees / 'journal-ecritures' / '2026-11.jsonl', (json.dumps(LIGNE) + '\n').encode())
        avant = self.arbre()

        def go(recapitulatif):
            with (self.donnees / 'journal-ecritures' / '2026-11.jsonl').open('a', encoding='utf-8') as journal:
                journal.write(json.dumps({**LIGNE, 'id': 'beefbeef'}) + '\n')
            return MOT_ALLER
        with self.assertRaisesRegex(self.d.Refus, 'changé pendant'):
            self.d.appliquer(self.donnees, self.sondes(), go)
        apres = self.arbre()
        self.assertIn('beefbeef', (self.donnees / 'journal-ecritures' / '2026-11.jsonl').read_text())
        del apres['journal-ecritures/2026-11.jsonl'], avant['journal-ecritures/2026-11.jsonl']
        self.assertEqual(apres, avant)

    def test_r1_processus_lance_pendant_le_mot(self):
        appels = []

        def processus():
            appels.append(1)
            return [] if len(appels) == 1 else ['serveur Smith']
        self.refuse_sans_changement('serveur Smith', lambda: self.aller(sondes=self.sondes(processus=processus)))
        self.assertEqual(len(appels), 2)

    def test_r1_cible_apparue_pendant_la_bascule(self):
        intrus = self.donnees / 'moneta' / 'imports' / 'releve-2026-10.pdf'

        def crochet(etape):
            if etape == 'bascule' and not intrus.exists():
                ecrire(intrus, b'apparu pendant la bascule')
        with self.assertRaisesRegex(self.d.Refus, 'apparu'):
            self.aller(crochet=crochet)
        self.assertEqual(intrus.read_bytes(), b'apparu pendant la bascule')
        for de, vers in BOUGE.items():
            self.assertEqual(sha(self.donnees / de), self.empreintes[de], de)
            if vers != 'moneta/imports/releve-2026-10.pdf':
                self.assertFalse((self.donnees / vers).exists(), vers)
        self.assertFalse((self.donnees / 'moneta' / '.passage').exists())
        self.assertRestes(self.donnees)

    def test_r1_aucun_rename(self):
        # Le renommage par os.rename écrase sans un mot sur macOS : il n'est plus employé.
        source = (ROOT / 'moneta' / 'scripts' / 'demenager.py').read_text(encoding='utf-8')
        self.assertNotIn('os.rename(', source)
        self.assertNotIn('os.replace(', source)

    # R2 : le journal commun déjà commencé est fusionné, à l'aller et au retour.
    def commencer(self, lignes):
        ecrire(self.donnees / 'journal-ecritures' / '2026-11.jsonl', ''.join(json.dumps(l) + '\n' for l in lignes).encode())

    def test_r2_journal_commun_commence(self):
        ancienne = json.loads((self.donnees / 'smith' / 'journal-ecritures' / '2026-11.jsonl').read_text())
        self.commencer([ancienne, LIGNE, {**LIGNE, 'type': 'resultat', 'resultat': 'enregistre', 'detail': None}])
        commun = sha(self.donnees / 'journal-ecritures' / '2026-11.jsonl')
        recaps = []
        note = self.d.appliquer(self.donnees, self.sondes(), lambda r: recaps.append(r) or MOT_ALLER)
        self.assertIn('fusionné', recaps[0])
        self.assertEqual(note['fusionnes'], ['journal-ecritures/2026-11.jsonl'])
        lignes = [json.loads(l) for l in (self.donnees / 'journal-ecritures' / '2026-11.jsonl').read_text().splitlines()]
        # Chaque ligne une fois (l'ancienne était en double), l'intention et son résultat gardés tous les deux.
        self.assertEqual(lignes, [ancienne, LIGNE, {**LIGNE, 'type': 'resultat', 'resultat': 'enregistre', 'detail': None}])
        self.assertEqual(note['empreintes']['journal-ecritures/2026-11.jsonl'],
                         sha(self.donnees / 'journal-ecritures' / '2026-11.jsonl'))
        remplaces = list((self.donnees / 'smith' / 'private' / 'avant-moneta').glob('*/remplaces/journal-ecritures/2026-11.jsonl'))
        self.assertEqual([sha(r) for r in remplaces], [commun])
        self.assertEqual(stat.S_IMODE(remplaces[0].stat().st_mode), 0o600)
        self.assertFalse((self.donnees / 'smith' / 'journal-ecritures' / '2026-11.jsonl').exists())
        # Au retour, un mois que l'ancien Smith a recommencé est fusionné de même.
        ecrire(self.donnees / 'smith' / 'journal-ecritures' / '2026-11.jsonl',
               (json.dumps({**LIGNE, 'id': 'abcdef12'}) + '\n').encode())
        self.retour()
        revenues = [json.loads(l) for l in (self.donnees / 'smith' / 'journal-ecritures' / '2026-11.jsonl').read_text().splitlines()]
        self.assertEqual(len(revenues), 4)
        self.assertIn({**LIGNE, 'id': 'abcdef12'}, revenues)

    def test_r2_refus_sans_rien_changer(self):
        cas = {
            'illisible': 'pas du json\n',
            'coupée': json.dumps(LIGNE),
            'différentes': json.dumps({**LIGNE, 'montant': '9.99'}) + '\n' + json.dumps(LIGNE) + '\n',
        }
        for motif, texte in cas.items():
            with self.subTest(motif):
                self.donnees = self.copie()
                ecrire(self.donnees / 'journal-ecritures' / '2026-11.jsonl', texte.encode())
                if motif == 'différentes':
                    ecrire(self.donnees / 'journal-ecritures' / '2026-11.jsonl', (json.dumps(LIGNE) + '\n').encode())
                    ecrire(self.donnees / 'smith' / 'journal-ecritures' / '2026-11.jsonl',
                           (json.dumps({**LIGNE, 'montant': '9.99'}) + '\n').encode())
                self.refuse_sans_changement(motif if motif != 'différentes' else 'deux lignes différentes')

    def test_r2_echec_injecte_avec_journal_commence(self):
        def preparer():
            self.donnees = self.copie()
            self.commencer([LIGNE])
        preparer()
        vues = self.etapes(lambda crochet: self.aller(crochet=crochet))
        for rang in range(len(vues)):
            with self.subTest(rang=rang):
                preparer()
                plan = self.plan()
                appels = []

                def crochet(etape):
                    appels.append(etape)
                    if len(appels) == rang + 1:
                        raise TestEchecs.Injecte(etape)
                with self.assertRaises(Exception):
                    self.aller(crochet=crochet)
                self.assertIn(self.d._etat(self.donnees, plan), ('avant', 'fait'))

    def etapes(self, faire):
        vues = []
        faire(vues.append)
        return vues

    # R3 : le commit de retour donné par Ugo, vérifié et noté à part.
    def depot(self, tete, objets=(), paquet=()):
        depot = self.monde / 'depot-fictif'
        shutil.rmtree(depot, ignore_errors=True)
        git = depot / '.git'
        (git / 'refs' / 'heads').mkdir(parents=True)
        (git / 'HEAD').write_text('ref: refs/heads/main\n')
        (git / 'refs' / 'heads' / 'main').write_text(tete + '\n')
        for objet in objets:
            ecrire(git / 'objects' / objet[:2] / objet[2:], b'x')
        noms = sorted(bytes.fromhex(o) for o in paquet)
        eventail = b''.join(sum(1 for n in noms if n[0] <= i).to_bytes(4, 'big') for i in range(256))
        ecrire(git / 'objects' / 'pack' / 'pack-fictif.idx', b'\xfftOc\x00\x00\x00\x02' + eventail + b''.join(noms))
        return depot

    def test_r3_commit_retour_verifie(self):
        a, b, c, e = ('a' * 40, 'b' * 40, 'c' * 40, '0e' + 'f' * 38)
        depot = self.depot(a, objets=[b], paquet=['01' * 20, e, 'ff' * 20])
        refus = self.d.commit_retour_refus
        self.assertRegex(refus(depot, 'abc1234'), '40 caractères')
        self.assertRegex(refus(depot, b.upper()), '40 caractères')
        self.assertRegex(refus(depot, a), 'actuel')
        self.assertRegex(refus(depot, c), 'inconnu')
        self.assertIsNone(refus(depot, b))
        self.assertIsNone(refus(depot, e))
        self.assertIsNone(refus(depot, 'ff' * 20))

    def test_r3_commit_retour_note_a_part(self):
        recaps = []
        note = self.d.appliquer(self.donnees, self.sondes(), lambda r: recaps.append(r) or MOT_ALLER, commit_retour='b' * 40)
        self.assertIn('b' * 40, recaps[0])
        self.assertEqual(note['commit_retour_smith'], 'b' * 40)
        ecrite = json.loads((self.donnees / 'moneta' / 'private' / 'demenagement.json').read_text())
        self.assertEqual(ecrite['commit_retour_smith'], 'b' * 40)
        self.assertIn('commit_smith', ecrite)
        self.assertEqual(self.d._commit_retour_note(self.donnees), 'b' * 40)

    def test_r3_appliquer_le_demande(self):
        for arguments, code in ((('--appliquer',), 2), (('--commit-retour', 'b' * 40), 2), (('--retour', '--commit-retour', 'b' * 40), 2),
                                (('--appliquer', '--commit-retour', 'abc'), 1)):
            with self.subTest(arguments):
                fait = lancer(*arguments)
                self.assertEqual(fait.returncode, code, fait.stdout + fait.stderr)
                self.assertIn('commit-retour', fait.stderr)
                self.assertNotIn('Traceback', fait.stderr)

    # R4 : aucun niveau d'un chemin touché n'est un lien, à l'aller et au retour.
    def test_r4_lien_sur_chaque_niveau(self):
        niveaux = ('moneta', 'moneta/database', 'moneta/backups', 'moneta/imports', 'journal-ecritures', 'moneta/private',
                   'smith/private/avant-moneta', 'smith/backups/database/avant-moneta', 'moneta/.passage')
        for niveau in niveaux:
            with self.subTest(niveau):
                self.donnees = self.copie()
                ailleurs = self.monde / 'ailleurs'
                shutil.rmtree(ailleurs, ignore_errors=True)
                ailleurs.mkdir()
                (self.donnees / niveau).parent.mkdir(parents=True, exist_ok=True)
                (self.donnees / niveau).symlink_to(ailleurs, target_is_directory=True)
                self.refuse_sans_changement('[Ll]ien')
                self.assertEqual(list(ailleurs.iterdir()), [], niveau)

    def test_r4_dossier_de_depart_lie(self):
        ailleurs = self.monde / 'smith-ailleurs'
        shutil.move(self.donnees / 'smith', ailleurs)
        (self.donnees / 'smith').symlink_to(ailleurs, target_is_directory=True)
        self.refuse_sans_changement('[Ll]ien')

    def test_r4_au_retour(self):
        for niveau in ('smith/journal-ecritures', 'smith/imports', 'moneta/private/avant-retour'):
            with self.subTest(niveau):
                self.donnees = self.copie()
                self.aller()
                ailleurs = self.monde / 'ailleurs'
                shutil.rmtree(ailleurs, ignore_errors=True)
                ailleurs.mkdir()
                shutil.rmtree(self.donnees / niveau, ignore_errors=True)
                (self.donnees / niveau).symlink_to(ailleurs, target_is_directory=True)
                self.refuse_sans_changement('[Ll]ien', self.retour)
                self.assertEqual(list(ailleurs.iterdir()), [], niveau)

    def refuse_sans_changement(self, motif, faire=None):
        avant = self.arbre()
        with self.assertRaisesRegex(self.d.Refus, motif):
            (faire or self.aller)()
        self.assertEqual(self.arbre(), avant)
        self.assertFalse((self.donnees / 'moneta' / '.passage').is_dir() and not (self.donnees / 'moneta' / '.passage').is_symlink())


class TestR7(Base):
    """R7 (P2) : le plan de la bascule est écrit avant elle; SIGTERM et SIGHUP remettent tout; après un arrêt brutal, le
    refus nomme le plan."""

    def enfant(self, signal_nom, rang=3):
        # Un vrai processus, qui s'envoie le signal au rang-ième renommage de la bascule.
        code = (f"import os, signal, sys; sys.path.insert(0, {str(ROOT)!r}); from moneta.scripts import demenager as d\n"
                "vus = []\n"
                "def crochet(etape):\n"
                "    if etape == 'bascule':\n"
                "        vus.append(1)\n"
                f"        if len(vus) == {rang}: os.kill(os.getpid(), signal.{signal_nom})\n"
                f"d.appliquer({str(self.donnees)!r}, d.FICTIVES, lambda r: 'déménager', crochet=crochet)\n")
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)

    def test_plan_ecrit_avant_la_bascule(self):
        vus = []

        def crochet(etape):
            if etape == 'bascule' and not vus:
                chemin = self.donnees / 'moneta' / '.passage' / 'plan-bascule.json'
                vus.append((json.loads(chemin.read_text()), stat.S_IMODE(chemin.stat().st_mode)))
        self.aller(crochet=crochet)
        plan, mode = vus[0]
        self.assertEqual(mode, 0o600)
        paires = {(d['avant'], d['apres']) for d in plan['deplacements']}
        for de, vers in BOUGE.items():
            self.assertIn((f'moneta/.passage/{vers}', vers), paires)
            self.assertIn((de, f"smith/private/avant-moneta/{plan['horodatage']}/{de}"), paires)
        self.assertFalse((self.donnees / 'moneta' / '.passage').exists())

    def test_sigterm_et_sighup_remettent_tout(self):
        for nom in ('SIGTERM', 'SIGHUP'):
            with self.subTest(nom):
                self.donnees = self.copie()
                fait = self.enfant(nom)
                self.assertNotEqual(fait.returncode, 0)
                self.assertIn('Interrompu', fait.stderr)
                self.assertAvant()

    def test_arret_brutal_refus_qui_nomme_le_plan(self):
        fait = self.enfant('SIGKILL')
        self.assertEqual(fait.returncode, -9)
        plan = json.loads((self.donnees / 'moneta' / '.passage' / 'plan-bascule.json').read_text())
        self.assertEqual(len(plan['deplacements']), 2 * len(BOUGE))
        avant = self.arbre()
        with self.assertRaisesRegex(self.d.Refus, 'plan-bascule.json'):
            self.aller()
        self.assertEqual(self.arbre(), avant)


class TestProcessus(unittest.TestCase):
    def test_lignes_de_ps(self):
        from moneta.scripts.demenager import processus_en_cours
        p = '/Users/ugod/_PersonalAgents'
        attendus = {
            '/Applications/Claude.app/Contents/MacOS/Claude': 'Claude Desktop',
            f'{p}/smith/.venv/bin/python scripts/smith_app.py': 'app Smith',
            f'{p}/smith/.venv/bin/python {p}/smith/assistant/scripts/smith_mcp.py': 'serveur Smith',
            f'{p}/moneta/.venv/bin/python {p}/moneta/moneta/scripts/moneta_mcp.py --app': 'serveur Moneta',
            f'/bin/zsh {p}/smith/bin/smith-sauvegarde-quotidienne': 'tâche de nuit',
            f'/bin/zsh {p}/moneta/bin/moneta-sauvegarde-quotidienne': 'tâche de nuit',
        }
        for ligne, libelle in attendus.items():
            self.assertEqual(processus_en_cours([ligne]), [libelle], ligne)
        for ligne in ('/opt/homebrew/bin/claude', '-zsh', 'node codex-companion.mjs', 'vim notes.md', ''):
            self.assertEqual(processus_en_cours([ligne]), [], ligne)
        self.assertEqual(set(processus_en_cours(list(attendus))), set(attendus.values()))


def lancer(*arguments, entree=''):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('MONETA_', 'UGOD_'))}
    env['MONETA_HOME'] = os.environ['MONETA_HOME']  # le monde fictif de la suite : sans installation, Moneta ne démarre pas sans lui
    return subprocess.run([str(ROOT / 'bin' / 'moneta-demenager'), *map(str, arguments)], capture_output=True, text=True,
                          timeout=300, input=entree, env=env)


class TestRepeter(Base):
    def test_parcours_complet(self):
        fait = lancer('--repeter', self.donnees)
        sortie = fait.stdout + fait.stderr
        self.assertEqual(fait.returncode, 0, sortie)
        self.assertTrue(fait.stdout.strip().splitlines()[-1].startswith('OK'), sortie)
        for mot in ('aller', 'retour', 'empreintes'):
            self.assertIn(mot, sortie.lower())
        # Fini sur le dernier aller, avec l'écriture fictive faite entre l'aller et le retour.
        for de, vers in BOUGE.items():
            self.assertFalse((self.donnees / de).exists(), de)
            self.assertTrue((self.donnees / vers).is_file(), vers)
        self.assertGreater(len(actions(self.donnees / 'moneta' / 'database' / 'finances.db')),
                           len(actions(self.modele / 'smith' / 'database' / 'finances.db')))
        self.assertRestes(self.donnees)

    def test_refuse_un_dossier_reel(self):
        for dossier in (REEL, REEL / 'smith', Path.home() / '_PersonalAgents' / 'essai-fictif'):
            with self.subTest(dossier):
                fait = lancer('--repeter', dossier)
                self.assertNotEqual(fait.returncode, 0)
                self.assertIn('fictif', (fait.stdout + fait.stderr).lower())
                self.assertNotIn('Traceback', fait.stderr)

    def test_refuse_un_dossier_incomplet(self):
        (self.donnees / 'smith' / 'database' / 'finances.db').unlink()
        avant = self.arbre()
        fait = lancer('--repeter', self.donnees)
        self.assertNotEqual(fait.returncode, 0)
        self.assertEqual(self.arbre(), avant)


class TestHorsDePortee(unittest.TestCase):
    """Aucun mode appelable par un outil MCP ni par l'app; aucun appel à Git."""

    def test_personne_ne_l_appelle(self):
        permis = {ROOT / 'moneta' / 'scripts' / 'demenager.py', ROOT / 'bin' / 'moneta-demenager'}
        depots = [(ROOT, ('moneta', 'bin')), (SMITH, ('assistant', 'bin'))]
        for racine, dossiers in depots:
            for dossier in dossiers:
                for chemin in (racine / dossier).rglob('*'):
                    if chemin in permis or not chemin.is_file() or '__pycache__' in chemin.parts:
                        continue
                    if chemin.suffix not in ('.py', '.js', '.html', '') or chemin.stat().st_size > 2_000_000:
                        continue
                    texte = chemin.read_text(encoding='utf-8', errors='ignore')
                    self.assertNotIn('demenag', texte.lower(), chemin)

    def test_aucun_appel_a_git(self):
        source = (ROOT / 'moneta' / 'scripts' / 'demenager.py').read_text(encoding='utf-8')
        # Le commit se lit dans les fichiers du dépôt (.git est permis); aucune commande git, nommée ou par son chemin.
        for noeud in ast.walk(ast.parse(source)):
            if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
                mot = noeud.value.strip().split(' ')[0]
                self.assertFalse(mot == 'git' or mot.endswith('/git'), noeud.value)
        self.assertNotIn('git', (ROOT / 'bin' / 'moneta-demenager').read_text(encoding='utf-8').replace('.git', ''))


if __name__ == '__main__':
    unittest.main()
