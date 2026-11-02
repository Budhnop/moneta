"""Le catalogue fermé de Moneta : les programmes et leurs arguments permis (repris de core/tools.py de Smith f9c1599, spec 013).
La liste blanche des actions du socle (socle.executeur) lit ce module."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str
    description: str
    min_args: int
    max_args: int
    access: str
    program: str
    command: Optional[str] = None


TOOLS = {
    # Finances: one closed JSON argument per command, validated again by the script.
    **{name: Tool(name=name, domain="finances", description=description, min_args=1, max_args=1, access=access,
                  program="finances", command=command)
       for name, command, access, description in (
           ("finances_depense", "depense", "write", "Ajouter une dépense."),
           ("finances_completer", "completer", "write", "Compléter les champs facultatifs d'une dépense."),
           ("finances_annuler", "annuler", "write", "Annuler une dépense (conservée dans l'historique)."),
           ("finances_depenses", "depenses", "read", "Lister les dépenses."),
           ("finances_revenu", "revenu", "write", "Ajouter un autre revenu."),
           ("finances_annuler_revenu", "annuler_revenu", "write", "Annuler un autre revenu (conservé dans l'historique)."),
           ("finances_revenus", "revenus", "read", "Lister les autres revenus."),
           ("finances_rapport", "rapport", "read", "Rapport annuel : revenus du dojo et dépenses par ligne T2125."),
           ("finances_historique", "historique", "read", "Journal d'audit des finances."),
           ("finances_importer", "importer", "write", "Importer un relevé bancaire OFX du dossier imports."),
           ("finances_classer", "classer", "write", "Classer une opération bancaire."),
           ("finances_regle", "regle", "write", "Ajouter une règle de classement et l'appliquer aux opérations à classer."),
           ("finances_retirer_regle", "retirer_regle", "write", "Retirer une règle de classement."),
           ("finances_import_analyser", "import_analyser", "read", "Analyser un relevé bancaire sans l'importer."),
           ("finances_regle_analyser", "regle_analyser", "read", "Opérations à classer qu'une règle classerait."),
           ("finances_operations", "operations", "read", "Lister les opérations bancaires."),
           ("finances_resume", "resume", "read", "Résumé des opérations bancaires par catégorie."),
           ("finances_manquants", "manquants", "read", "Relevés manquants et ruptures de solde depuis janvier 2026."),
           ("finances_points", "points", "read", "Points de la Cobalt et objectif du voyage au Japon (totaux seulement)."),
           ("finances_objectif", "objectif", "write", "Fixer ou changer l'objectif de points du voyage au Japon."),
           ("finances_retirer_objectif", "retirer_objectif", "write", "Retirer l'objectif de points du voyage au Japon."),
           ("finances_budget", "budget", "read", "État du budget familial du compte Perso (totaux par catégorie et par mois)."),
           ("finances_budget_fixer", "budget_fixer", "write", "Fixer ou changer le budget mensuel d'une catégorie perso."),
           ("finances_budget_retirer", "budget_retirer", "write", "Retirer le budget d'une catégorie perso à partir d'un mois."),
       )},
    "finances_reference": Tool(name="finances_reference", domain="finances", description="Comptes, catégories et modes.",
        min_args=0, max_args=0, access="read", program="finances", command="reference"),
}
# Named sources of the app (spec 006): one closed command, its only argument a source Moneta declares.
TOOLS["sources_moneta"] = Tool(name="sources_moneta", domain="moneta", description="Source de lecture nommée de l'agent moneta.",
                               min_args=1, max_args=1, access="read", program="sources_moneta")
# The annual report takes an optional « comptant » after the year (D058).
TOOLS["finances_rapport"] = Tool(name="finances_rapport", domain="finances", description=TOOLS["finances_rapport"].description,
                                 min_args=1, max_args=2, access="read", program="finances", command="rapport")



FINANCES_JSON = {"finances_depense", "finances_completer", "finances_annuler", "finances_depenses",
                 "finances_revenu", "finances_annuler_revenu", "finances_revenus", "finances_importer", "finances_classer",
                 "finances_regle", "finances_retirer_regle", "finances_import_analyser", "finances_regle_analyser",
                 "finances_operations", "finances_resume", "finances_manquants", "finances_points", "finances_objectif",
                 "finances_retirer_objectif", "finances_budget", "finances_budget_fixer", "finances_budget_retirer"}



def get_tool(name):
    return TOOLS.get(name)


def allowed(name):
    return name in TOOLS


def validate_arguments(name, arguments):
    tool = get_tool(name)

    if tool is None:
        return False

    if not isinstance(arguments, list):
        return False

    if not all(isinstance(arg, str) for arg in arguments):
        return False

    if not (tool.min_args <= len(arguments) <= tool.max_args):
        return False

    if name == "sources_moneta":
        from moneta.agent.droits import REGISTRE
        return REGISTRE.proprietaire(arguments[0]) == "moneta" and arguments[0] in REGISTRE.sources

    if name == "finances_historique":
        return arguments[0].isdigit() and 1 <= int(arguments[0]) <= 100

    if name == "finances_rapport":
        return arguments[0].isdigit() and len(arguments[0]) == 4 and arguments[1:] in ([], ["comptant"])

    if name in FINANCES_JSON:
        # Closed field checks happen in the finances script; here only shape and size.
        import json
        if len(arguments[0]) > 4000:
            return False
        try:
            return isinstance(json.loads(arguments[0]), dict)
        except ValueError:
            return False

    return True
