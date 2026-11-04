"""Validation du sous-ensemble JSON Schema des registres, sans dépendance ni coercition."""
from copy import deepcopy
import math

CLES = {'type', 'properties', 'required', 'items', 'anyOf', 'default', '$defs', '$ref', 'title', 'description'}
TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}


class Schema:
    def __init__(self, document):
        self.document = deepcopy(document)
        self._verifier(self.document, set())

    def _reference(self, nom):
        if type(nom) is not str or not nom.startswith('#/$defs/'):
            raise ValueError('Schéma refusé : référence locale attendue.')
        courant = self.document
        try:
            for cle in nom[2:].split('/'):
                courant = courant[cle.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            raise ValueError('Schéma refusé : référence locale introuvable.') from None
        return courant

    def _verifier(self, s, pile):
        if not isinstance(s, dict) or set(s) - CLES:
            raise ValueError('Schéma refusé : mot-clé hors du sous-ensemble.')
        if id(s) in pile:
            raise ValueError('Schéma refusé : référence cyclique.')
        pile = pile | {id(s)}
        if 'type' in s and (type(s['type']) is not str or s['type'] not in TYPES):
            raise ValueError('Schéma refusé : type inconnu.')
        for cle in ('title', 'description'):
            if cle in s and type(s[cle]) is not str:
                raise ValueError(f'Schéma refusé : {cle} doit être du texte.')
        for cle in ('properties', '$defs'):
            if cle in s:
                if not isinstance(s[cle], dict) or not all(type(k) is str for k in s[cle]):
                    raise ValueError(f'Schéma refusé : {cle} doit être un objet.')
                for enfant in s[cle].values():
                    self._verifier(enfant, pile)
        if 'required' in s and (not isinstance(s['required'], list) or not all(type(k) is str for k in s['required'])):
            raise ValueError('Schéma refusé : required doit être une liste de noms.')
        if 'items' in s:
            self._verifier(s['items'], pile)
        if 'anyOf' in s:
            if not isinstance(s['anyOf'], list) or not s['anyOf']:
                raise ValueError('Schéma refusé : anyOf doit contenir des schémas.')
            for enfant in s['anyOf']:
                self._verifier(enfant, pile)
        if '$ref' in s:
            self._verifier(self._reference(s['$ref']), pile)
        if 'default' in s:
            self._valider(s, deepcopy(s['default']), 'default')

    def valider(self, arguments):
        return self._valider(self.document, deepcopy(arguments), 'arguments')

    def _valider(self, s, valeur, nom):
        def refus(message):
            raise ValueError(f'Argument « {nom} » : {message}.')
        if '$ref' in s:
            valeur = self._valider(self._reference(s['$ref']), valeur, nom)
        if 'anyOf' in s:
            for choix in s['anyOf']:
                try:
                    valeur = self._valider(choix, deepcopy(valeur), nom)
                    break
                except ValueError:
                    pass
            else:
                refus('aucun des types permis ne convient')
        typ = s.get('type')
        valide = {'object': type(valeur) is dict, 'array': type(valeur) is list,
                  'string': type(valeur) is str, 'integer': type(valeur) is int,
                  'number': type(valeur) in (int, float) and (type(valeur) is int or math.isfinite(valeur)),
                  'boolean': type(valeur) is bool, 'null': valeur is None}
        if typ and not valide[typ]:
            refus(f'type attendu : {typ}')
        if isinstance(valeur, dict):
            for cle in s.get('required', []):
                if cle not in valeur:
                    raise ValueError(f'Argument « {nom}.{cle} » : obligatoire.')
            for cle, enfant in s.get('properties', {}).items():
                if cle not in valeur and 'default' in enfant:
                    valeur[cle] = deepcopy(enfant['default'])
                if cle in valeur:
                    valeur[cle] = self._valider(enfant, valeur[cle], f'{nom}.{cle}')
        if isinstance(valeur, list) and 'items' in s:
            valeur = [self._valider(s['items'], v, f'{nom}[{i}]') for i, v in enumerate(valeur)]
        return valeur


def charger(document):
    """Vérifie le schéma dès le chargement; valider rend une copie avec ses valeurs par défaut."""
    return Schema(document)
