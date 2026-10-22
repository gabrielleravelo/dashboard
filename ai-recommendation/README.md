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

## Utilisation

**1. Récupérer les CV de la base de dev** (nécessite un accès au projet `tailed-community-dev`)

```bash
# Avec une clé de compte de service (gardée HORS du dépôt)
python fetch_cvs.py --key C:\chemin\hors\depot\tailed-dev-key.json --limit 20
# Ou avec Google Cloud CLI
gcloud auth application-default login
python fetch_cvs.py --limit 20

# Profils Firestore seulement (sans CV, sans nom ni courriel)
python fetch_cvs.py --key ... --profiles-only
```

**2. Transformer les CV en données structurées**

```bash
python cv_to_structured.py ../cv_tests_prives            # un dossier
python cv_to_structured.py mon_cv.pdf --debug             # un CV, avec diagnostic
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

Sur 20 CV de la base de dev :

| Version | CV exploitables |
|---|---|
| Première version du portage | 2 / 20 |
| Après ajustements sur les vrais CV | **19 / 20** |

Principaux ajustements (repérés par `AJOUT` dans le code) :
- section « Summary of Skills and Qualifications » (modèle très fréquent) ;
- section de bénévolat → expériences avec `employmentType: "volunteer"` ;
- sections ignorées (activités parascolaires, prix, associations) ;
- titres au pluriel, titres espacés, mois accentués ;
- compétences écrites en phrases ;
- lignes « Technical skills: … » dans une expérience ou un projet ;
- titres de projets et descriptions bien séparés ;
- formation sur une seule ligne « Programme — École | années ».

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
- Les offres n'ont pas de description : le matching ne peut utiliser que le titre.
- Le moteur utilise pour l'instant la formation et les compétences, pas encore
  les expériences ni les projets.

## Prochaines étapes

- [x] Charger les vraies offres depuis `jobs.json` (avec cache des vecteurs)
- [x] Jeu d'évaluation et comparaison de méthodes (aléatoire, TF-IDF, MiniLM, E5)
- [ ] Annoter manuellement les recommandations pour une mesure rigoureuse
- [ ] Combiner les sources du profil : saisie manuelle, CV, puis champs simples (`program`, `skills`)
- [ ] Utiliser les expériences et les projets dans le matching
- [ ] Enrichir les profils avec les dépôts GitHub publics (`githubUsername`)
- [ ] Proposer les améliorations du parseur dans `resume-parser.ts`

## Sécurité et données personnelles

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
