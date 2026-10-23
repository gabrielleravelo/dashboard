# ai-recommendation — Prototype de recommandation Tail'ed

Système de recommandation qui associe les étudiants aux offres de stage et
d'emploi de Tail'ed, à partir de **données structurées** extraites de leur CV
(et, à terme, enrichies par leurs dépôts GitHub publics).

> Statut : prototype de stage, en cours de développement. Pas encore branché
> sur la plateforme.

## Principe

L'algorithme ne reçoit jamais le CV brut : il travaille sur des données
structurées, au même format que les profils Firestore (`profiles/{uid}`).

```
CV (PDF, Firebase Storage)
   │  cv_to_structured.py
   ▼
Profil structuré (experiences, education, projects, skillsStructured)
   │  profile_adapter.py
   ▼
Moteur de recommandation  ◄──  Offres (tailed-internships-new-grad/data/jobs.json)
   │  recommendation_engine.py / api.py
   ▼
Offres recommandées
```

## Contenu du dossier

| Fichier | Rôle |
|---|---|
| `recommander_depuis_cv.py` | **Pipeline complet** : CV (PDF) → données structurées → offres recommandées |
| `cv_to_structured.py` | CV PDF → données structurées. Portage Python de `functions/src/lib/resume-parser.ts`, même schéma de sortie |
| `test_cv_to_structured.py` | Tests unitaires du parseur : les 12 tests officiels de `resume-parser.test.ts` + cas trouvés sur de vrais CV |
| `fetch_cvs.py` | Récupère les CV (Storage) et les profils (Firestore) de la base de dev |
| `evaluate_parser.py` | Compare les données extraites aux données saisies par les étudiants |
| `profile_adapter.py` | Transforme un profil structuré en entrée pour le moteur |
| `jobs_source.py` | Charge les offres : les ~28 000 vraies offres (`jobs.json`, cache 24 h) ou les 100 offres de test ; filtres par type et Canada |
| `recommendation_engine.py` | Moteur : similarité sémantique (SentenceTransformer) entre le profil et le titre des offres, avec cache des vecteurs |
| `test_recommendation_engine.py` | Tests du moteur et du chargement des offres (faux modèle, rapides et hors ligne) |
| `evaluate_recommendations.py` | Évaluation : précision@10 et MRR de plusieurs méthodes sur des profils fictifs |
| `eval/profils_evaluation.json` | Jeu d'évaluation : 12 profils fictifs de domaines variés + règles de pertinence |
| `test_evaluate_recommendations.py` | Tests du script d'évaluation |
| `api.py` | API FastAPI qui expose les recommandations |
| `test_profil_genie_electrique.py` | Démo avec un profil fictif hors informatique |
| `data/sample_jobs_100.json` | 100 offres extraites de `jobs.json`, pour les tests hors ligne |

## Installation

```bash
cd ai-recommendation
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows (PowerShell)
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
python -m pytest -v
```

## Démarrage rapide : CV → recommandations

```bash
python recommander_depuis_cv.py mon_cv.pdf --live --canada
python recommander_depuis_cv.py mon_cv.pdf --live --type internship --top 20
python recommander_depuis_cv.py profil.json --live      # profil déjà structuré
```

Si le CV n'est pas assez standard pour être structuré (statut différent de
`ok`), aucune recommandation n'est faite, conformément à la règle du projet.

## Utilisation détaillée

**1. Récupérer et structurer les CV de la base de dev** (nécessite un accès au projet `tailed-community-dev`)

Les CV sont lus **en mémoire** et passés directement au parseur : aucun PDF
n'est enregistré. Seul le JSON structuré (sans nom ni coordonnées) est gardé.

```bash
# Avec une clé de compte de service (gardée HORS du dépôt)
python fetch_cvs.py --key C:\chemin\hors\depot\tailed-dev-key.json --limit 20 --parse-direct --struct-dir ../../tail-ed-ai-data/profils_structures

# Ne rien enregistrer du tout : statistiques du parseur seulement
python fetch_cvs.py --key ... --limit 20 --parse-direct --no-save

# Profils Firestore seulement (sans CV, sans nom ni courriel)
python fetch_cvs.py --key ... --profiles-only
```

Sans `--key`, le script utilise Google Cloud CLI
(`gcloud auth application-default login`).

**2. Structurer un CV local** (par exemple son propre CV, ou pour diagnostiquer un cas)

```bash
python cv_to_structured.py mon_cv.pdf --debug
```

Chaque CV reçoit un statut :
- `ok` : formation ou expérience + compétences → `recommendable: true`
- `partiel` : informations incomplètes
- `echec` : CV non standard ou scanné → pas de recommandations

**3. Tester les recommandations**

```bash
python jobs_source.py                                  # télécharge et résume les vraies offres
python test_profil_genie_electrique.py                 # 100 offres de test
python test_profil_genie_electrique.py --live --canada # vraies offres au Canada
uvicorn api:app --reload                               # API sur les offres de test
$env:JOBS_SOURCE = "live"; uvicorn api:app             # API sur les vraies offres (PowerShell)
```

Le premier lancement sur les vraies offres calcule environ 28 000 vecteurs
(quelques minutes). Ils sont ensuite gardés dans `data/cache/` et seules les
nouvelles offres sont recalculées.

## Évaluer les recommandations

Le jeu d'évaluation contient 12 profils **fictifs** de domaines variés
(logiciel, données/IA, génie électrique, mécanique et civil, finance,
marketing, santé, design UX, cybersécurité, logistique, ressources humaines).
Aucun modèle n'est entraîné (modèle préentraîné, sans données étiquetées) :
ce jeu sert uniquement à **mesurer** et **comparer** les méthodes.

```bash
python evaluate_recommendations.py                          # étiquettes automatiques, offres au Canada
python evaluate_recommendations.py --methods tfidf,minilm,e5
```

Métriques : **Précision@10** (proportion d'offres pertinentes parmi les 10
premières) et **MRR** (1 / rang de la première offre pertinente).

Les étiquettes automatiques (mots-clés dans le titre) sont approximatives et
avantagent un peu TF-IDF. Pour une mesure rigoureuse, annoter à la main :

```bash
python evaluate_recommendations.py --methods tfidf,minilm --export-csv eval/a_annoter.csv
# remplir la colonne « pertinent » (1/0) dans Excel, à l'aveugle (l'ordre est mélangé)
python evaluate_recommendations.py --methods random,tfidf,minilm --labels eval/a_annoter.csv
```

## Résultats actuels

### Extraction des CV

Sur 20 CV de la base de développement :

| Version | CV exploitables |
|---|---|
| Première version du portage | 2 / 20 |
| Après ajustements sur les vrais CV | **19 / 20** |

Principaux ajustements (repérés par `AJOUT` dans le code) : section « Summary of
Skills and Qualifications » (modèle très fréquent), bénévolat, sections
ignorées (activités parascolaires, prix), titres au pluriel ou espacés, mois
accentués, compétences écrites en phrases, séparation titre/description des
projets, formation sur une seule ligne.

Attention : le parseur a été ajusté sur ces mêmes 20 CV, ce score est donc
probablement optimiste. La base en contient 173 : en garder une partie de côté
(jamais utilisée pour ajuster) donnerait une mesure plus fiable.

### Recommandations

Évaluation sur 1 186 offres au Canada, 12 profils fictifs, étiquettes
automatiques (mots-clés), k = 10 :

| Méthode | Précision@10 | MRR |
|---|---|---|
| Aléatoire | 0,04 | 0,21 |
| TF-IDF (mots-clés) | 0,31 | 0,52 |
| **MiniLM multilingue (moteur actuel)** | **0,62** | **1,00** |

- Le moteur est deux fois plus précis que TF-IDF ; l'écart est le plus grand
  quand les mots diffèrent (profil en français, offres en anglais) : logiciel
  1,00 contre 0,30, données/IA 0,90 contre 0,20, génie électrique 0,90 contre 0,30.
- MRR = 1,00 : pour les 12 profils, la première offre recommandée est pertinente.
- Points faibles : design UX (0,10), logistique (0,10), santé (0,30), RH (0,40).
  Causes observées : titres ambigus (« CAD Design » pour un profil UX), niveau
  du poste non pris en compte (« Physician » pour une étudiante en soins
  infirmiers), peu d'offres disponibles au Canada dans ces domaines, et règles
  de mots-clés parfois trop strictes.

Ces chiffres donnent une tendance ; une annotation manuelle donnera une mesure
plus rigoureuse (voir « Évaluer les recommandations »).

## Source des offres

Les offres viennent de
[tailed-internships-new-grad](https://github.com/tailed-community/tailed-internships-new-grad),
fichier `data/jobs.json`, mis à jour chaque jour. C'est la même source que
`functions/src/lib/jobs-feed.ts` et `src/lib/external-jobs.ts`.

Champs disponibles : `id`, `company`, `title`, `location`, `type`
(`internship` / `new_grad`), `season`, `source`, `url`, `date_posted`,
`date_added`, `active`. Il n'y a **pas de description**, d'où un matching basé
sur le titre.

**Attention à la localisation :** le script du dépôt des offres
(`scripts/core/normalize.py`) convertit l'abréviation « CA » en « Canada ».
Des offres en Californie apparaissent donc comme « San Diego, Canada ».
`jobs_source.is_canada()` exige une province, une ville canadienne connue ou un
format sans ambiguïté : il garde ~1 150 offres au lieu de ~1 650 avec un filtre
naïf sur le mot « Canada ».

## Limites connues

- CV scannés (images) : pas de texte, donc statut `echec` (il faudrait de l'OCR).
- CV en deux colonnes : l'ordre du texte peut se mélanger.
- CV hors informatique : la structure est bien extraite, mais les compétences
  écrites en phrases et certains diplômes québécois (DEP, AEC) sont moins
  bien reconnus.
- Les offres n'ont pas de description dans `jobs.json` : le matching ne peut
  utiliser que le titre.
- Le moteur utilise pour l'instant la formation et les compétences, pas encore
  les expériences ni les projets.

## Sécurité et données personnelles

Avec `--parse-direct`, les CV sont lus en mémoire et ne sont jamais écrits sur
le disque. Si des PDF ou des profils sont enregistrés, ils doivent rester hors
du dépôt et être supprimés à la fin des tests.

Les CV, les profils et la clé de compte de service ne doivent **jamais** être
commités. Le `.gitignore` du dépôt bloque :

```
cv_tests_prives/
profils_firestore/
profils_structures/
*-key.json
student_profile.json
data/cache/
```

Ne garde jamais une clé de compte de service dans le dossier du dépôt.
