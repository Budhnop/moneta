"""Les mêmes protections chez Moneta, et aucun nouvel accès (spec 013, T042, récit 3.1, 3.2, 3.4 et 3.6).

Go sous les plafonds de 1 000 $ et 10 étapes, sans fenêtre; annulation, retrait, plafond dépassé, règle et signal récent : la
fenêtre du Mac, au nom d'Agent Smith (D107). Le signal et le journal sont communs : un signal allumé par Smith, Thot ou Atlas ouvre
la fenêtre de Moneta; un signal de Moneta est sain pour le socle de Smith; l'argent écrit en go par Smith compte dans le plafond du
jour de Moneta. Smith tourne ici par son propre code (MONETA_TESTS_SMITH), sur le même dossier fictif. Tout est fictif.
"""
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moneta import chemins, regles
from moneta.declaration import DECLARATION
from outils_tests import SMITH, FausseFenetre
from socle.chemins import write_private
from socle import confirmation, declaration, installation, politique, signal_texte
from test_banque import CHEQUING, DOJO_LINES, PERSO_LINES, SAVINGS, ofx, statement
from test_ecritures_finances import Ecritures, call

FENETRE = 'Agent Smith va écrire :'


def smith(code, home, signal):
    """Lance code avec le Python et le code de Smith, déclaré au socle (import core), sur le dossier fictif home; sa sortie."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('UGOD_', 'MONETA_'))}
    # Un Drive fictif aussi : sinon Smith se sait près du vrai Drive et n'est plus en mode fictif.
    env.update(UGOD_HOME=str(home), UGOD_SIGNAL_TEXTE=str(signal), UGOD_CONFIRMATION='automatique',
               UGOD_DRIVE_DOSSIER=str(Path(home).parent / 'smith-drive'),
               PYTHONPATH=str(SMITH / 'assistant'), PYTHONDONTWRITEBYTECODE='1')
    fait = subprocess.run([str(SMITH / '.venv' / 'bin' / 'python'), '-c', 'import core\n' + code], cwd=SMITH / 'assistant',
                          env=env, capture_output=True, text=True, timeout=60)
    if fait.returncode:
        raise AssertionError(fait.stderr[-2000:])
    return fait.stdout.strip()


class Protections(Ecritures):
    def setUp(self):
        super().setUp()
        self.home = self.folder / 'home'
        self.signal_fichier = Path(os.environ['SOCLE_SIGNAL_TEXTE'])


class TestGoSousLesPlafonds(Protections):
    def revenu(self, montant):
        return self.ok('revenu_preparer', {'date': self.TODAY, 'montant': montant, 'type': 'kimono', 'mode': 'virement'})

    def test_dix_etapes_qui_ajoutent_ou_modifient_sous_1000_dollars(self):
        ledger, _ = self.finances()
        # Un revenu tient sur une ligne de la fenêtre, une dépense sur quatre : dix dépenses ne tiendraient pas à l'écran.
        for _ in range(9):
            self.revenu(100)
        self.expense(99.99)
        prepared = self.ok('finances_lot_preparer')
        self.assertEqual((prepared['confirmation'], prepared['raisons_fenetre'], prepared['montant_compte']), ('go', [], 999.99))
        with FausseFenetre() as fenetre:
            done = self.ok('WR_SAVE_FINANCES_BD', {'code': prepared['code']})
        self.assertEqual((fenetre.nombre, done['enregistre'], done['confirmation']), (0, True, 'go'))
        # Une modification (compléter une dépense) passe aussi en go.
        self.ok('depense_completer_preparer', {'id': 1, 'fournisseur': 'Quincaillerie fictive'})
        self.go()

    def test_onze_etapes_ou_plus_de_1000_dollars_ouvrent_la_fenetre(self):
        etape = {'operation': 'revenu', 'outil': 'revenu_preparer', 'resume': {'montant': 1.0}, 'signal': None}
        self.assertEqual(politique.decider('finances_lot', [dict(etape) for _ in range(10)]).mode, 'go')
        decision = politique.decider('finances_lot', [dict(etape) for _ in range(11)])
        self.assertEqual((decision.mode, decision.raisons), ('fenetre', ('plafond_etapes',)))
        # Dans le vrai lot, la fenêtre de onze étapes ne tiendrait pas à l'écran : refusée avant tout, jamais un go.
        self.finances()
        for _ in range(11):
            self.revenu(1)
        refus = call(self.server, 'finances_lot_preparer', {})
        self.assertTrue(refus['isError'])
        self.assertIn('ne tient pas en entier', refus['content'][0]['text'])
        self.ok('finances_lot_vider')
        self.expense(1000.01)
        self.window(self.ok('finances_lot_preparer'), ['plafond_montant'])


class TestFenetre(Protections):
    def titre(self, prepared, raisons):
        """Enregistre par la fenêtre pour ces raisons; la fenêtre ouverte."""
        with FausseFenetre() as fenetre:
            done = self.ok('WR_SAVE_FINANCES_BD', {'code': prepared['code']})
        self.assertEqual((done['confirmation'], done['raisons_fenetre']), ('fenetre', [regles.RAISONS[r] for r in raisons]))
        return fenetre

    def assert_au_nom_de_smith(self, fenetre):
        self.assertEqual(fenetre.nombre, 1)
        # Le texte que la vraie fenêtre montre (FausseFenetre reçoit les lignes, sans l'en-tête de la déclaration).
        self.assertTrue(confirmation.check(fenetre.ouvertures[0][1]).startswith(FENETRE + '\n'))

    def test_chaque_cas_de_fenetre_parle_au_nom_d_agent_smith(self):
        ledger, _ = self.finances()
        self.ok('depense_preparer', {'date': self.TODAY, 'montant': 20, 'categorie': '8811'})
        self.go()
        cas = [('regle_preparer', {'motif': 'location salle', 'categorie': '8910'}, ['reglage']),
               ('depense_annulation_preparer', {'id': 1}, ['suppression'])]
        for outil, arguments, raisons in cas:
            with self.subTest(outil=outil):
                self.ok(outil, arguments)
                prepared = self.ok('finances_lot_preparer')
                self.assertEqual(prepared['confirmation'], 'fenetre')
                self.assert_au_nom_de_smith(self.titre(prepared, raisons))
        regle = self.ok('finances_reference')['regles'][0]['id']
        self.ok('regle_retirer_preparer', {'id': regle})
        self.assert_au_nom_de_smith(self.titre(self.ok('finances_lot_preparer'), ['suppression']))
        self.expense(1500)
        self.assert_au_nom_de_smith(self.titre(self.ok('finances_lot_preparer'), ['plafond_montant', 'plafond_jour']))
        self.signal(10)
        self.expense(5)
        self.assert_au_nom_de_smith(self.titre(self.ok('finances_lot_preparer'), ['texte_autrui_recent']))


class TestSignalCommun(Protections):
    def test_un_signal_de_smith_thot_ou_atlas_ouvre_la_fenetre_de_moneta(self):
        self.finances()
        # Socle 2.0 (R4) : un agent n'écrit jamais au nom d'un autre. Les autres écrivent par leur propre code; ici, leur signal est
        # posé tel quel, et l'écosystème du banc d'essai (ecosysteme.toml à côté des données) dit qu'ils sont connus.
        write_private(self.folder, 'ecosysteme.toml', b'agents = ["moneta", "smith", "thot", "atlas"]\n')
        for par in ('thot', 'atlas'):
            with self.subTest(par=par):
                moment = datetime.now(timezone.utc) - timedelta(minutes=5)
                signal_texte._ecrire({'version': 1, 'dernier_rendu': moment.strftime('%Y-%m-%dT%H:%M:%SZ'), 'par': par})
                self.expense(10)
                self.window(self.ok('finances_lot_preparer'), ['texte_autrui_recent'])
        # Smith par son propre code, sur le même signal.
        smith('from socle import signal_texte\nsignal_texte.inscrire()', self.home, self.signal_fichier)
        self.assertIn('"par": "smith"', self.signal_fichier.read_text(encoding='utf-8'))
        self.expense(10)
        self.window(self.ok('finances_lot_preparer'), ['texte_autrui_recent'])

    def test_un_signal_de_moneta_est_sain_pour_le_socle_de_smith(self):
        signal_texte.inscrire()
        self.assertIn('"par": "moneta"', self.signal_fichier.read_text(encoding='utf-8'))
        self.assertEqual(smith('from socle import signal_texte\nprint(signal_texte.etat())', self.home, self.signal_fichier),
                         'recent')
        signal_texte.inscrire(maintenant=datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertEqual(smith('from socle import signal_texte\nprint(signal_texte.etat())', self.home, self.signal_fichier),
                         'ancien')

    def test_un_autre_nom_reste_douteux(self):
        self.finances()
        self.signal_fichier.write_text('{"version": 1, "dernier_rendu": null, "par": "inconnu"}', encoding='utf-8')
        self.expense(10)
        self.window(self.ok('finances_lot_preparer'), ['signal_douteux'])


class TestPlafondDuJourCommun(Protections):
    def test_600_dollars_par_smith_puis_600_par_moneta(self):
        self.finances()
        # Le journal commun est le même dossier : Smith et Moneta partagent ici leur dossier fictif.
        smith("from decimal import Decimal\nfrom types import SimpleNamespace\nfrom socle import journal_ecritures as j\n"
              "i = j.intention('WR_SAVE_DOJO_BD', 'lot', SimpleNamespace(mode='go', raisons=[], montant=Decimal('600.00')),"
              " [{'numero': 1, 'operation': 'paiement_preparer', 'resume': 'Famille Exemple, 600 $'}])\n"
              "assert j.resultat(i, 'enregistre') is None", self.home, self.signal_fichier)
        ecrites = self.journal()
        self.assertEqual([(l['agent'], l['type'], l['mode']) for l in ecrites if l['type'] == 'intention'],
                         [('smith', 'intention', 'go')])
        self.expense(600)
        self.window(self.ok('finances_lot_preparer'), ['plafond_jour'])

    def test_sans_smith_600_passe(self):
        self.finances()
        self.expense(600)
        self.go()


class TestTexteBancaire(Protections):
    """D050, puis R11 de la spec 002 : une description bancaire reste retenue tant qu'Ugo ne la demande pas lui-même."""

    def test_description_retenue_puis_signal_a_la_demande(self):
        imports = self.home / 'imports'
        imports.mkdir(parents=True)
        (imports / 'septembre.ofx').write_bytes(ofx(statement(CHEQUING, PERSO_LINES), statement(SAVINGS, DOJO_LINES)))
        _, server = self.finances()
        self.ok('releve_import_preparer', {'fichier': 'septembre.ofx', 'comptes': {'…3456 EOP': 'Perso', '…3456 ES1': 'Dojo'}})
        self.go()
        avant = self.signal_fichier.read_text(encoding='utf-8')
        retenues = self.ok('banque_operations', {'compte': 'Perso'})
        self.assertNotIn('texte_externe', retenues)
        self.assertTrue(all(o.get('description') is None for o in retenues['operations']))
        self.assertEqual(self.signal_fichier.read_text(encoding='utf-8'), avant)
        self.expense(5)
        self.go()
        montrees = self.ok('banque_operations', {'compte': 'Perso', 'inclure_texte_autrui': True})
        self.assertIn('description', montrees['texte_externe'])
        self.assertEqual(signal_texte.etat(), 'recent')
        self.expense(5)
        self.window(self.ok('finances_lot_preparer'), ['texte_autrui_recent'])


class TestDeclaration(unittest.TestCase):
    """Récit 3.6 : Moneta ne lit que les paiements que Dojo lui partage, n'a que sa base et les sorties de D109."""

    def test_lectures_bases_et_sorties(self):
        # Ce que le socle a retenu (une copie figée de DECLARATION), pas seulement le module.
        agent = declaration.actuelle()
        self.assertEqual((agent.nom, DECLARATION.nom), ('moneta', 'moneta'))
        self.assertEqual(agent.lectures, ('smith:paiements_dojo',))
        self.assertEqual(agent.bases(), (chemins.finances_db(),))
        self.assertEqual(agent.bases()[0].name, 'finances.db')
        self.assertEqual(agent.dossiers_sortie(), (chemins.drive(),))
        self.assertEqual(agent.dossiers_reels, tuple(d for d in (chemins.REAL_HOME,) if d))
        self.assertEqual(chemins.REAL_HOME, installation.dossier_reel(chemins.INSTALLATION, 'moneta'))

    def test_aucun_outil_du_dojo(self):
        noms = set(DECLARATION.noms_outils())
        self.assertTrue(noms)
        self.assertFalse({n for n in noms if n == 'WR_SAVE_DOJO_BD' or n.startswith(('dojo_', 'membre_', 'paiement_'))})
        self.assertTrue(all(DECLARATION.outil(n) is not None for n in noms))
        self.assertIsNone(DECLARATION.outil('WR_SAVE_DOJO_BD'))


if __name__ == '__main__':
    unittest.main()
