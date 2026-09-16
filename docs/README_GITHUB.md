# <p align="center"><img src="images/Nexora.png" alt="Nexora Logo" width="180"/><br>N3XORA v2.0</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/Celery-37814A?style=for-the-badge&logo=celery&logoColor=white" alt="Celery"/>
  <img src="https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white" alt="Redis"/>
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL"/>
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker"/>
  <img src="https://img.shields.io/badge/Three.js-000000?style=for-the-badge&logo=three.js&logoColor=white" alt="Three.js"/>
</p>

---

## 🔬 Description

**Nexora v2.0** est une plateforme web moderne et conteneurisée pour l'analyse bioinformatique de séquences ADN/ARN, la simulation de docking moléculaire et l'analyse heuristique de stabilité génomique (HSA, ex-QSA — aucun calcul quantique). 

Initialement conçue avec une architecture PHP monolithique, la version **v2.0** a été entièrement refondue pour offrir une infrastructure en couches conteneurisée et asynchrone basée sur **FastAPI** (Python 3.12), **Celery** pour la file de tâches asynchrones, **Redis** comme broker, et **PostgreSQL 16** pour la persistance des données. L'interface utilisateur moderne en **HTML5/Vanilla CSS/JavaScript (ES6)** est servie de façon performante par un proxy inverse **Nginx**.

---

### Hardening scientifique des heuristiques

N3XORA distingue les **signaux heuristiques** des conclusions biologiques : les signatures génomiques sont des descripteurs contextuels, la résistance antimicrobienne rapporte des déterminants génotypiques avec leur méthode et leur niveau de preuve sans fabriquer de pourcentage, et HSA reste un indice exploratoire sans probabilité de mutation. Pour les bactéries, AMRFinderPlus est le moteur privilégié lorsqu’il est installé; un antibiogramme reste nécessaire pour une conclusion phénotypique.

## 🚀 Fonctionnalités Clés

### 🧬 1. Biologie Moléculaire & Analyse de Séquences
* **Intégration NCBI (Entrez API) & Cache local** : Recherche et téléchargement automatique de séquences de nucléotides via leur identifiant d'accession NCBI. Les séquences sont mises en cache dans PostgreSQL pour optimiser les performances.
* **Analyse de Séquences** : Calcul de la fréquence des nucléotides (A, T, C, G pour l'ADN ; A, U, C, G pour l'ARN), composition, masse moléculaire et indicateurs biochimiques.
* **Transcription et Traduction** : Transcription d'ADN en ARNm et traduction d'ARNm en séquences peptidiques (protéines) avec identification des codons.
* **Visualisation Analytique** : Graphiques dynamiques de distribution des bases générés via **Chart.js**.

### 🔑 2. Détection de Mutations & Profils de Résistance
* **Détection de Variants (Mutations)** : Identification automatique des mutations et des variants génétiques par rapport à des signatures de référence.
* **Profils de Résistance aux Antibiotiques** : Analyse et détection automatique des gènes et mutations conférant des résistances bactériennes avec génération de scores de résistance.

### 🧪 3. Docking Moléculaire (Moteur AutoDock Vina)
* **Docking Asynchrone** : Soumission de simulations de docking ligand-récepteur gérées en arrière-plan par des workers **Celery** pour éviter de bloquer l'interface.
* **Base de Données de Ligands** : environ 30 exemples pré-enregistrés (Paracétamol, Ibuprofène, Aspirine, Caféine, Morphine, Imatinib, Ritonavir, Remdesivir, Quercétine, Curcumine, etc.); leur présence ne constitue pas une validation expérimentale.
* **Calculs Physico-chimiques** : Intégration de **RDKit** pour évaluer les propriétés moléculaires en temps réel (LogP, masse moléculaire, donneurs/accepteurs de liaisons hydrogène, surface polaire TPSA, score Lipinski).
* **Visualisation 3D** : Rendu interactif des poses ligand-protéine via **Three.js** et **OrbitControls**.
* **Récepteur** : Le mode léger utilise un PDB fourni par l'administrateur depuis RCSB PDB ou ESMFold ou AlphaFold DB. ColabFold/AlphaFold2 est optionnel et n'est pas requis par l'image de base.
* **Interprétation** : Vina produit des scores empiriques; ASP est un post-traitement clusterisé et non une énergie libre expérimentale.

### 4. HSA (Heuristic Stability Analysis)
* **Simulation asynchrone HSA** : proxy pédagogique de stabilité face à des facteurs environnementaux (température, pH, stress). **Pas de mécanique quantique.**
* **Scores HSA-2.0** : GC%, GC-skew, entropie de Shannon, Tm nearest-neighbor local et hétérogénéité — indice exploratoire non validé comme prédicteur de mutation.

---

## 🏛️ Architecture Technique

Nexora v2 utilise une architecture en couches conteneurisée et asynchrone (monolithe modulaire Docker Compose — environnement de développement) :

```
                        [ Navigateur Client ]
                                  │
                                  ▼ (Port 80)
                       [ Proxy Inverse Nginx ]
                         ├── / -> Frontend Statique (HTML5 / Vanilla CSS & JS)
                         └── /api/ -> [ FastAPI Backend ] (Port 8000)
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
             [ Base PostgreSQL 16 ]                 [ Redis Task Broker ]
              (Port 5434 -> 5432)                    (Port 6381 -> 6379)
                        │                                     │
                        │                                     ▼
                [ Schéma & Données ]                [ Celery Task Workers ]
                (Ligands validés...)               (Docking, Analysis lourde, HSA)
                                                              │
                                                              ▼
                                                    [ Celery Flower Monitor ]
                                                           (Port 5555)
```

---

## 🛠️ Stack Technologique

* **Frontend** :
  * HTML5 / CSS3 pur (Thème sombre moderne, design premium et responsive).
  * JavaScript ES6 (Architecture modulaire sans framework lourd).
  * **Three.js** : Moteur de rendu 3D moléculaire interactif.
  * **Chart.js** : Visualisation des données analytiques et statistiques.
* **Backend** :
  * **FastAPI** (Python 3.12) : API REST moderne, asynchrone, documentée automatiquement via Swagger.
  * **Celery** : Tâches lourdes asynchrones — docking Vina, HSA, et analyse de séquences au-delà du seuil navigateur.
  * **Redis 7** : Broker de messages pour Celery (pas de cache applicatif de résultats docking branché).
  * **PostgreSQL 16** : Base de données relationnelle robuste (recherche textuelle avancée avec index GIN et extension `pg_trgm`).
* **Moteurs de Calcul Bioinformatique** :
  * **AutoDock Vina** : moteur de docking. **ASP** = Agrégation Statistique des Poses (post-traitement Boltzmann), pas une méthode de docking autonome.
  * **Biopython 1.83** : Manipulation de structures 3D et accès aux APIs NCBI Entrez.
  * **RDKit 2024** : Outil de calcul de descripteurs et propriétés chimiques des ligands.
  * **ColabFold 1.6.2** : Profil optionnel dans `backend/requirements-structure.txt`, destiné aux machines disposant de ressources suffisantes.

---

## ⚙️ Configuration & Lancement (Docker)

### 1. Prérequis
Assurez-vous d'avoir installé sur votre machine :
* [Docker](https://docs.docker.com/get-docker/)
* [Docker Compose](https://docs.docker.com/compose/install/)

### 2. Configuration de l'environnement
Copiez le fichier d'exemple et configurez vos variables (notamment l'email NCBI et la clé secrète JWT) :
```bash
cp .env.example .env
```
Éditez le fichier `.env` si nécessaire :
* `NCBI_EMAIL` : Votre adresse email pour les requêtes à l'API Entrez.
* `DB_PASSWORD` : Mot de passe de la base de données PostgreSQL.
* `SECRET_KEY` : Clé secrète de chiffrement des tokens de session.
* `STRUCTURE_PROVIDER=auto_rcsb` pour rechercher automatiquement un PDB expérimental à partir de la séquence; `RECEPTOR_PDB_PATH` reste disponible en mode administrateur explicite.

### 3. Lancement de l'application
Démarrez l'ensemble de la stack technique avec Docker Compose :
```bash
docker compose up --build -d
```

Le mode Docker standard n'installe pas ColabFold. Son profil optionnel se construit avec `docker build --build-arg INSTALL_COLABFOLD=true -f backend/Dockerfile .`; cette pile nécessite davantage de mémoire, des dépendances JAX/OpenMM et des bases MSA.

### 4. Accès aux services
Une fois les conteneurs démarrés, l'application est accessible aux adresses suivantes :
* 🖥️ **Interface Web (Frontend)** : [http://localhost](http://localhost)
* 📖 **Documentation API (FastAPI Swagger)** : [http://localhost/docs](http://localhost/docs)
* 🌸 **Celery Flower** (auth Basic via `FLOWER_BASIC_AUTH`) : [http://localhost:5555](http://localhost:5555)
* 🗄️ **Base de données PostgreSQL** : Accessible sur le port `5434` (externe) / `5432` (interne).
* 🔴 **Redis** : Accessible sur le port `6381` (externe) / `6379` (interne).

---

## 📸 Aperçus de l'Application

* **Acquisition de séquences NCBI** :
  ![NCBI Search](images/Capture%20d%E2%80%99%C3%A9cran%20du%202026-04-15%2007-44-00.png)
  
* **Transcription & Traduction** :
  ![Transcription Translation](images/Capture%20d%E2%80%99%C3%A9cran%20du%202026-03-04%2014-32-05.png)

* **Visualisation 3D de Docking Moléculaire** :
  ![3D Docking](images/Capture%20d%E2%80%99%C3%A9cran%20du%202026-03-15%2014-58-05.png)

* **Analyse Heuristique de Stabilité (HSA)** :
  ![QSA Analysis](images/Capture%20d%E2%80%99%C3%A9cran%20du%202026-04-15%2007-47-12.png)

---

## 🔒 Sécurité et Performance
* **Rate Limiting & Protection Brute-force** : `login_attempts` avec IP réelle ; blocage temporaire après 5 échecs / 15 min (IP ou identifiant).
* **Chiffrement** : Hashage des mots de passe avec **bcrypt** et authentification par token **JWT** sécurisé.
* **Séparation des files de tâches Celery** : Trois files d'attente distinctes (`docking`, `analysis`, `qsa` [=HSA]) pour garantir que les simulations lourdes n'affectent pas la réactivité de l'analyse classique de séquences.

---

## 🤝 Contribution

1. Forkez le projet
2. Créez une branche thématique (`git checkout -b feature/nouvelle-fonctionnalite`)
3. Commitez vos modifications (`git commit -am 'Ajoute une fonctionnalité'`)
4. Poussez sur votre branche (`git push origin feature/nouvelle-fonctionnalite`)
5. Ouvrez une Pull Request

---

## 📝 Licence

Ce projet est sous licence **GPL (General Public License)**. Consultez le fichier [LICENSE](LICENSE) pour plus de détails.

---

### ⚠️ Avertissement Scientifique
*Nexora est un outil de simulation à but éducatif et de recherche académique. Toute prédiction ou affinité calculée doit être validée de manière rigoureuse par des techniques expérimentales de laboratoire (in vitro / in vivo) avant toute utilisation médicale ou clinique.*


## Research Workflow — Sahel (Phase 1)

N3XORA v2 ajoute une couche d'orchestration scientifique dédiée aux études de variants de pathogènes d'intérêt régional. Le premier cas d'usage est paramétré autour de *Plasmodium falciparum* et du Mali/Sahel, sans limiter le schéma à un seul pathogène.

Le workflow est : `source/isolate → variant → protein target → structure provenance → pocket → AutoDock Vina → ASP complémentaire → interprétation 3D`. Les nouveaux objets `research_studies`, `research_variants`, `research_targets` et `research_docking_runs` assurent la traçabilité. Les routes sont sous `/api/v1/research/*`.

Cette couche ne transforme pas automatiquement un variant en mutation causale, un déterminant moléculaire en phénotype, ni un score de docking en efficacité thérapeutique. Les structures et résultats conservent leur provenance et leur statut de preuve.

## Research workflow — Phase 2

N3XORA ajoute un pont déterministe **variant → protéine** : une substitution protéique explicitement vérifiée peut être appliquée à une cible WT pour produire une séquence variante traçable. Cette construction n'est ni une preuve de causalité ni une génération automatique de structure 3D.

### Research Phase 4 — comparaison structurale WT/variant
Le workflow de recherche peut comparer deux structures déjà validées (WT et variant) par correspondance de séquence, superposition Kabsch sur Cα, RMSD/déplacements, région locale autour du variant et comparaison descriptive de poches. Cette comparaison ne déduit ni résistance, ni affinité, ni efficacité thérapeutique.
