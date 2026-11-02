"""Finances, phase 1 : comptes, catégories de dépenses (lignes T2125), dépenses en cents, journal d'audit.

Les revenus du dojo ne sont pas recopiés ici : les rapports les lisent dans dojo.db.
"""

TABLES = (
    """CREATE TABLE comptes (
    id INTEGER PRIMARY KEY,
    nom TEXT NOT NULL UNIQUE,
    actif INTEGER NOT NULL DEFAULT 1
)""",
    # pct_deductible NULL : pourcentage à préciser à chaque dépense (ex. véhicule, selon l'usage d'affaires).
    """CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    nom TEXT NOT NULL,
    pct_deductible INTEGER CHECK (pct_deductible IS NULL OR pct_deductible BETWEEN 0 AND 100),
    note TEXT,
    actif INTEGER NOT NULL DEFAULT 1
)""",
    """CREATE TABLE depenses (
    id INTEGER PRIMARY KEY,
    compte_id INTEGER NOT NULL REFERENCES comptes(id),
    date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    montant_cents INTEGER NOT NULL CHECK (montant_cents > 0),
    categorie_id INTEGER NOT NULL REFERENCES categories(id),
    pct_deductible INTEGER NOT NULL CHECK (pct_deductible BETWEEN 0 AND 100),
    fournisseur TEXT,
    description TEXT,
    mode TEXT CHECK (mode IS NULL OR mode IN ('comptant', 'carte', 'virement', 'cheque')),
    piece TEXT,
    annulee INTEGER NOT NULL DEFAULT 0 CHECK (annulee IN (0, 1)),
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
    "CREATE INDEX idx_depenses_date ON depenses(date)",
    """CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_action TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL,
    cible_type TEXT,
    cible_id INTEGER,
    details TEXT,
    source TEXT NOT NULL DEFAULT 'system',
    resultat TEXT NOT NULL
)""",
)

# Lignes du formulaire T2125 (fédéral); le TP-80 québécois suit les mêmes catégories. À valider avec le comptable.
CATEGORIES = (
    ("8521", "Publicité", 100, None),
    ("8523", "Repas et frais de représentation", 50, "Déductible à 50 %."),
    ("8690", "Assurances", 100, None),
    ("8710", "Intérêts et frais bancaires", 100, None),
    ("8760", "Taxes d'affaires, permis, cotisations et affiliations", 100, None),
    ("8810", "Frais de bureau", 100, None),
    ("8811", "Fournitures (dont équipement de dojo)", 100, None),
    ("8860", "Honoraires professionnels (comptable, juridique)", 100, None),
    ("8910", "Loyer (location de salle)", 100, None),
    ("8960", "Entretien et réparations", 100, None),
    ("9200", "Frais de déplacement (tournois, séminaires)", 100, None),
    ("9220", "Services publics", 100, None),
    ("9270", "Autres dépenses", 100, None),
    ("9281", "Frais de véhicule à moteur", None, "Pourcentage d'usage d'affaires à préciser à chaque dépense."),
)


def deja_appliquee(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='depenses'").fetchone() is not None


def appliquer(con):
    for statement in TABLES:
        con.execute(statement)
    con.execute("INSERT INTO comptes(nom) VALUES ('Dojo')")
    con.executemany("INSERT INTO categories(code, nom, pct_deductible, note) VALUES (?, ?, ?, ?)", CATEGORIES)
