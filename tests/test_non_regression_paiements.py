"""Spec 011 de Smith : rejoue sur Moneta les captures de 3351db7 (tests/references/cas_paiements, copiées telles quelles), sans
ancien code ni régénération des JSON (spec 013, repris de test_non_regression_paiements.py de Smith).

Moneta ne lit plus la base Dojo lui-même : il passe par lecture_dojo, qui contrôle la réponse au champ près. Les cas ci-dessous
(ECARTS) en changent, chacun écrit ici avec son nouveau résultat, jamais masqué : une base Dojo absente, verrouillée ou sans
table donne un refus (ToolError) au lieu de l'erreur SQLite; un montant négatif ou une date avec heure, que l'ancien code
additionnait, sort du contrat et donne un refus. Jamais un zéro. Tous les autres cas sont identiques.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cas_paiements import JOUEURS, REFERENCES
from socle.lots import ToolError

LECTURES = ('comptant', 'virement', 'sans mode', 'mode inconnu', 'mode vide', 'fractions et deux annees',
            'annee precedente', 'annee sans paiement', 'sans famille', 'sans colonne mode',
            'base absente', 'base verrouillee', 'base sans_table')
RAPPORTS = tuple(f'{nom} / {choix} comptant' for nom in LECTURES for choix in ('sans', 'avec')
                 if (nom, choix) != ('base verrouillee', 'avec')) + (
    'annee invalide', 'annee invalide avant base absente')
COMPLEMENTS = (*(f'{j} : 10001 paiements' for j in ('report', 'revenus', 'finances_rapport')),
               *(f'{j} : valeurs limites' for j in ('report', 'revenus', 'detail_annee', 'finances_rapport')),
               'report : Dojo lu avant Finances', 'detail_annee : Finances lu avant Dojo',
               'finances_rapport : message de base absente', 'finances_rapport : annee invalide avant base absente')
MODES_ESPACES = (*(f'{j} : {c} comptant' for j in ('report', 'finances_rapport') for c in ('sans', 'avec')), 'revenus', 'detail_annee')
ATTENDUS = {'complements': COMPLEMENTS, 'modes_espaces': MODES_ESPACES, 'report': RAPPORTS, 'finances_rapport': RAPPORTS, 'revenus': LECTURES,
            'detail_annee': LECTURES + ('plus de 10000 paiements', 'plus de 10000 apres fusion')}

ILLISIBLES = 'Paiements du Dojo illisibles'
REFUS = {'exception': 'ToolError'}
HORS_CONTRAT = {'refus': f'{ILLISIBLES} : réponse hors contrat.'}
REFUSE = f"{ILLISIBLES} : le programme de Dojo a refusé ou n'a pas répondu."
ECARTS = {
    **{('revenus', f'base {b}'): REFUS for b in ('absente', 'verrouillee', 'sans_table')},
    **{('detail_annee', f'base {b}'): REFUS for b in ('absente', 'verrouillee', 'sans_table')},
    **{('report', f'base {b} / {c} comptant'): REFUS
       for b, c in (('absente', 'sans'), ('absente', 'avec'), ('verrouillee', 'sans'), ('sans_table', 'sans'), ('sans_table', 'avec'))},
    **{('complements', f'{j} : valeurs limites'): HORS_CONTRAT for j in ('report', 'revenus', 'detail_annee')},
    ('complements', 'finances_rapport : valeurs limites'): {'refus': f'Finances : {ILLISIBLES} : réponse hors contrat.'},
    ('complements', 'report : Dojo lu avant Finances'): {'exception': 'ToolError', 'message': REFUSE},
    ('complements', 'finances_rapport : message de base absente'): {'exception': 'ToolError', 'message': f'Finances : {REFUSE}'},
}


def lire(famille):
    return json.loads((REFERENCES / f'{famille}.json').read_text(encoding='utf-8'))


def jouer(famille, entree):
    """Le joueur; un refus que le joueur laisse passer (sans base ni message demandés) est rendu comme une valeur."""
    try:
        return JOUEURS[famille](entree)
    except ToolError as exc:
        return {'refus': str(exc)}


class TestPaiements(unittest.TestCase):
    def test_le_jeu_est_complet(self):
        self.assertEqual(set(JOUEURS), set(ATTENDUS))
        self.assertEqual({p.stem for p in REFERENCES.glob('*.json')}, set(ATTENDUS))
        for famille, noms in ATTENDUS.items():
            with self.subTest(famille=famille):
                contenu = lire(famille)
                self.assertEqual((contenu['source'], contenu['famille']), ('3351db7', famille))
                self.assertCountEqual([cas['nom'] for cas in contenu['cas']], noms)
                for cas in contenu['cas']:
                    self.assertEqual(set(cas), {'nom', 'entree', 'attendu'})
        # Chaque écart vise un cas qui existe, et dont l'ancien résultat n'était pas déjà un refus de Moneta.
        for (famille, nom), nouveau in ECARTS.items():
            with self.subTest(ecart=(famille, nom)):
                ancien = next(c['attendu'] for c in lire(famille)['cas'] if c['nom'] == nom)
                self.assertNotEqual(ancien, nouveau)

    def rejouer(self, famille):
        for cas in lire(famille)['cas']:
            with self.subTest(cas=cas['nom']):
                sortie = jouer(famille, cas['entree'])
                attendu = ECARTS.get((famille, cas['nom']), cas['attendu'])
                # Le texte JSON : assertEqual seul ne distingue pas 1 de 1.0.
                self.assertEqual(json.dumps(sortie, ensure_ascii=False), json.dumps(attendu, ensure_ascii=False))


for _famille in ATTENDUS:
    setattr(TestPaiements, f'test_{_famille}', lambda self, famille=_famille: self.rejouer(famille))


if __name__ == '__main__':
    unittest.main()
