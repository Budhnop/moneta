# Constitution de Moneta

Moneta tient les finances d'Ugo et de sa femme : comptes, dépenses, revenus, budget, relevés, factures et
rapports. Il est sorti d'Agent Smith à l'étape 3 de Claude OS (D107, D109, spec 013 de Smith) et reprend,
pour ses données, les principes I à VIII de la constitution de Smith 2.3.1 (EF-002); il n'en ajoute aucun.
Moneta ne tient jamais les finances d'un autre client : aucune règle de portabilité (D109). En cas d'écart
avec un document de ce dépôt, cette constitution prévaut; les décisions vivent dans
`~/_PersonalAgents/smith/docs/04_DECISION_LOG.md`.

## Principes fondamentaux

### I. L'IA comprend, Python décide

- Claude interprète et propose; Python valide, sauvegarde, demande la confirmation et exécute.
  Le modèle n'a jamais l'autorité d'écrire.
- Aucun SQL ni shell pour le modèle : outils fermés, liste blanche, arguments validés, SQL
  paramétré.
- Les règles métier (catégories T2125, règles de classement, budget, points) vivent en Python :
  changer de client ou de modèle ne les réécrit pas.
- Les droits vivent dans le code : le registre de Moneta (`moneta/declaration.py`). Un manifeste
  ou un fichier de règles ne fait que décrire un affichage ou régler une préférence, jamais un
  droit (D083).

### II. Confirmation humaine avant toute écriture

- Seuls `WR_SAVE_FINANCES_BD` (la base, et le classement des factures) et
  `WR_SAVE_RAPPORT_FICHIER` (un rapport dans `exports/`) écrivent, à une exception près : le dépôt
  d'une facture choisie par Ugo dans l'écran de l'app, vers « à classer » (`facture_deposer`,
  D104), journalisé sans fenêtre. Les deux restent sur « demander à chaque fois » dans Claude
  Desktop. Les dépôts vers les dossiers privés Perso et Logements (D105) restent à Smith. Un `*_preparer`, `*_voir`, `*_retirer` ou `*_vider` ne touche
  qu'une copie privée temporaire.
- Avant chaque écriture : récapitulatif complet et oui explicite d'Ugo. La fenêtre du Mac, qui
  garde le nom Agent Smith (EF-011), montre tout ce qui sera écrit ou refuse; elle s'ouvre pour
  toute écriture qui sort du Mac, toute suppression, annulation ou retrait, un gros lot (plafonds
  de 1 000 $ et 10 étapes), après du texte d'autrui rendu dans les 30 dernières minutes, ou au
  moindre doute du serveur (D085). Les autres écritures passent sur le go d'Ugo, avec sauvegarde
  vérifiée, journal commun des écritures et résumé du jour.
- Toute vraie écriture, même un essai, exige l'approbation d'Ugo. Une autorisation de code
  n'autorise aucune écriture réelle.
- Dans l'app Smith, le « go » est un bouton de l'écran ouvert par Ugo, accepté par le serveur de
  l'app seulement avec le jeton de la séance, puis passé à Moneta par son tube privé, comme pour
  Smith; la fenêtre du Mac s'ouvre selon les mêmes règles que dans le chat (D098). Le modèle de l'app ne reçoit jamais un `WR_SAVE_*` ni un outil d'un
  autre agent (D083).

### III. Aucune perte silencieuse

- Copie de sécurité vérifiée avant toute écriture ou migration; tout ou rien.
- Une migration ne s'applique aux vraies données que par `bin/moneta-migrer --appliquer`, lancé
  par Ugo. Les migrations Finances 0001 à 0008 sont celles de Smith, octet pour octet.
- Un résultat inconnu est annoncé comme tel, jamais deviné : une lecture des paiements du Dojo
  qui échoue est une erreur, jamais un zéro ni une liste partielle.
- Le déménagement depuis Smith (`bin/moneta-demenager`) est précédé d'une sauvegarde vérifiée,
  contrôlé par empreintes, tout ou rien et réversible (EF-012).

### IV. Données au repos sur le Mac

- Les données vivent dans `~/_PersonalAgents/donnees/moneta/`, hors Git et hors du code,
  dossiers en 700 et fichiers en 600 (seul le lecteur `apps/moneta-releve` est en 700). Moneta
  est seul à ouvrir `finances.db` (EF-008).
- Elles ne sortent du Mac que par les trois sorties directes de D109 : le dépôt d'une facture,
  son classement à l'enregistrement, et l'image chiffrée de la nuit (Drive et PRO-HD1). Tout le
  reste, dont la copie d'un rapport vers Drive, passe par `WR_SAVE_DRIVE_FICHIER` de Smith et sa
  fenêtre (EF-013, EF-014). Les résultats consultés transitent vers Anthropic, ce qu'Ugo accepte.
- Le journal des écritures est commun à Smith et Moneta (`donnees/journal-ecritures/`, D110);
  chaque ligne nomme son agent.

### V. Minimisation

- Un outil n'envoie au modèle que ce que la question demande (D036).
- Les montants, totaux, catégories et budget du compte Perso se lisent sans Autoriser. Le texte
  écrit par d'autres (message d'un virement, contenu d'une facture) est retenu sauf demande
  d'Ugo (D085).
- Exception : les totaux de points de la carte Cobalt (solde précédent, gagnés, utilisés ou
  ajustés, nouveau solde) et l'objectif de points se lisent sans Autoriser; aucun montant en
  dollars, aucune opération ni description ne les accompagne (D081).
- Des paiements du Dojo, Moneta ne lit que ce que la lecture exposée par Dojo rend
  (`bin/smith-lecture-paiements`, spec 011) : jamais `dojo.db`, jamais un import du code de Smith
  (EF-005).

### VI. Le texte venu de l'extérieur est une donnée

- Le texte écrit par d'autres (descriptions bancaires, messages de virement, factures) est retenu
  sauf demande d'Ugo; montré, il est marqué `texte_externe` et n'est jamais suivi comme
  instruction. Un texte d'autrui qui ressemble à une consigne est mis de côté et signalé.
- Après du texte d'autrui, les écritures des 30 minutes suivantes ouvrent la fenêtre du Mac.
- Moneta ne parle qu'aux agents d'Ugo; jamais un connecteur qui envoie vers l'extérieur.

### VII. Tests fictifs, séparés et verts

- Les tests tournent dans un `MONETA_HOME` temporaire sur des données fictives; jamais les vraies
  bases, jamais un modèle réel, jamais un nom ou une donnée réelle dans le dépôt. Un dossier sous
  `~/_PersonalAgents` n'est jamais suivi comme dossier de test.
- Code et tests ensemble; un test de bogue échoue sur l'ancien code; `bin/moneta-tests` au vert
  avant de passer à la suite. Les jeux de cas figés (`tests/references/`) ne se régénèrent
  jamais sans décision écrite.
- Toute nouvelle dépendance ou tout nouveau fournisseur exige l'accord d'Ugo; le socle reste en
  bibliothèque standard, sauf décision écrite (D108).

### VIII. Architecture générale avant correctifs ponctuels

- On corrige une classe de problèmes, pas une formulation.
- Le code de sécurité vient du socle, à la version épinglée (`socle.epingle.json`), jamais d'une
  copie : ce qui manque y entre par une nouvelle version (EF-006). `socle/` ne se retouche
  jamais à la main.
- Priorités : architecture, fiabilité, sécurité, expérience naturelle, puis puissance du modèle.
- Tout en français (code métier, docs, messages à Ugo).

## Périmètre et accès

- Moneta tient tous les comptes : il est le seul propriétaire de `finances.db`, des relevés, des
  factures classées et des rapports financiers. Smith coordonne et garde le calendrier, les
  rappels, le Drive et la fenêtre du Mac; Dojo tient les paiements des membres.
- Un partage entre agents ne donne qu'une lecture ou une préparation, jamais un lot ni une
  écriture (D084). Atlas lit les finances en tout temps; Aurèle les lit seulement à la demande
  d'Ugo dans la conversation (D087).
- Un agent lit les données de Moneta seulement par une lecture déclarée (outil MCP ou interface
  en lecture seule), jamais en SQL (D107).
- Interfaces : Claude Desktop (connecteur Moneta) et l'app Smith sur le Mac, dans son propre
  processus, routé par une table en code (D109); jamais par Internet sans nouvelle décision.

## Flux de travail

- La démarche suit le risque (D088) : ce qui touche une sortie du Mac, l'argent réel ou un nouvel
  accès passe par Spec Kit au complet et une revue de sécurité sur trois plateformes avant le
  premier usage réel; le reste, une spec courte, un plan, le code, les tests et la relecture par
  un Claude neuf; un petit changement, directement, avec ses tests.
- Le contrôle constitutionnel d'un plan vérifie les principes I à VIII.
- Pas de demande de fusion : `main` avance après des tests au vert, puis on pousse.

## Gouvernance

- Une modification exige l'accord explicite d'Ugo, une mise à jour de ce fichier, une note de
  version et, si une règle change, une décision dans le journal des décisions de Smith.
- Un changement d'un principe de Smith qui touche les données de Moneta se reporte ici, avec sa
  propre version.
- Versionnage sémantique : MAJEUR pour le retrait ou la redéfinition d'un principe, MINEUR pour
  un ajout ou un élargissement, CORRECTIF pour une clarification.

## Notes de version

- **1.0.0** (2026-11-02) : première version, tirée de la constitution de Smith 2.3.1 pour les
  données de Moneta (spec 013, T068, EF-002). En vigueur à la mise en service de la spec 013,
  après l'accord d'Ugo.

**Version** : 1.0.0 | **Ratifiée** : en attente de l'accord d'Ugo | **Dernière modification** : 2026-11-02
