"""Moneta : tous les comptes (Finances de Smith jusqu'à la spec 013). Dans ce jalon, des sources lisibles sans Autoriser seulement (constitution V)."""
from moneta.agent.droits import Agent, Outil, Source, gestion, lecture, preparation

AGENT = Agent(
    id='moneta', nom='Moneta', statut='en construction',
    outils=(
        Outil('facture_deposer', 'depot'),
        *lecture('finances_reference', 'finances_depenses', 'finances_revenus', 'finances_rapport', 'finances_historique',
                 'banque_a_classer', 'banque_operations', 'banque_resume', 'releves_manquants', 'factures_a_classer',
                 'facture_voir', 'points_japon', 'budget_etat'),
        # Ajouter ou modifier : go (spec 002, R2); annuler ou retirer : fenêtre. Une règle aussi (F11, D091) : elle classe
        # d'un coup et classera les relevés à venir.
        *preparation('finances_lot',
                     go=('depense_preparer', 'depense_completer_preparer', 'revenu_preparer', 'releve_import_preparer',
                         'operation_classer_preparer', 'objectif_points_preparer', 'budget_preparer'),
                     fenetre=('regle_preparer', 'depense_annulation_preparer', 'revenu_annulation_preparer', 'regle_retirer_preparer',
                              'objectif_points_retirer_preparer', 'budget_retirer_preparer')),
        *gestion('finances_lot'),
        Outil('WR_SAVE_FINANCES_BD', 'ecriture', 'finances_lot'),
        Outil('WR_SAVE_RAPPORT_FICHIER', 'ecriture'),
    ),
    # D090 : montants, catégories et totaux; les descriptions bancaires, les fournisseurs et les messages Interac sont
    # jugés par la barrière de chaque outil (R9 : retenus, ou marqués Autrui sur demande). Les factures (image, nom de
    # fichier déposé dans Drive) n'y sont pas : leur texte est celui du fournisseur.
    sans_autrui=('facture_deposer', 'finances_reference', 'finances_depenses', 'finances_revenus', 'finances_rapport', 'finances_historique',
                 'banque_a_classer', 'banque_operations', 'banque_resume', 'releves_manquants', 'points_japon',
                 'budget_etat', 'depense_preparer', 'depense_completer_preparer', 'revenu_preparer',
                 'releve_import_preparer', 'operation_classer_preparer', 'regle_preparer', 'objectif_points_preparer',
                 'budget_preparer', 'depense_annulation_preparer', 'revenu_annulation_preparer', 'regle_retirer_preparer',
                 'objectif_points_retirer_preparer', 'budget_retirer_preparer', 'finances_lot_voir', 'finances_lot_retirer',
                 'finances_lot_vider', 'finances_lot_preparer', 'WR_SAVE_FINANCES_BD', 'WR_SAVE_RAPPORT_FICHIER'),
    sources=(
        Source('revenus_dojo_annee', 'liste', ('date', 'description', 'montant'), requiert='finances_rapport'),
        Source('depenses_dojo_annee', 'liste', ('date', 'description', 'montant'), requiert='finances_rapport'),
        Source('cadran_finances', 'liste', ('nom', 'fait', 'total'), requiert='banque_a_classer'),
        Source('points_solde', 'nombre', requiert='points_japon'),
        Source('points_objectif', 'nombre', requiert='points_japon'),
        Source('revenus_dojo_annee_total', 'nombre', requiert='finances_rapport'),
        Source('depenses_dojo_annee_total', 'nombre', requiert='finances_rapport'),
    ),
)
