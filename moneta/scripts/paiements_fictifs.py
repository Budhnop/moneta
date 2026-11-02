#!/usr/bin/env python3
"""Le smith-lecture-paiements du monde fictif de bin/moneta-mcp --fictif : aucun paiement, dans la forme exacte du contrat.

Le serveur fictif ne lance jamais le vrai programme de Smith, qui lirait la vraie base du Dojo. Bibliothèque standard seulement.
"""
import json
import sys

if len(sys.argv) != 3 or sys.argv[1] not in ('totaux', 'lignes') or not sys.argv[2].isdigit():
    sys.exit(2)
annee = int(sys.argv[2])
vide = {'comptant': 0, 'virement': 0, 'non_precise': 0}
valeur = {'montants': vide, 'nombres': vide} if sys.argv[1] == 'totaux' else {'lignes': []}
sys.stdout.write(json.dumps({'version': 1, 'commande': sys.argv[1], 'annee': annee, **valeur}) + '\n')
