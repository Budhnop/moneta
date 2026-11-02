"""Dépenses familiales (spec 004) : cartes additionnelles de la Cobalt, titulaire de chaque opération, budgets par catégorie.

Une carte additionnelle n'est gardée que par son empreinte et son aperçu masqué, rattachée au compte de la carte; au plus
une est en vigueur par compte, une carte remplacée reste acceptée. La colonne operations.carte_additionnelle_id (NULL :
titulaire principal) sert au contrôle seulement : aucune lecture ne la rend (FR-004).

Un budget est une ligne par version, à partir d'un mois; montant NULL : retiré à partir de ce mois. Rien n'est effacé : une
ligne au même mois de début est marquée remplacée. La catégorie de dépense et la plage des mois sont vérifiées par
finances.py à l'écriture (la plage avance avec le temps). Aucune table reconstruite : rien à couper.
"""

TABLES = (
    """CREATE TABLE cartes_additionnelles (
    id INTEGER PRIMARY KEY,
    compte_bancaire_id INTEGER NOT NULL REFERENCES comptes_bancaires(id),
    empreinte TEXT NOT NULL UNIQUE CHECK (length(empreinte) = 64),
    apercu TEXT NOT NULL UNIQUE,
    rattachee_le TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    remplacee_le TEXT
)""",
    """CREATE UNIQUE INDEX idx_carte_additionnelle_en_vigueur ON cartes_additionnelles(compte_bancaire_id)
    WHERE remplacee_le IS NULL""",
    "ALTER TABLE operations ADD COLUMN carte_additionnelle_id INTEGER REFERENCES cartes_additionnelles(id)",
    """CREATE TABLE budgets (
    id INTEGER PRIMARY KEY,
    categorie_perso_id INTEGER NOT NULL REFERENCES categories_perso(id),
    debut TEXT NOT NULL CHECK (debut GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
                               AND substr(debut, 6, 2) BETWEEN '01' AND '12'),
    montant_cents INTEGER CHECK (montant_cents IS NULL OR montant_cents BETWEEN 1 AND 10000000),
    cree_le TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    remplace_le TEXT
)""",
    "CREATE UNIQUE INDEX idx_budget_en_vigueur ON budgets(categorie_perso_id, debut) WHERE remplace_le IS NULL",
)


def deja_appliquee(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='budgets'").fetchone() is not None


def appliquer(con):
    for statement in TABLES:
        con.execute(statement)
