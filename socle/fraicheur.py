"""L'âge de la dernière copie vérifiée de chaque destination d'une sauvegarde (spec 018 de Smith, R5) : le Mac, le disque
externe et le dossier Drive du Mac. Le jugement est commun à tous les agents; l'agent donne ses lignes de journal déjà
analysées, ce module ne lit aucun fichier.

derniere() trouve la date, juger() en fait un contrôle du vérificateur (contrat --json version 2 : ok, attention, texte,
corriger). Les seuils sont des constantes : ils changent plus tard sans changer de spec."""
from datetime import date, datetime
import re

ATTENTION_JOURS = 2  # au-delà : ok, mais avec attention
ERREUR_JOURS = 7  # au-delà, ou aucune copie : pas ok
LIBELLES = {'mac': 'Mac', 'disque': 'disque', 'drive': 'Drive'}
LONGUEUR = 200
JOUR = re.compile(r'\d{4}-\d{2}-\d{2}')


def derniere(lignes, etape, motif_image, par_ligne=False):
    """La date de la dernière copie vérifiée de l'étape, ou None. lignes : (moment, niveau, étape, détail) déjà analysés.

    Seules comptent les lignes OK de l'étape. Par défaut, la date est celle que porte le NOM de l'image la plus récente que
    ces lignes nomment (motif_image décide ce qui est une image de l'agent; « <sorte>/<image> » vaut « <image> »); une ligne
    « à jour » ne nomme aucune image et ne compte pas. Avec par_ligne, c'est la date de la dernière ligne OK de l'étape
    (la copie en clair du Mac de Smith et de Moneta)."""
    motif = re.compile(motif_image) if isinstance(motif_image, str) else motif_image
    trouvee = None
    for moment, niveau, nom_etape, detail in lignes:
        if niveau != 'OK' or nom_etape != etape:
            continue
        if par_ligne:
            jour = moment.date() if isinstance(moment, datetime) else moment
        else:
            mots = str(detail).split()
            image = mots[0].rsplit('/', 1)[-1] if mots else ''
            if not motif.match(image) or not JOUR.search(image):
                continue
            try:
                jour = date.fromisoformat(JOUR.search(image).group(0))
            except ValueError:
                continue
        if trouvee is None or jour > trouvee:
            trouvee = jour
    return trouvee


def _jour(valeur):
    return valeur.date() if isinstance(valeur, datetime) else valeur


def corriger(agent, destination, label):
    if destination == 'disque':
        return f'brancher PRO-HD1, puis launchctl kickstart gui/$UID/{label}'
    if destination == 'drive':
        return f'ouvrir Google Drive sur le Mac, puis launchctl kickstart gui/$UID/{label}'
    return f'voir {agent.lower()}-sauvegarde-quotidienne etat'


def juger(agent, destination, derniere, aujourdhui, label):
    """Le contrôle de la destination ('mac', 'disque' ou 'drive') : {ok, attention, texte, corriger}.

    Jusqu'à ATTENTION_JOURS jours : ok. Jusqu'à ERREUR_JOURS : ok avec attention (jamais un échec). Au-delà, ou sans copie :
    pas ok. Texte et corriger tiennent en 200 caractères et ne disent que l'agent, la destination, des dates et des
    commandes. corriger est vide quand tout va bien. label : la tâche de nuit (launchctl kickstart)."""
    if destination not in LIBELLES:
        raise ValueError(f'Destination inconnue : {destination!r} (mac, disque ou drive).')
    suffixe = ' (dossier Drive du Mac)' if destination == 'drive' else ''
    tete = f'{agent}, {LIBELLES[destination]} : '
    derniere, aujourdhui = _jour(derniere), _jour(aujourdhui)
    age = None if derniere is None else max((aujourdhui - derniere).days, 0)
    if age is None or age > ERREUR_JOURS:
        texte = f'{tete}aucune copie vérifiée depuis {ERREUR_JOURS} jours (dernière : {derniere.isoformat() if derniere else "aucune"})'
        ok, attention = False, False
    elif age > ATTENTION_JOURS:
        texte = f'{tete}copie vérifiée du {derniere.isoformat()} ({age} jours)'
        ok, attention = True, True
    else:
        texte = f'{tete}copie vérifiée du {derniere.isoformat()}'
        ok, attention = True, False
    texte = texte + suffixe
    # Un nom d'agent démesuré est coupé avec le reste : le contrat borne le texte à 200 caractères.
    return {'ok': ok, 'attention': attention, 'texte': texte[:LONGUEUR],
            'corriger': '' if ok and not attention else corriger(agent, destination, label)[:LONGUEUR]}
