"""bin/moneta-verifier --json, version 2 (spec 018 de Smith, T024) : l'âge de la dernière copie vérifiée du Mac, du disque et de Drive,
lu dans le journal de la nuit de Moneta (private/sauvegarde-quotidienne.log) et jugé par socle.fraicheur.

Mac : la date de la dernière ligne « OK local ». Drive : l'image de « OK hors Mac : <sorte>/<image> ». Disque : l'image de
« OK disque : <sorte>/<image> ». Une ligne « à jour » ne compte pas. Données fictives seulement (MONETA_HOME de run_all).
"""
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ['MONETA_HOME'])
LABEL = 'ca.dojod.moneta.sauvegarde'


def lancer(*arguments):
    return subprocess.run([str(ROOT / 'bin' / 'moneta-verifier'), *arguments], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)


def image(jour, mensuelle=False):
    return f"moneta-{'mensuelle-' if mensuelle else ''}{jour}_211500.dmg"


class TestVerifierSauvegardes(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(HOME, ignore_errors=True)
        HOME.mkdir(parents=True)
        HOME.chmod(0o700)
        self.journal = HOME / 'private' / 'sauvegarde-quotidienne.log'

    def ecrire(self, *lignes):
        """lignes : (jour de la ligne, « OK local : … »); le journal reste en 600 dans un private/ en 700."""
        self.journal.parent.mkdir(mode=0o700, exist_ok=True)
        self.journal.write_text(''.join(f'{jour} 21:15 {texte}\n' for jour, texte in lignes), encoding='utf-8')
        self.journal.chmod(0o600)

    def trois(self, **options):
        from moneta.scripts import verifier
        return verifier.sauvegardes(verifier.journal_de_nuit(HOME), **options)

    def sain(self, jour):
        return [(jour, f'OK local : finances_{jour}.db'), (jour, f'OK hors Mac : quotidiennes/{image(jour)} (1 fichiers vérifiés)'),
                (jour, f'OK disque : quotidiennes/{image(jour)} (1 fichiers vérifiés)')]

    def test_trois_controles_dans_l_ordre_au_contrat_v2(self):
        aujourdhui = date.today().isoformat()
        self.ecrire(*self.sain(aujourdhui))
        controles = self.trois()
        self.assertEqual([c['texte'] for c in controles], [f'Moneta, Mac : copie vérifiée du {aujourdhui}',
                                                          f'Moneta, disque : copie vérifiée du {aujourdhui}',
                                                          f'Moneta, Drive : copie vérifiée du {aujourdhui} (dossier Drive du Mac)'])
        for c in controles:
            self.assertEqual(c, {'ok': True, 'texte': c['texte'], 'corriger': '', 'attention': False})

    def test_le_vrai_programme_dit_la_version_2(self):
        # Les autres contrôles sont rouges (aucune base) : seuls comptent ici la version, les clés et les trois derniers.
        self.ecrire(*self.sain(date.today().isoformat()))
        valeur = json.loads(lancer('--json').stdout)
        self.assertEqual(valeur['version'], 2)
        for c in valeur['controles']:
            self.assertEqual(set(c), {'ok', 'texte', 'corriger', 'attention'})
            self.assertLessEqual(len(c['texte']), 200)
            self.assertLessEqual(len(c['corriger']), 200)
        derniers = valeur['controles'][-3:]
        self.assertEqual([c['texte'].split(' : ')[0] for c in derniers], ['Moneta, Mac', 'Moneta, disque', 'Moneta, Drive'])
        self.assertTrue(all(c['ok'] for c in derniers))

    def test_sans_journal_les_trois_sont_des_echecs_qui_disent_quoi_faire(self):
        controles = self.trois()
        self.assertEqual([c['ok'] for c in controles], [False] * 3)
        self.assertEqual([c['corriger'] for c in controles],
                         ['voir moneta-sauvegarde-quotidienne etat',
                          f'brancher PRO-HD1, puis launchctl kickstart gui/$UID/{LABEL}',
                          f'ouvrir Google Drive sur le Mac, puis launchctl kickstart gui/$UID/{LABEL}'])
        # Lecture seule : le dossier private/ n'est pas créé par le vérificateur.
        self.assertFalse((HOME / 'private').exists())

    def test_le_mac_prend_la_date_de_la_derniere_ligne_ok_local(self):
        self.ecrire(('2026-11-01', 'OK local : finances_2026-11-01.db'), ('2026-11-03', 'OK local : finances_2026-11-03.db'),
                    ('2026-11-04', 'ERREUR local : disque plein'))
        mac = self.trois(aujourdhui=date(2026, 11, 4))[0]
        self.assertEqual((mac['ok'], mac['attention'], mac['texte']), (True, False, 'Moneta, Mac : copie vérifiée du 2026-11-03'))

    def test_drive_et_disque_prennent_la_date_de_l_image_pas_celle_de_la_ligne(self):
        # Rattrapage du 4 novembre : la ligne est du 4, l'image du 30 octobre. Une ligne « à jour » ne compte pas.
        self.ecrire(('2026-11-04', f'OK hors Mac : quotidiennes/{image("2026-10-30")} (1 fichiers vérifiés, 0 pièce(s))'),
                    ('2026-11-04', f'OK disque : mensuelles/{image("2026-10-01", True)} (1 fichiers vérifiés)'),
                    ('2026-11-04', 'OK disque : à jour (3 image(s) déjà sur le disque)'))
        _, disque, drive = self.trois(aujourdhui=date(2026, 11, 4))
        self.assertEqual((drive['ok'], drive['attention']), (True, True))
        self.assertIn('copie vérifiée du 2026-10-30 (5 jours)', drive['texte'])
        self.assertEqual((disque['ok'], disque['attention']), (False, False))
        self.assertIn('dernière : 2026-10-01', disque['texte'])

    def test_seuils_deux_et_sept_jours(self):
        resultats = {}
        for age in (0, 2, 3, 7, 8):
            jour = date(2026, 11, 4) - timedelta(days=age)
            self.ecrire(('2026-11-04', f'OK disque : quotidiennes/{image(jour)} (1 fichiers vérifiés)'))
            disque = self.trois(aujourdhui=date(2026, 11, 4))[1]
            resultats[age] = (disque['ok'], disque['attention'])
        self.assertEqual(resultats, {0: (True, False), 2: (True, False), 3: (True, True), 7: (True, True), 8: (False, False)})

    def test_une_image_d_un_autre_nom_ou_une_ligne_abimee_ne_compte_pas(self):
        self.ecrire(('2026-11-04', 'OK hors Mac : quotidiennes/agent-smith-2026-11-04_210000.dmg (1 fichiers vérifiés)'),
                    ('2026-11-04', 'OK disque : quotidiennes/moneta-2026-11-04.dmg (1 fichiers vérifiés)'))
        with open(self.journal, 'a', encoding='utf-8') as f:
            f.write('ligne sans forme\n\x00\n2026-13-45 99:99 OK local : x\n')
        _, disque, drive = self.trois(aujourdhui=date(2026, 11, 4))
        self.assertEqual((disque['ok'], drive['ok']), (False, False))

    def test_journal_illisible_donne_trois_echecs(self):
        self.journal.parent.mkdir(mode=0o700)
        self.journal.write_bytes(b'\xff\xfe\x00 pas de l\'utf-8')
        self.assertEqual([c['ok'] for c in self.trois()], [False] * 3)

    def test_sortie_sans_json_marque_l_attention(self):
        hier = (date.today() - timedelta(days=4)).isoformat()
        self.ecrire((hier, f'OK local : finances_{hier}.db'))
        sortie = lancer().stdout
        self.assertIn(f'⚠️ Moneta, Mac : copie vérifiée du {hier} (4 jours) — corriger : voir moneta-sauvegarde-quotidienne etat', sortie)
        self.assertIn('❌ Moneta, Drive', sortie)

    def test_les_textes_ne_portent_que_des_dates_et_des_noms_de_destination(self):
        self.ecrire(*self.sain(date.today().isoformat()))
        for c in self.trois():
            self.assertNotIn('finances', c['texte'])
            self.assertNotIn('.dmg', c['texte'])


if __name__ == '__main__':
    unittest.main()
