# Moneta

Moneta tient les finances d'Ugo et de sa femme : comptes, dépenses, revenus, budget, points, relevés,
factures et rapports. C'est l'ancien domaine Finances d'Agent Smith, sorti dans son propre dépôt à l'étape 3
de Claude OS (D107, D109, spec 013 de Smith). Il offre exactement les outils de Finances, sous les mêmes
noms, comme connecteur de Claude Desktop et comme processus de l'app Smith.

Règles : [`.specify/memory/constitution.md`](.specify/memory/constitution.md). Décisions et spec : dans le
dépôt de Smith (`docs/04_DECISION_LOG.md`, `specs/013-sortie-moneta/`).

## Installation

Sur le Mac d'Ugo seulement, à côté de Smith (`~/_PersonalAgents/smith`), qui fournit la lecture des
paiements du Dojo.

1. Python 3.14, dans l'environnement du dépôt, sans aucune dépendance installée :
   `/opt/homebrew/bin/python3.14 -m venv .venv`.
2. Le socle commun est déjà dans `socle/`, à la version de `socle.epingle.json`. Pour en changer :
   `bin/moneta-socle-epingler <tag>` (ou `--branche X.Y`), puis les tests; jamais de retouche à la main.
3. Les tests : `bin/moneta-tests` (tout au vert). La lecture des paiements y est celle du dépôt de Smith
   désigné par `MONETA_TESTS_SMITH`, sur des données fictives.
4. Mise en service, par Ugo seulement et dans l'ordre de `quickstart.md` §7 de la spec 013 : déménagement des
   données, base, lecteur, tâche de nuit, connecteur.

## Scripts

| Script | Ce qu'il fait |
|---|---|
| `bin/moneta-mcp` | Serveur MCP de Moneta pour Claude Desktop et l'app : lectures, et le lot Finances avec `--ecriture`. `--fictif` : base synthétique temporaire. |
| `bin/moneta-migrer` | État des migrations de `finances.db`; `--appliquer` (Ugo) les applique après une sauvegarde vérifiée. |
| `bin/moneta-verifier` | État propre : données en 700 et 600, base lisible, migrations à jour, dossier des factures dans Drive, lecteur de relevés. `--json` pour `smith-verifier`. |
| `bin/moneta-construire-releve` | Construit et signe le lecteur de relevés PDF (PDFKit) dans `donnees/moneta/apps/moneta-releve`; jamais copié de l'ancien lecteur de Smith. |
| `bin/moneta-sauvegarde-quotidienne` | La nuit de Moneta (tâche `ca.dojod.moneta.sauvegarde`, 21 h 15, 15 minutes après Smith) : copie locale vérifiée, image chiffrée vers Drive et PRO-HD1, rétention. |
| `bin/moneta-demenager` | Déménagement des données Finances de `donnees/smith/` à `donnees/moneta/`, et son retour : `--inventaire`, `--repeter` (fictif), `--appliquer` et `--retour` (Ugo, dans le terminal). |
| `bin/moneta-socle-epingler` | Change la version épinglée du socle. Ne committe pas. |
| `bin/moneta-tests` | Toute la suite, chaque fichier dans son propre monde fictif. |

Le modèle de la tâche de nuit est `outils/ca.dojod.moneta.sauvegarde.plist`, jamais installé par le code :
Ugo règle ses deux destinations (« À RÉGLER ») et l'installe à la mise en service. La phrase de passe de
l'image est lue au trousseau sous `ca.dojod.moneta.sauvegarde`, jamais celle de Smith.

## Données

Hors Git et hors du code, dans `~/_PersonalAgents/donnees/moneta/`, dossiers en 700 et fichiers en 600 :

| Dossier | Contenu |
|---|---|
| `database/` | `finances.db`, la seule base de Moneta; personne d'autre ne l'ouvre. |
| `backups/` | Copies vérifiées avant chaque écriture ou migration (`database/moneta/`), copies de la nuit (`database/daily/`), copie de lecture du serveur (`database/mcp-lecture/`). |
| `imports/` | Relevés à importer. |
| `exports/` | Rapports et export CSV; un rapport ne va sur Drive que par `WR_SAVE_DRIVE_FICHIER` de Smith. |
| `private/` | Journal de la nuit, registre des pièces sauvegardées, note du déménagement, dossier d'import des factures de l'app. |
| `apps/` | `moneta-releve`, le lecteur compilé (le seul fichier en 700). |
| `config/` | Réservé aux réglages de Moneta (vide à ce jour). |

Le journal des écritures est commun à Smith et Moneta : `~/_PersonalAgents/donnees/journal-ecritures/` (D110).
Les tests ne touchent jamais ces dossiers : `MONETA_HOME` désigne un dossier fictif, et un dossier sous
`~/_PersonalAgents` n'est jamais suivi.

## Sorties du Mac

Seulement les trois de D109, gardées par `tests/test_sorties_moneta.py` :

1. le dépôt d'une facture dans « DojoD/05 Administration/Factures/à classer » de Google Drive;
2. son classement dans « Factures/AAAA » à l'enregistrement de la dépense (`WR_SAVE_FINANCES_BD`);
3. l'image chiffrée de la nuit, vers Drive et PRO-HD1.

Tout le reste passe par Smith et la fenêtre du Mac. Les paiements du Dojo se lisent seulement par
`~/_PersonalAgents/smith/bin/smith-lecture-paiements`, jamais dans `dojo.db`.
