"""Les règles de confirmation de Moneta : ce que la politique du socle lit par la déclaration (spec 013, mêmes valeurs que Smith).

Le cours d'une décision vit dans socle.politique; le lot, l'argent et les libellés sont à Moneta. Aucun juge propre : pas de calendrier.
"""
from decimal import Decimal

from socle.declaration import Regles

MONTANT_MAX = Decimal('1000.00')
ETAPES_MAX = 10
# Le lot de E1; tout autre garde sa fenêtre.
LOTS = ('finances_lot',)
# L'argent que chaque étape écrit, par le champ de son résumé (F11, D091) : un classement T2125 compte la dépense qu'il fait.
# Un classement sans depense_t2125 (une catégorie perso) ne compte rien.
AVEC_MONTANT = {'finances_lot': {'depense': 'montant', 'revenu': 'montant', 'classer': 'depense_t2125'}}
FACULTATIF = {('finances_lot', 'classer')}
# La raison qu'une étape tenue hors du go donne, par sa préparation (par défaut : suppression).
RAISON_FENETRE = {'regle_preparer': 'reglage'}
RAISONS = {
    # Plus jamais ajoutée (D098, spec 008) : l'app suit les règles du chat. Libellé gardé tel quel (table de l'app
    # Calendrier) pour les anciennes lignes du journal.
    'app': "Demandée depuis l'app Smith : la fenêtre du Mac y reste la règle (D093).",
    'texte_autrui_recent': "Du texte écrit par d'autres a été lu il y a moins de 30 minutes.",
    'signal_douteux': "Le serveur ne sait pas si du texte d'autrui a été lu récemment.",
    'sortie': 'Cette écriture peut quitter le Mac (calendrier ou liste partagés, invités).',
    'suppression': 'Suppression, annulation ou retrait.',
    'reglage': 'Règle de classement : elle classe aussi les opérations à venir.',
    'hors_liste': 'Écriture hors de la liste des écritures locales.',
    'plafond_montant': 'Plus de 1 000 $ dans ce lot.',
    'plafond_jour': "Plus de 1 000 $ écrits sans fenêtre aujourd'hui.",
    'plafond_etapes': 'Plus de 10 étapes dans ce lot.',
    'configuration_absente': 'Calendriers privés non configurés.',
    'app_calendrier_ancienne': 'App Calendrier trop ancienne pour écrire sans fenêtre.',
    'doute': 'Le serveur a un doute.',
}
# L'argument des lectures qui peuvent donner le texte d'autrui (contrat barrieres-lecture).
INCLURE = {'type': 'boolean', 'description': "vrai seulement quand Ugo demande lui-même ce texte; il sera marqué texte_externe et "
                                             "ouvrira la fenêtre des écritures pendant 30 minutes"}


REGLES = Regles(lots=LOTS, avec_montant=AVEC_MONTANT, facultatif=frozenset(FACULTATIF), raison_fenetre=RAISON_FENETRE, raisons=RAISONS,
                montant_max=MONTANT_MAX, etapes_max=ETAPES_MAX, juges={}, hors_wr=('facture_deposer',))
