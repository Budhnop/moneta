"""Objectif de points du voyage au Japon (spec 005) : une ligne par version, une seule en vigueur, rien n'est effacé.

Changer ferme la ligne en vigueur (fin = 'change') et en insère une nouvelle; retirer la ferme (fin = 'retire'). La date
visée « pas dans le passé » est vérifiée par finances.py à l'écriture : avec le temps, elle passe sans rendre la ligne
invalide.
"""

TABLES = (
    """CREATE TABLE objectifs_points (
    id INTEGER PRIMARY KEY,
    cible INTEGER NOT NULL CHECK (cible BETWEEN 1 AND 10000000),
    date_visee TEXT CHECK (date_visee IS NULL OR (date_visee GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'
                                                  AND date(date_visee) IS date_visee)),
    cree_le TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fin_le TEXT,
    fin TEXT CHECK (fin IS NULL OR fin IN ('change', 'retire')),
    CHECK ((fin_le IS NULL) = (fin IS NULL))
)""",
    "CREATE UNIQUE INDEX idx_objectif_points_en_vigueur ON objectifs_points((1)) WHERE fin_le IS NULL",
)


def deja_appliquee(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='objectifs_points'").fetchone() is not None


def appliquer(con):
    for statement in TABLES:
        con.execute(statement)
