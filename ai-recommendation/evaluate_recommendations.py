"""
evaluate_recommendations.py — Mesurer la qualité des recommandations.

Pour chaque profil FICTIF du jeu d'évaluation (eval/profils_evaluation.json),
chaque méthode classe les offres, et on mesure :
  - Précision@k : parmi les k premières offres, la proportion de pertinentes.
  - MRR (Mean Reciprocal Rank) : 1 / rang de la première offre pertinente
    (1 = la première offre est pertinente, 0,5 = la deuxième, etc.).

Méthodes comparées :
  random  : ordre aléatoire (point de référence minimal)
  tfidf   : mots-clés communs (TF-IDF), sans compréhension du sens
  minilm  : le moteur actuel (paraphrase-multilingual-MiniLM-L12-v2)
  e5      : intfloat/multilingual-e5-small (modèle plus récent, ~470 Mo à télécharger)

Deux façons de décider si une offre est pertinente :
  1. Automatique (par défaut) : règles de mots-clés sur le titre de l'offre
     ('pertinent_si' / 'sauf_si'). Rapide, mais approximatif, et avantage
     un peu la méthode TF-IDF qui repose elle aussi sur les mots.
  2. Manuelle (plus rigoureuse) :
       python evaluate_recommendations.py --export-csv eval/a_annoter.csv
       -> ouvrir le CSV dans Excel, remplir la colonne « pertinent » (1 ou 0)
       python evaluate_recommendations.py --labels eval/a_annoter.csv

Utilisation :
    python evaluate_recommendations.py                      # offres au Canada
    python evaluate_recommendations.py --all                # toutes les offres
    python evaluate_recommendations.py --methods tfidf,minilm,e5
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from jobs_source import load_jobs
from profile_adapter import adapt_profile

BASE_DIR = Path(__file__).resolve().parent
PROFILES_FILE = BASE_DIR / "eval" / "profils_evaluation.json"
RESULTS_DIR = BASE_DIR / "eval" / "resultats"

MODELS = {
    "minilm": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "e5": "intfloat/multilingual-e5-small",
}


# ---------------------------------------------------------------------------
# Métriques
# ---------------------------------------------------------------------------
def precision_at_k(relevances: list[int], k: int) -> float:
    """relevances : 1/0 pour les offres classées, dans l'ordre."""
    top = relevances[:k]
    return sum(top) / k if k else 0.0


def reciprocal_rank(relevances: list[int], k: int) -> float:
    for rank, rel in enumerate(relevances[:k], start=1):
        if rel:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Profils et pertinence
# ---------------------------------------------------------------------------
def load_profiles(path: Path = PROFILES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["profils"]


def profile_text(adapted: dict) -> str:
    return " ".join([adapted.get("formation", ""), " ".join(adapted.get("competences", []))])


def auto_relevance(profile: dict, job: dict) -> int:
    title = job.get("title") or ""
    if not re.search(profile["pertinent_si"], title, re.I):
        return 0
    if profile.get("sauf_si") and re.search(profile["sauf_si"], title, re.I):
        return 0
    return 1


def read_labels(path: Path) -> dict[tuple[str, str], int]:
    """Lit le CSV annoté : (profil_id, job_id) -> 1/0. Les lignes vides sont ignorées."""
    text = path.read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,")
    labels = {}
    for row in csv.DictReader(text.splitlines(), dialect=dialect):
        value = (row.get("pertinent") or "").strip().lower()
        if value in {"1", "oui", "o", "yes", "y", "x"}:
            labels[(row["profil_id"], row["job_id"])] = 1
        elif value in {"0", "non", "n", "no"}:
            labels[(row["profil_id"], row["job_id"])] = 0
    return labels


# ---------------------------------------------------------------------------
# Méthodes de classement : chacune retourne les indices des offres, du meilleur au pire
# ---------------------------------------------------------------------------
class RandomRanker:
    name = "random"

    def __init__(self, jobs, seed: int = 42):
        self.n = len(jobs)
        self.seed = seed

    def rank(self, adapted: dict, k: int) -> list[int]:
        rng = np.random.default_rng(self.seed)
        return rng.permutation(self.n)[:k].tolist()


class TfidfRanker:
    name = "tfidf"

    def __init__(self, jobs):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vectorizer = TfidfVectorizer(strip_accents="unicode", lowercase=True,
                                          ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform([j.get("title", "") for j in jobs])

    def rank(self, adapted: dict, k: int) -> list[int]:
        query = self.vectorizer.transform([profile_text(adapted)])
        scores = (self.matrix @ query.T).toarray().ravel()
        return np.argsort(-scores, kind="stable")[:k].tolist()


class EmbeddingRanker:
    def __init__(self, jobs, name: str, model=None):
        from recommendation_engine import RecommendationEngine
        self.name = name
        self.engine = RecommendationEngine(jobs=jobs, model_name=MODELS.get(name, name), model=model)

    def rank(self, adapted: dict, k: int) -> list[int]:
        scores = self.engine.profile_scores(adapted)
        if scores is None:
            return []
        return np.argsort(-scores, kind="stable")[:k].tolist()


def build_rankers(methods: list[str], jobs: list[dict]) -> list:
    rankers = []
    for m in methods:
        print(f"\nPréparation de la méthode « {m} »...")
        if m == "random":
            rankers.append(RandomRanker(jobs))
        elif m == "tfidf":
            rankers.append(TfidfRanker(jobs))
        else:
            rankers.append(EmbeddingRanker(jobs, m))
    return rankers


# ---------------------------------------------------------------------------
# Évaluation
# ---------------------------------------------------------------------------
def evaluate(profiles, jobs, rankers, k: int = 10, labels: dict | None = None):
    """Retourne {méthode: {profil_id: {...métriques...}}}."""
    results = {}
    for ranker in rankers:
        per_profile = {}
        for prof in profiles:
            adapted = adapt_profile(prof["profil"])
            top = ranker.rank(adapted, k)
            if labels is None:
                rels = [auto_relevance(prof, jobs[i]) for i in top]
                unlabeled = 0
            else:
                pairs = [(prof["id"], str(jobs[i].get("id"))) for i in top]
                unlabeled = sum(p not in labels for p in pairs)
                rels = [labels.get(p, 0) for p in pairs]
            per_profile[prof["id"]] = {
                "precision": precision_at_k(rels, k),
                "mrr": reciprocal_rank(rels, k),
                "non_annotees": unlabeled,
                "top": [jobs[i].get("title") for i in top[:3]],
            }
        results[ranker.name] = per_profile
    return results


def export_for_annotation(profiles, jobs, rankers, k: int, path: Path):
    """CSV des offres à annoter : chaque paire (profil, offre) n'apparaît qu'une
    fois, même si plusieurs méthodes l'ont proposée. L'ordre est mélangé pour ne
    pas savoir quelle méthode a proposé l'offre (annotation à l'aveugle)."""
    rows, seen = [], set()
    for prof in profiles:
        adapted = adapt_profile(prof["profil"])
        for ranker in rankers:
            for i in ranker.rank(adapted, k):
                key = (prof["id"], str(jobs[i].get("id")))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "profil_id": prof["id"], "domaine": prof["domaine"],
                    "job_id": key[1], "titre": jobs[i].get("title", ""),
                    "entreprise": jobs[i].get("company", ""), "lieu": jobs[i].get("location", ""),
                    "pertinent": "",
                })
    rng = np.random.default_rng(0)
    order = rng.permutation(len(rows))
    rows = sorted((rows[i] for i in order), key=lambda r: r["profil_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig : accents OK dans Excel
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)} paires profil-offre à annoter dans : {path}")
    print("Remplis la colonne « pertinent » avec 1 (pertinente) ou 0 (non pertinente).")


def print_report(results, profiles, k: int, n_jobs: int, relevant_counts: dict):
    methods = list(results)
    print(f"\n{'=' * 70}\nRÉSULTATS sur {n_jobs} offres, {len(profiles)} profils, k = {k}\n{'=' * 70}")

    header = f"{'Profil':14}" + "".join(f"{m:>10}" for m in methods) + f"{'pertin.':>9}"
    print(f"\nPrécision@{k} par profil (pertin. = offres pertinentes disponibles)")
    print(header)
    for prof in profiles:
        pid = prof["id"]
        row = "".join(f"{results[m][pid]['precision']:>10.2f}" for m in methods)
        n = relevant_counts.get(pid)
        print(f"{pid:14}{row}{(n if n is not None else '-'):>9}")

    print(f"\n{'Moyenne':14}" + "".join(
        f"{np.mean([r['precision'] for r in results[m].values()]):>10.2f}" for m in methods))
    print(f"{'MRR moyen':14}" + "".join(
        f"{np.mean([r['mrr'] for r in results[m].values()]):>10.2f}" for m in methods))

    missing = sum(r["non_annotees"] for m in methods for r in results[m].values())
    if missing:
        print(f"\nAttention : {missing} offre(s) recommandée(s) non annotée(s), comptée(s) "
              "comme non pertinentes. Relance --export-csv pour les ajouter.")


def main():
    ap = argparse.ArgumentParser(description="Évaluer les recommandations")
    ap.add_argument("--source", default="live", help="live, sample ou chemin d'un JSON")
    ap.add_argument("--all", action="store_true", help="Toutes les offres (pas seulement le Canada)")
    ap.add_argument("--methods", default="random,tfidf,minilm")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--profiles", default=str(PROFILES_FILE))
    ap.add_argument("--export-csv", help="Créer un CSV à annoter à la main")
    ap.add_argument("--labels", help="CSV annoté à utiliser à la place des règles automatiques")
    args = ap.parse_args()

    profiles = load_profiles(Path(args.profiles))
    jobs = load_jobs(args.source, canada_only=not args.all)
    print(f"Offres évaluées : {len(jobs)} ({'toutes' if args.all else 'Canada'})")

    rankers = build_rankers([m.strip() for m in args.methods.split(",") if m.strip()], jobs)

    if args.export_csv:
        export_for_annotation(profiles, jobs, rankers, args.k, Path(args.export_csv))
        return

    labels = read_labels(Path(args.labels)) if args.labels else None
    relevant_counts = None if labels else {
        p["id"]: sum(auto_relevance(p, j) for j in jobs) for p in profiles}
    results = evaluate(profiles, jobs, rankers, args.k, labels)
    print_report(results, profiles, args.k, len(jobs), relevant_counts or {})
    print(f"\nÉtiquettes : {'manuelles (' + args.labels + ')' if labels else 'automatiques (mots-clés)'}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"resultats_{datetime.now():%Y%m%d_%H%M}.json"
    out.write_text(json.dumps({
        "date": datetime.now().isoformat(timespec="minutes"),
        "offres": len(jobs), "perimetre": "toutes" if args.all else "canada",
        "k": args.k, "etiquettes": args.labels or "automatiques",
        "resultats": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Résultats détaillés : {out}")


if __name__ == "__main__":
    main()
