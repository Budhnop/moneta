#!/usr/bin/env python3
"""Un smith-lecture-paiements de test : rend, dans la forme exacte du contrat, les réponses de la spec 011 figées sur Smith f9c1599
(tests/references/cas_moneta/paiements.json). Spec 013, T038, option A choisie par Ugo le 2026-11-02 : le monde de T011 a deux
paiements hors contrainte CHECK que le vrai programme refuse, à raison; les cas qui lisent les paiements les reçoivent d'ici.
Bibliothèque standard seulement. Année absente des cas : code 1, jamais un zéro."""
import json
from pathlib import Path
import sys

FONCTIONS = {'totaux': 'totaux_annee', 'lignes': 'lignes_annee'}

if len(sys.argv) != 3 or sys.argv[1] not in FONCTIONS or not sys.argv[2].isdigit():
    sys.exit(2)
commande, annee = sys.argv[1], int(sys.argv[2])
cas = json.loads((Path(__file__).resolve().parent / 'references' / 'cas_moneta' / 'paiements.json').read_text(encoding='utf-8'))['cas']
trouve = [c['attendu'] for c in cas if c['entree'] == {'today': c['entree']['today'], 'fonction': FONCTIONS[commande], 'annee': annee}]
if len(trouve) != 1:
    print('Lecture des paiements refusée : année hors des cas figés.', file=sys.stderr)
    sys.exit(1)
valeur = {'montants': trouve[0][0], 'nombres': trouve[0][1]} if commande == 'totaux' else {'lignes': trouve[0]}
sys.stdout.write(json.dumps({'version': 1, 'commande': commande, 'annee': annee, **valeur}, ensure_ascii=False) + '\n')
