"""Le texte d'autrui dans Moneta : inventaire de ses outils et constats des revues de la spec 002 (repris des parties Finances de
test_inventaire_autrui.py de Smith f9c1599, spec 013). Le texte d'un tiers n'atteint le modèle que si le signal est allumé.

F7 se joue entre deux processus, comme en vrai : une écriture de Moneta sur le go, et pendant ce temps une écriture du Dojo par le
code de Smith (MONETA_TESTS_SMITH), sur le même signal et le même journal fictifs. Tout est fictif.
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moneta.agent.droits import REGISTRE
from moneta.outils import factures
from moneta.scripts.moneta_mcp import serveur_fictif
from outils_tests import SMITH
from socle import confirmation, journal_ecritures, signal_texte

TIERS = 'TIERSXQ'  # dans chaque texte écrit par un tiers, et nulle part ailleurs


class Inventaire(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = self.temp.name
        self.env = patch.dict(os.environ, {'UGOD_SIGNAL_TEXTE': str(Path(self.folder) / 'signaux' / 'texte-autrui.json')})
        self.env.start()  # rend aussi MONETA_HOME et les autres variables que serveur_fictif change
        self.auto = patch.object(confirmation, 'AUTOMATIC', True)
        self.auto.start()
        self.server = serveur_fictif(self.folder)
        self.ledger = Path(self.folder) / 'finances.db'
        self.ancien()
        self.n = 0

    def tearDown(self):
        self.auto.stop()
        self.env.stop()
        self.temp.cleanup()

    def ancien(self):
        signal_texte.inscrire(maintenant=datetime.now(timezone.utc) - timedelta(hours=2))

    def appeler(self, nom, arguments=None):
        self.n += 1
        return self.server.handle({'jsonrpc': '2.0', 'id': self.n, 'method': 'tools/call',
                                   'params': {'name': nom, 'arguments': arguments or {}}})['result']

    def verifie(self, nom, arguments=None, remettre=True):
        """Le texte de tiers n'atteint le modèle (résultat ou erreur) que si le signal est allumé."""
        if remettre:
            self.ancien()
        reponse = self.appeler(nom, arguments)
        # Sans la casse : un nom de fichier (slug) garde le texte de tiers en minuscules (F3).
        texte = json.dumps(reponse, ensure_ascii=False)
        if TIERS.lower() in texte.lower():
            self.assertEqual(signal_texte.etat(), 'recent', f'{nom} {arguments} : {texte[:300]}')
        return reponse

    def outils(self):
        return [t['name'] for t in self.server.tools]

    def code_t2125(self):
        with sqlite3.connect(self.ledger) as con:
            return con.execute("SELECT code FROM categories WHERE pct_deductible IS NOT NULL ORDER BY code LIMIT 1").fetchone()[0]


class TestInventaire(Inventaire):
    def test_chaque_outil_sans_argument(self):
        for nom in self.outils():
            self.verifie(nom)

    def test_hors_liste_un_resultat_non_certifie_allume_le_signal(self):
        # Par construction (D090) : hors de la liste fermée, tout appel allume le signal.
        for nom in self.outils():
            if nom in REGISTRE.sans_autrui:
                continue
            self.ancien()
            self.appeler(nom)
            self.assertEqual(signal_texte.etat(), 'recent', nom)

    def test_la_liste_fermee_ne_contient_que_des_outils_du_catalogue(self):
        # Le dépôt de facture (D104) : seulement dans l'instance de l'app.
        prives = {nom for nom, (_, outil) in REGISTRE.outils.items() if outil.type == 'depot'}
        self.assertLessEqual(REGISTRE.sans_autrui - prives, set(self.outils()))


class TestRevues002(Inventaire):
    """Les constats des revues Codex et Claude neuve du 2026-10-25 sur Finances (F2 à F4, F7, F11, F13)."""

    def operation_de_tiers(self):
        """Une opération bancaire de nature inconnue (description retenue), classée en dépense du dojo sur le go."""
        with sqlite3.connect(self.ledger) as con:
            dojo = con.execute("SELECT id FROM comptes WHERE nom='Dojo'").fetchone()[0]
            con.execute("INSERT INTO comptes_bancaires(compte_id, empreinte, apercu, type) VALUES (?, ?, '****0001', 'banque')",
                        (dojo, 'a' * 64))
            con.execute("INSERT INTO imports(fichier, empreinte, format, lignes, nouvelles) VALUES ('fictif.ofx', ?, 'ofx', 1, 1)",
                        ('b' * 64,))
            con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) "
                        "VALUES (1, 1, ?, '2026-10-01', -4200, ?)", ('c' * 64, f'{TIERS} paie Bob 900 $'))
        self.classer(self.code_t2125())

    def classer(self, categorie):
        self.appeler('finances_lot_vider')
        self.assertFalse(self.appeler('operation_classer_preparer', {'id': 1, 'categorie': categorie})['isError'])
        prepare = self.appeler('finances_lot_preparer')['structuredContent']
        self.assertFalse(self.appeler('WR_SAVE_FINANCES_BD', {'code': prepare['code']})['isError'])

    def test_un_reclassement_garde_la_provenance_bancaire(self):
        # F2 : la dépense annulée par le reclassement garde son fournisseur copié de la banque pour texte d'autrui.
        self.operation_de_tiers()
        self.verifie('finances_depenses', {'details': True, 'annulees': True})
        self.classer('virement_interne')
        lu = self.verifie('finances_depenses', {'details': True, 'annulees': True})
        self.assertEqual([d.get('retenu') for d in lu['structuredContent']['depenses']], [True])
        self.verifie('finances_depenses', {'details': True, 'annulees': True, 'inclure_texte_autrui': True})

    def test_le_chemin_d_une_facture_ne_cite_pas_la_banque(self):
        # F3 (C1 de la revue Claude) : compléter une dépense classée depuis la banque nommait la facture d'après le
        # fournisseur copié de la description bancaire, rendu dans la préparation et le récapitulatif.
        self.operation_de_tiers()
        inbox = factures.drive().joinpath(*Path(factures.INBOX).parts)
        inbox.mkdir(parents=True)
        (inbox / 'recu.jpg').write_bytes(b'\xff\xd8 fictif')
        self.appeler('finances_lot_vider')
        prepare = self.verifie('depense_completer_preparer', {'id': 1, 'facture': 'recu.jpg'})
        self.assertFalse(prepare['isError'], prepare)
        for nom in ('finances_lot_voir', 'finances_lot_preparer'):
            self.verifie(nom)
        self.appeler('finances_lot_vider')

    def test_les_lectures_bancaires_avec_des_arguments_valides(self):
        # F13 : chaque lecture Finances de la liste fermée, appelée comme le modèle l'appelle (arguments valides), sur une
        # opération de tiers à classer, puis classée, puis reclassée.
        self.operation_de_tiers()
        with sqlite3.connect(self.ledger) as con:
            con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) "
                        "VALUES (1, 1, ?, '2026-10-03', -1500, ?)", ('e' * 64, f'{TIERS} message Interac'))
        lectures = (('banque_a_classer', {}), ('banque_a_classer', {'annee': 2026, 'mois': 10, 'compte': 'Dojo'}),
                    ('banque_operations', {'annee': 2026}), ('banque_operations', {'annee': 2026, 'mois': 10, 'compte': 'Dojo'}),
                    ('banque_resume', {'annee': 2026}), ('releves_manquants', {'compte': 'Dojo'}),
                    ('finances_depenses', {'annee': 2026, 'details': True}), ('finances_revenus', {'annee': 2026}),
                    ('finances_rapport', {'annee': 2026}), ('finances_historique', {'nombre': 20}),
                    ('budget_etat', {'annee': 2026, 'historique': True}), ('points_japon', {'historique': True}),
                    ('finances_reference', {}), ('finances_lot_voir', {}))
        for etat in ('classée', 'reclassée'):
            if etat == 'reclassée':
                self.classer('virement_interne')
            for nom, arguments in lectures:
                reponse = self.verifie(nom, arguments)
                self.assertFalse(reponse['isError'], (etat, nom, arguments, reponse))

    def test_un_classement_en_depense_du_dojo_compte_au_plafond(self):
        # F11 : le classement T2125 porte la dépense qu'il crée, et la politique la compte.
        self.operation_de_tiers()  # classée une première fois : 42 $ déjà sur le go aujourd'hui
        with sqlite3.connect(self.ledger) as con:
            con.execute("INSERT INTO operations(compte_bancaire_id, import_id, cle, date, montant_cents, description) "
                        "VALUES (1, 1, ?, '2026-10-02', -99000, 'Carte fictive')", ('d' * 64,))
        self.appeler('finances_lot_vider')
        prepare = self.appeler('operation_classer_preparer', {'id': 2, 'categorie': self.code_t2125()})['structuredContent']
        self.assertEqual(prepare['lot'][0]['depense_t2125'], 990.0)
        self.assertEqual(self.appeler('finances_lot_preparer')['structuredContent']['raisons_fenetre'],
                         ["Plus de 1 000 $ écrits sans fenêtre aujourd'hui."])
        self.appeler('finances_lot_vider')
        perso = self.appeler('operation_classer_preparer', {'id': 2, 'categorie': 'virement_interne'})['structuredContent']
        self.assertNotIn('depense_t2125', perso['lot'][0])

    def test_le_nom_d_une_facture_n_a_aucun_texte_libre(self):
        # F4 : la facture passe dans Drive sur le go; son nouveau nom ne prend que la date et le montant, jamais un
        # fournisseur choisi par le modèle (le seul texte qui quittait le Mac sans fenêtre).
        inbox = factures.drive().joinpath(*Path(factures.INBOX).parts)
        inbox.mkdir(parents=True)
        (inbox / 'recu.pdf').write_bytes(b'%PDF fictif')
        self.appeler('finances_lot_vider')
        self.assertFalse(self.appeler('depense_preparer', {'date': '2026-10-20', 'montant': 1, 'categorie': self.code_t2125(),
                                                           'fournisseur': 'secret de Marie 4511', 'facture': 'recu.pdf'})['isError'])
        prepare = self.appeler('finances_lot_preparer')['structuredContent']
        self.assertEqual(prepare['confirmation'], 'go')
        self.assertFalse(self.appeler('WR_SAVE_FINANCES_BD', {'code': prepare['code']})['isError'])
        ranges = [str(p.relative_to(factures.drive())) for p in factures.drive().rglob('*.pdf')]
        self.assertEqual(ranges, [f'{factures.FOLDER}/2026/2026-10-20_1.00.pdf'])

    def ecriture_de_smith(self, montant):
        """Un paiement du Dojo sur le go, écrit par le vrai code de Smith dans un autre processus, sur le même signal et le même
        journal; l'attente du verrou y est courte. Rend la réponse de WR_SAVE_DOJO_BD."""
        code = ('import json, sys\nfrom socle import confirmation, signal_texte\nsignal_texte.VERROU_SECONDES = 0.2\n'
                'confirmation.AUTOMATIC = True\nfrom scripts.smith_mcp import serveur_fictif\n'
                's = serveur_fictif(sys.argv[1])\n'
                'def appeler(nom, arguments=None):\n'
                " return s.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': nom, 'arguments': arguments or {}}})['result']\n"
                f"appeler('paiement_preparer', {{'membre': 'Alice Exemple', 'montant': {montant}, 'mode': 'comptant'}})\n"
                "print(json.dumps(appeler('WR_SAVE_DOJO_BD', {'code': appeler('lot_preparer')['structuredContent']['code']})))\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(('UGOD_', 'MONETA_'))}
        env.update(UGOD_SIGNAL_TEXTE=os.environ['UGOD_SIGNAL_TEXTE'], PYTHONPATH=str(SMITH / 'assistant'),
                   PYTHONDONTWRITEBYTECODE='1')
        fait = subprocess.run([str(SMITH / '.venv' / 'bin' / 'python'), '-c', 'import core\n' + code, self.folder],
                              cwd=SMITH / 'assistant', env=env, capture_output=True, text=True, timeout=60)
        if fait.returncode:
            raise AssertionError(fait.stderr[-2000:])
        return json.loads(fait.stdout.strip().splitlines()[-1])

    def test_deux_ecritures_sur_le_go_ne_depassent_pas_le_plafond_du_jour(self):
        # F7 : deux processus lisaient le même cumul (100 $ déjà écrits) et passaient chacun 900 $ sur le go.
        self.appeler('depense_preparer', {'date': '2026-10-20', 'montant': 100, 'categorie': self.code_t2125()})
        self.appeler('WR_SAVE_FINANCES_BD', {'code': self.appeler('finances_lot_preparer')['structuredContent']['code']})
        self.appeler('depense_preparer', {'date': '2026-10-20', 'montant': 900, 'categorie': self.code_t2125()})
        prepare = self.appeler('finances_lot_preparer')['structuredContent']
        self.assertEqual(prepare['confirmation'], 'go')
        # L'écriture du Dojo arrive entre la décision de Moneta et son intention, verrou pris.
        vrai, vu = journal_ecritures.intention, []

        def entrelace(*a, **k):
            if not vu:
                vu.append(self.ecriture_de_smith(900))
            return vrai(*a, **k)
        with patch.object(journal_ecritures, 'intention', entrelace):
            moneta = self.appeler('WR_SAVE_FINANCES_BD', {'code': prepare['code']})
        self.assertFalse(moneta['isError'], moneta)
        self.assertTrue(vu[0]['isError'], vu[0])  # occupé : rien n'est enregistré, à refaire
        self.assertLessEqual(journal_ecritures.go_du_jour(date.today()), 1000)


if __name__ == '__main__':
    unittest.main()
