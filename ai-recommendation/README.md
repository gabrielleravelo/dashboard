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
| `recommendation_engine.py` | Moteur : similarité sémantique (SentenceTransformer) entre le profil et le titre des offres |
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
python -m pytest test_cv_to_structured.py -v
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
python test_profil_genie_electrique.py
uvicorn api:app --reload
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

- [ ] Charger les vraies offres depuis `jobs.json` (avec cache des vecteurs)
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
```

Ne garde jamais une clé de compte de service dans le dossier du dépôt.
