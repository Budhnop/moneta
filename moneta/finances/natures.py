"""The nature of a bank operation, read from its account's type and its description (spec 002, R9, E15).

Classed at read time: the description stays whole in the ledger, and a wrong pattern is fixed without a migration. Cards
and lines of credit (Amex, Visa Desjardins) pass whole. On the Desjardins account, a pattern recognises what the line is;
an Interac transfer gives its direction and the other person's name only, its free message is retained. Anything else is
unknown, hence retained. No pattern until Ugo has looked at a real statement himself: every Desjardins line is unknown.
"""
import re

NATURES = ('carte', 'achat_carte', 'paiement_carte', 'achat_interac', 'paiement_facture', 'retrait', 'frais', 'depot_paie',
           'prelevement', 'virement_interac', 'inconnue')
# (nature, regular expression matched against the whole description, direction of a transfer: recu, envoye or None).
# A transfer's expression names the other person in the group nom and its free text in the group message.
MOTIFS_BANQUE = ()
SENS = {'recu': 'reçu de', 'envoye': 'envoyé à'}


def classer(compte_type, description):
    """(nature, description to show or None, whether a free message was retained)."""
    if compte_type in ('carte', 'marge'):
        return 'carte', description, False
    if compte_type == 'banque':
        for nature, motif, sens in MOTIFS_BANQUE:
            found = re.fullmatch(motif, description or '')
            if not found:
                continue
            if nature == 'virement_interac':
                return nature, f"Virement Interac {SENS[sens]} {found.group('nom')}", bool(found.groupdict().get('message'))
            return nature, description, False
    return 'inconnue', None, False
