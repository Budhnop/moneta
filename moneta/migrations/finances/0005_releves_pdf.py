"""Relevés PDF (spec 003) : imports au format pdf avec leur source et leur période, comptes de type marge, soldes de chaque
relevé (contrôle et chaîne des relevés manquants), points des relevés Cobalt, comptes qu'Ugo a dit d'ignorer.

SQLite ne change pas une contrainte CHECK : imports et comptes_bancaires sont reconstruits (créer, copier, retirer,
renommer), clés étrangères coupées pour cette migration seulement; le lanceur vérifie toutes les références avant le COMMIT.
Les imports OFX déjà faits reçoivent leur période : première et dernière date de leurs opérations.
"""

CLES_ETRANGERES_COUPEES = True

SOURCES = ('folio_desjardins', 'cobalt', 'carte_desjardins', 'marge_desjardins')
DAY = "GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'"

IMPORTS = f"""CREATE TABLE imports_neuf (
    id INTEGER PRIMARY KEY,
    fichier TEXT NOT NULL,
    empreinte TEXT NOT NULL UNIQUE CHECK (length(empreinte) = 64),
    format TEXT NOT NULL CHECK (format IN ('ofx', 'pdf')),
    source TEXT CHECK (source IS NULL OR source IN {SOURCES!r}),
    du TEXT CHECK (du IS NULL OR du {DAY}),
    au TEXT CHECK (au IS NULL OR au {DAY}),
    lignes INTEGER NOT NULL,
    nouvelles INTEGER NOT NULL,
    importe_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (du IS NULL OR au IS NULL OR du <= au),
    CHECK ((format = 'ofx' AND source IS NULL) OR (format = 'pdf' AND source IS NOT NULL AND du IS NOT NULL AND au IS NOT NULL))
)"""

ACCOUNTS = """CREATE TABLE comptes_bancaires_neuf (
    id INTEGER PRIMARY KEY,
    compte_id INTEGER NOT NULL REFERENCES comptes(id),
    empreinte TEXT NOT NULL UNIQUE CHECK (length(empreinte) = 64),
    apercu TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('banque', 'carte', 'marge')),
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

TABLES = (
    # Soldes du point de vue d'Ugo : une dette (carte, marge) est négative.
    f"""CREATE TABLE soldes_releves (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id),
    compte_bancaire_id INTEGER NOT NULL REFERENCES comptes_bancaires(id),
    du TEXT NOT NULL CHECK (du {DAY}),
    au TEXT NOT NULL CHECK (au {DAY}),
    solde_debut_cents INTEGER NOT NULL,
    solde_fin_cents INTEGER NOT NULL,
    CHECK (du <= au),
    UNIQUE (compte_bancaire_id, du, au)
)""",
    f"""CREATE TABLE points_releves (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id),
    compte_bancaire_id INTEGER NOT NULL REFERENCES comptes_bancaires(id),
    du TEXT NOT NULL CHECK (du {DAY}),
    au TEXT NOT NULL CHECK (au {DAY}),
    precedent INTEGER NOT NULL,
    gagnes INTEGER NOT NULL,
    utilises INTEGER NOT NULL,
    nouveau INTEGER NOT NULL,
    concorde INTEGER NOT NULL CHECK (concorde IN (0, 1)),
    CHECK (du <= au),
    UNIQUE (compte_bancaire_id, du, au)
)""",
    """CREATE TABLE comptes_ignores (
    id INTEGER PRIMARY KEY,
    empreinte TEXT NOT NULL UNIQUE CHECK (length(empreinte) = 64),
    apercu TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('banque', 'carte', 'marge')),
    ignore_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
    "CREATE INDEX idx_soldes_releves_compte ON soldes_releves(compte_bancaire_id, du)",
)


def deja_appliquee(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='soldes_releves'").fetchone() is not None


def appliquer(con):
    con.execute(IMPORTS)
    con.execute("INSERT INTO imports_neuf(id, fichier, empreinte, format, du, au, lignes, nouvelles, importe_le) "
                "SELECT i.id, i.fichier, i.empreinte, i.format, (SELECT min(date) FROM operations WHERE import_id=i.id), "
                "(SELECT max(date) FROM operations WHERE import_id=i.id), i.lignes, i.nouvelles, i.importe_le FROM imports i")
    con.execute("DROP TABLE imports")
    con.execute("ALTER TABLE imports_neuf RENAME TO imports")
    con.execute(ACCOUNTS)
    con.execute("INSERT INTO comptes_bancaires_neuf(id, compte_id, empreinte, apercu, type, cree_le) "
                "SELECT id, compte_id, empreinte, apercu, type, cree_le FROM comptes_bancaires")
    con.execute("DROP TABLE comptes_bancaires")
    con.execute("ALTER TABLE comptes_bancaires_neuf RENAME TO comptes_bancaires")
    for statement in TABLES:
        con.execute(statement)
