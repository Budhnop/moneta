"""Provenance d'une dépense classée depuis une opération bancaire (F2, D091) : depenses.operation_id, posé à la création et
jamais changé. Un reclassement annule la dépense et défait le lien de l'opération, mais son fournisseur, copié de la
banque, reste jugé comme le texte de cette opération (spec 002, R9).

Les dépenses déjà là prennent l'opération qui les lie encore, sinon celle que nomme leur annulation par reclassement
(audit), sinon celle que nomme leur description d'origine (« Relevé bancaire, opération #N »).
"""
import re

RECLASSEMENT = re.compile(r"reclassement de l'opération #(\d+)$")
RELEVE = re.compile(r'Relevé bancaire, opération #(\d+)$')


def deja_appliquee(con):
    return any(row[1] == "operation_id" for row in con.execute("PRAGMA table_info(depenses)"))


def appliquer(con):
    con.execute("ALTER TABLE depenses ADD COLUMN operation_id INTEGER REFERENCES operations(id)")
    found = dict(con.execute("SELECT depense_id, min(id) FROM operations WHERE depense_id IS NOT NULL GROUP BY depense_id"))
    for number, details in con.execute("SELECT cible_id, details FROM audit_log WHERE action='annuler_depense' "
                                       "AND cible_type='depense' ORDER BY id"):
        match = RECLASSEMENT.match(details or '')
        if match and number not in found:
            found[number] = int(match.group(1))
    for number, description in con.execute("SELECT id, description FROM depenses"):
        match = RELEVE.match(description or '')
        if match and number not in found:
            found[number] = int(match.group(1))
    known = {row[0] for row in con.execute("SELECT id FROM operations")}
    con.executemany("UPDATE depenses SET operation_id=? WHERE id=?",
                    [(operation, number) for number, operation in found.items() if operation in known])
