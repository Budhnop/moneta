"""Moneta, tous les comptes d'Ugo. Importer ce paquet déclare Moneta au socle, avant que rien ne puisse l'appeler (spec 013)."""
from socle.declaration import declarer

from moneta.declaration import DECLARATION

declarer(DECLARATION)
