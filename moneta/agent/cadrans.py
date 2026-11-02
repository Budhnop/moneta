"""Lectures indépendantes des anneaux : aucun texte d'exception ne sort des sources."""
def lire_anneaux(lectures):
    lignes, raisons = [], {}
    for nom, lecture in lectures:
        try:
            fait, total = lecture()
        except Exception:
            fait, total = None, None
            raisons[nom] = 'lecture indisponible'
        lignes.append([nom, fait, total])
    return {'type': 'liste', 'colonnes': ['nom', 'fait', 'total'], 'lignes': lignes, **({'raisons': raisons} if raisons else {})}
