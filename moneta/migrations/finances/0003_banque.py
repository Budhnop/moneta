"""Finances phase 2 : compte Perso, relevés bancaires importés (OFX), catégories perso et règles de classement.

Le numéro de compte n'est jamais stocké : seulement son empreinte (pour reconnaître le compte) et un aperçu masqué.
Une opération classée en dépense du dojo crée la dépense T2125 correspondante (depense_id); un dépôt du dojo est rapproché
des paiements déjà comptés dans dojo.db, sans devenir un revenu.
"""

TABLES = (
    """CREATE TABLE comptes_bancaires (
    id INTEGER PRIMARY KEY,
    compte_id INTEGER NOT NULL REFERENCES comptes(id),
    empreinte TEXT NOT NULL UNIQUE CHECK (length(empreinte) = 64),
    apercu TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('banque', 'carte')),
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
    """CREATE TABLE categories_perso (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    nom TEXT NOT NULL,
    note TEXT,
    actif INTEGER NOT NULL DEFAULT 1
)""",
    """CREATE TABLE imports (
    id INTEGER PRIMARY KEY,
    fichier TEXT NOT NULL,
    empreinte TEXT NOT NULL UNIQUE CHECK (length(empreinte) = 64),
    format TEXT NOT NULL CHECK (format IN ('ofx')),
    lignes INTEGER NOT NULL,
    nouvelles INTEGER NOT NULL,
    importe_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
    # cible : code T2125 (dépense du dojo) ou code de categories_perso. pct : pourcentage déductible, seulement pour 9281.
    """CREATE TABLE regles (
    id INTEGER PRIMARY KEY,
    motif TEXT NOT NULL UNIQUE CHECK (length(motif) BETWEEN 3 AND 60),
    cible TEXT NOT NULL,
    pct INTEGER CHECK (pct IS NULL OR pct BETWEEN 0 AND 100),
    actif INTEGER NOT NULL DEFAULT 1 CHECK (actif IN (0, 1)),
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
    # montant_cents signé : négatif = sortie, positif = entrée. classe NULL = à classer.
    """CREATE TABLE operations (
    id INTEGER PRIMARY KEY,
    compte_bancaire_id INTEGER NOT NULL REFERENCES comptes_bancaires(id),
    import_id INTEGER NOT NULL REFERENCES imports(id),
    cle TEXT NOT NULL CHECK (length(cle) = 64),
    date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    montant_cents INTEGER NOT NULL CHECK (montant_cents <> 0),
    description TEXT NOT NULL,
    classe TEXT CHECK (classe IS NULL OR classe IN ('perso', 'depense_dojo')),
    categorie_perso_id INTEGER REFERENCES categories_perso(id),
    depense_id INTEGER REFERENCES depenses(id),
    regle_id INTEGER REFERENCES regles(id),
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (compte_bancaire_id, cle),
    CHECK ((classe IS NULL AND categorie_perso_id IS NULL AND depense_id IS NULL)
        OR (classe = 'perso' AND categorie_perso_id IS NOT NULL AND depense_id IS NULL)
        OR (classe = 'depense_dojo' AND depense_id IS NOT NULL AND categorie_perso_id IS NULL AND montant_cents < 0))
)""",
    "CREATE INDEX idx_operations_date ON operations(date)",
    "CREATE INDEX idx_operations_classe ON operations(classe)",
)

# Liste validée par Ugo le 2026-10-04. depot_dojo : entrée du compte Dojo déjà comptée dans les paiements de dojo.db.
CATEGORIES_PERSO = (
    ("logement", "Logement", None),
    ("epicerie", "Épicerie", None),
    ("restaurants", "Restaurants", None),
    ("transport", "Transport et auto", None),
    ("sante", "Santé", None),
    ("assurances", "Assurances", None),
    ("telecom", "Télécom et abonnements", None),
    ("loisirs", "Loisirs", None),
    ("vetements", "Vêtements", None),
    ("cadeaux", "Cadeaux et dons", None),
    ("frais_bancaires", "Frais bancaires", None),
    ("revenus", "Revenus", "Salaire et autres revenus personnels."),
    ("virement_interne", "Virements internes", "Entre vos propres comptes : ni revenu ni dépense."),
    ("depot_dojo", "Dépôt du dojo (déjà compté)", "Entrée du compte Dojo déjà comptée dans les paiements ou les autres revenus."),
)


def deja_appliquee(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations'").fetchone() is not None


def appliquer(con):
    for statement in TABLES:
        con.execute(statement)
    con.execute("INSERT INTO comptes(nom) VALUES ('Perso')")
    con.executemany("INSERT INTO categories_perso(code, nom, note) VALUES (?, ?, ?)", CATEGORIES_PERSO)
