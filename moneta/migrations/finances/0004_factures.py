"""Facture liée à une dépense (D063) : chemin du fichier dans « Agent Smith/factures » de Google Drive, une dépense par facture."""


def deja_appliquee(con):
    return any(row[1] == "facture" for row in con.execute("PRAGMA table_info(depenses)"))


def appliquer(con):
    con.execute("ALTER TABLE depenses ADD COLUMN facture TEXT")
    con.execute("CREATE UNIQUE INDEX idx_depenses_facture ON depenses(facture) WHERE facture IS NOT NULL")
