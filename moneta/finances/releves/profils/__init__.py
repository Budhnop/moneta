"""Profils de relevés PDF : un module par source (contracts/profil-releve.md de la spec 003).

Un profil est ajouté quand Ugo a son premier vrai relevé de cette source; d'ici là, la source est refusée. Chaque module
expose SOURCE, reconnait(pages) et lire(pages). Le registre est une liste fixe : rien n'est chargé par son nom.
"""
SOURCES = ('folio_desjardins', 'cobalt', 'carte_desjardins', 'marge_desjardins')
NOMS = {'folio_desjardins': 'folio Desjardins', 'cobalt': 'carte American Express Cobalt',
        'carte_desjardins': 'carte de crédit Desjardins', 'marge_desjardins': 'marge de crédit Desjardins'}

REGISTRE = []


def registre():
    """The profiles in use: the real ones, plus the fictitious ones of the tests only on fictitious data."""
    import moneta  # noqa: F401  déclare Moneta au socle
    from socle import confirmation
    if not confirmation.fictitious():
        return list(REGISTRE)
    from moneta.finances.releves.profils import fictif
    return [*REGISTRE, fictif.FOLIO, fictif.COBALT, fictif.MARGE]
