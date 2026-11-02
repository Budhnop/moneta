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
    "dojo_consulter": Tool(name="dojo_consulter", domain="dojo", description="Consultation Dojo structurée en lecture seule.",
        min_args=1, max_args=1, access="read", program="dojo_queries"),
    "ajouter": Tool(
        name="ajouter",
        domain="dojo",
        description="Ajouter un membre.",
        min_args=3,
        max_args=5,
        access="write",
        program="dojo",
        command="ajouter",
    ),
    "grade": Tool(
        name="grade",
        domain="dojo",
        description="Modifier le grade d'un membre.",
        min_args=2,
        max_args=4,
        access="write",
        program="dojo",
        command="grade",
    ),
    "desactiver": Tool(
        name="desactiver",
        domain="dojo",
        description="Désactiver un membre.",
        min_args=1,
        max_args=2,
        access="write",
        program="dojo",
        command="desactiver",
    ),
    "reactiver": Tool(
        name="reactiver",
        domain="dojo",
        description="Réactiver un membre, en lui redonnant une famille au besoin.",
        min_args=1,
        max_args=3,
        access="write",
        program="dojo",
        command="reactiver",
    ),
    "changer_famille": Tool(
        name="changer_famille",
        domain="dojo",
        description="Rattacher un membre actif à la famille d'un autre membre, ou à une nouvelle famille.",
        min_args=2,
        max_args=3,
        access="write",
        program="dojo",
        command="changer_famille",
    ),
    "inscrire": Tool(
        name="inscrire",
        domain="dojo",
        description="Inscrire une famille à la session active.",
        min_args=2,
        max_args=3,
        access="write",
        program="dojo",
        command="inscrire",
    ),
    "tarif": Tool(
        name="tarif",
        domain="dojo",
        description="Changer le tarif de l'inscription d'une famille à la session active.",
        min_args=3,
        max_args=3,
        access="write",
        program="dojo",
        command="tarif",
    ),
    "session_creer": Tool(
        name="session_creer",
        domain="dojo",
        description="Créer une session inactive avec ses tarifs.",
        min_args=4,
        max_args=4,
        access="write",
        program="dojo",
        command="session_creer",
    ),
    "session_activer": Tool(
        name="session_activer",
        domain="dojo",
        description="Rendre une session active (l'ancienne devient inactive).",
        min_args=1,
        max_args=1,
        access="write",
        program="dojo",
        command="session_activer",
    ),
    "session_dates": Tool(
        name="session_dates",
        domain="dojo",
        description="Changer les dates d'une session existante.",
        min_args=3,
        max_args=3,
        access="write",
        program="dojo",
        command="session_dates",
    ),
    "renommer": Tool(
        name="renommer",
        domain="dojo",
        description="Corriger le prénom et le nom d'un membre.",
        min_args=4,
        max_args=4,
        access="write",
        program="dojo",
        command="renommer",
    ),
    "dogi": Tool(
        name="dogi",
        domain="dojo",
        description="Inscrire ou changer la taille du dogi d'un membre.",
        min_args=3,
        max_args=3,
        access="write",
        program="dojo",
        command="dogi",
    ),
    "paiement": Tool(
        name="paiement",
        domain="dojo",
        description="Enregistrer un paiement.",
        min_args=2,
        max_args=4,
        access="write",
        program="paiements",
        command="paiement",
    ),
    "paye_ailleurs": Tool(
        name="paye_ailleurs",
        domain="dojo",
        description="Marquer l'inscription d'une famille payée en totalité hors Agent Smith.",
        min_args=2,
        max_args=2,
        access="write",
        program="paiements",
        command="paye_ailleurs",
    ),
    "paye_ailleurs_annuler": Tool(
        name="paye_ailleurs_annuler",
        domain="dojo",
        description="Retirer la marque « payé ailleurs » d'une inscription.",
        min_args=2,
        max_args=2,
        access="write",
        program="paiements",
        command="paye_ailleurs_annuler",
    ),
    "presences": Tool(
        name="presences",
        domain="dojo",
        description="Enregistrer plusieurs présences.",
        min_args=4,
        max_args=4,
        access="write",
        program="presences",
        command="presences",
    ),
    "presences_retraits_preparer": Tool(
        name="presences_retraits_preparer", domain="dojo",
        description="Vérifier une liste précise de présences avant un retrait groupé.",
        min_args=2, max_args=2, access="read", program="presences_correction", command="preparer_groupe",
    ),
    "presences_retirer": Tool(
        name="presences_retirer", domain="dojo",
        description="Retirer atomiquement les présences vérifiées et confirmées, avec backup et audit.",
        min_args=1, max_args=1, access="write", program="presences_correction", command="retirer_groupe",
    ),
    "dojo_reference": Tool(
        name="dojo_reference", domain="dojo",
        description="Référence fermée en lecture : identités (actifs et inactifs), tarifs de la session active et grades.",
        min_args=0, max_args=0, access="read", program="dojo_reference", command="reference",
    ),
    "dojo_seances": Tool(
        name="dojo_seances", domain="dojo",
        description="Séances d'une période avec leur nombre de présences, en lecture.",
        min_args=2, max_args=2, access="read", program="dojo_reference", command="seances",
    ),
    "dojo_historique": Tool(
        name="dojo_historique", domain="dojo",
        description="Dernières entrées du journal d'audit, en lecture.",
        min_args=1, max_args=1, access="read", program="dojo_reference", command="historique",
    ),
    "membre_fiche": Tool(
        name="membre_fiche", domain="dojo",
        description="Fiche complète d'un membre actif (présences, grades, inscriptions et paiements de sa famille), en lecture.",
        min_args=1, max_args=1, access="read", program="dojo_reference", command="fiche",
    ),
    "presences_lire": Tool(
        name="presences_lire",
        domain="dojo",
        description="Consulter les présences enregistrées pour une journée.",
        min_args=1,
        max_args=1,
        access="read",
        program="presences",
        command="consulter",
    ),
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
# Named sources of the app (spec 006): one closed command per agent, its only argument a source that agent declares.
TOOLS.update({f"sources_{agent}": Tool(name=f"sources_{agent}", domain=agent, description=f"Source de lecture nommée de l'agent {agent}.",
                                       min_args=1, max_args=1, access="read", program=f"sources_{agent}")
              for agent in ("dojo", "finances", "publicite", "smith")})
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

    if name == "dojo_consulter":
        import json
        from core.dojo_query import valid_query
        if len(arguments[0]) > 3000:
            return False
        try:
            return valid_query(json.loads(arguments[0]))
        except (ValueError, TypeError):
            return False

    if name == "presences":
        return validate_presences_arguments(arguments)

    if name == "session_creer":
        return validate_session_arguments(arguments)

    if name == "session_dates":
        return validate_session_arguments([*arguments, '[{"nom": "x", "montant": 0}]'])

    if name == "session_activer":
        return 0 < len(arguments[0].strip()) <= 60 and not any(ord(c) < 32 for c in arguments[0])

    if name in {"presences_retraits_preparer", "presences_retirer"}:
        import json
        if len(arguments[-1]) > 200000:
            return False
        try:
            data = json.loads(arguments[-1])
        except (ValueError, TypeError):
            return False
        if name == "presences_retraits_preparer":
            return validate_presence_read_arguments(arguments[:1]) and validate_removal_names(data)
        return validate_presence_removal_batch(data)

    if name == "presences_lire":
        return validate_presence_read_arguments(arguments)

    if name == "paiement":
        modes = [a for a in arguments if a.startswith("--mode=")]
        if len(modes) > 1 or (modes and (modes[0] != arguments[-1] or modes[0] not in {"--mode=comptant", "--mode=virement"})):
            return False
        return len(arguments) - len(modes) <= 3

    if name in {"ajouter", "grade"}:
        import datetime
        passages = [a for a in arguments if a.startswith("--passage=")]
        if len(passages) > 1 or (passages and passages[0] != arguments[-1]):
            return False
        if passages:
            try:
                datetime.date.fromisoformat(passages[0].split("=", 1)[1])
            except ValueError:
                return False
        return len(arguments) - len(passages) <= (4 if name == "ajouter" else 3)

    if name == "dojo_seances":
        import datetime
        try:
            start, end = (datetime.date.fromisoformat(a) for a in arguments)
        except ValueError:
            return False
        return start <= end and (end - start).days <= 400

    if name.startswith("sources_"):
        from agents.registre import REGISTRE
        return REGISTRE.proprietaire(arguments[0]) == name.removeprefix("sources_") and arguments[0] in REGISTRE.sources

    if name == "membre_fiche":
        return 2 <= len(arguments[0].strip()) <= 100 and not any(ord(c) < 32 for c in arguments[0])

    if name in {"dojo_historique", "finances_historique"}:
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



def validate_session_arguments(arguments):
    import json
    from datetime import date
    name, start, end, tariffs = arguments
    if not 0 < len(name.strip()) <= 60 or any(ord(c) < 32 for c in name) or len(tariffs) > 2000:
        return False
    try:
        if date.fromisoformat(end) <= date.fromisoformat(start):
            return False
        tariffs = json.loads(tariffs)
    except (TypeError, ValueError):
        return False
    return (isinstance(tariffs, list) and 0 < len(tariffs) <= 10
            and all(isinstance(t, dict) and set(t) == {"nom", "montant"} and isinstance(t["nom"], str)
                    and 0 < len(t["nom"].strip()) <= 40 and type(t["montant"]) in (int, float) and 0 <= t["montant"] <= 10000
                    for t in tariffs)
            and len({t["nom"].strip().casefold() for t in tariffs}) == len(tariffs))


def validate_presences_arguments(arguments):
    import datetime

    if len(arguments) != 4:
        return False

    date_text, duration_text, activity_type, members_text = arguments

    try:
        datetime.date.fromisoformat(date_text)
    except ValueError:
        return False

    try:
        duration = float(duration_text)
    except ValueError:
        return False

    if not (0 < duration <= 24):
        return False

    if activity_type != "cours":
        return False

    members = [
        name.strip()
        for name in members_text.split(",")
    ]

    if not members or any(not name for name in members):
        return False

    return True


def validate_presence_read_arguments(arguments):
    import datetime

    if len(arguments) != 1:
        return False

    try:
        datetime.date.fromisoformat(arguments[0])
    except ValueError:
        return False

    return True

def validate_presence_removal_snapshot(snapshot):
    import datetime
    import math
    fields = {"date", "session_id", "seance_id", "presence_id", "member_id", "member", "hours", "duration", "source"}
    if not isinstance(snapshot, dict) or set(snapshot) != fields:
        return False
    if not isinstance(snapshot["date"], str):
        return False
    try:
        if datetime.date.fromisoformat(snapshot["date"]).isoformat() != snapshot["date"]:
            return False
    except ValueError:
        return False
    if any(type(snapshot[key]) is not int or snapshot[key] <= 0 for key in ("session_id", "seance_id", "presence_id", "member_id")):
        return False
    if any(type(snapshot[key]) not in (int, float) or not 0 < snapshot[key] <= 24 or not math.isfinite(snapshot[key]) for key in ("hours", "duration")):
        return False
    member = snapshot["member"]
    if not isinstance(member, str) or len(member.split()) < 2 or len(member) > 200 or any(c in member for c in "\n\r\x00"):
        return False
    source = snapshot["source"]
    return source is None or (isinstance(source, str) and len(source) <= 80)


def validate_removal_names(names):
    return (isinstance(names, list) and 1 <= len(names) <= 100
            and all(isinstance(name, str) and 2 <= len(name.split()) and len(name) <= 200
                    and not any(c in name for c in "\n\r\x00") for name in names)
            and len(set(names)) == len(names))


def validate_presence_removal_batch(snapshots):
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 100:
        return False
    if not all(validate_presence_removal_snapshot(row) for row in snapshots):
        return False
    return (len({(row['date'], row['session_id'], row['seance_id']) for row in snapshots}) == 1
            and all(len({row[key] for row in snapshots}) == len(snapshots)
                    for key in ('presence_id', 'member_id', 'member')))
