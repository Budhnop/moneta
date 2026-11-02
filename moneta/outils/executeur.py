"""Les programmes fermés de Moneta, lancés par le socle (socle.executeur, spec 013) : la liste et le catalogue sont à Moneta.

Gardés en fonctions de module : les tests et les captures de cas remplacent build_command ici.
"""
from moneta.chemins import PAQUET
from moneta.outils import catalogue
from socle.executeur import ExecutionError, Executeur, sur  # noqa: F401

PROGRAMS = {
    "finances": PAQUET / "finances" / "finances.py",
    "sources_moneta": PAQUET / "agent" / "sources.py",
}

EXECUTEUR = Executeur(PROGRAMS, catalogue)


def build_command(action, arguments):
    return EXECUTEUR.commande(action, arguments)


def execute(action, arguments, timeout=15):
    return EXECUTEUR.lancer(action, build_command(action, arguments), timeout)


def execute_safe(action, arguments, timeout=15):
    return sur(action, lambda: execute(action, arguments, timeout))
