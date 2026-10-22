"""
test_profil_genie_electrique.py — Est-ce qu'un étudiant en génie électrique
est recommandé aux offres d'électrique ?

Profil FICTIF, déjà au format structuré Tail'ed (comme la sortie de
cv_to_structured.py), passé au moteur via profile_adapter.

Utilisation :
    python test_profil_genie_electrique.py                 # 100 offres de test
    python test_profil_genie_electrique.py --live          # ~28 000 vraies offres
    python test_profil_genie_electrique.py --live --canada # vraies offres au Canada
"""

import argparse

from profile_adapter import adapt_profile
from recommendation_engine import RecommendationEngine

# Profil fictif d'un étudiant en génie électrique (format Tail'ed)
profil_structure = {
    "education": [{
        "id": "edu-1",
        "school": "Polytechnique Montréal",
        "program": "Baccalauréat en génie électrique",
        "source": "resume-parse",
    }],
    "experiences": [{
        "id": "exp-1",
        "title": "Stagiaire en génie électrique",
        "organization": "Hydro Exemple Inc.",
        "source": "resume-parse",
    }],
    "projects": [],
    "skillsStructured": [
        {"name": "MATLAB", "category": "language"},
        {"name": "Simulink"},
        {"name": "AutoCAD Electrical"},
        {"name": "Automates programmables (PLC)"},
        {"name": "Conception de circuits"},
        {"name": "Arduino"},
    ],
}

MOTS_ELECTRIQUE = ("electrical", "électrique", "electrique", "electronic",
                   "électronique", "power", "hardware", "embedded", "controls")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Utiliser les vraies offres")
    ap.add_argument("--canada", action="store_true", help="Offres au Canada seulement")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    profil = adapt_profile(profil_structure)
    print("Profil envoyé au moteur :", profil)

    engine = RecommendationEngine(
        source="live" if args.live else "sample",
        canada_only=args.canada,
    )
    total = len(engine.jobs)
    resultats = engine.recommend(profil, job_type=None, top_k=total)

    print(f"\nTOP {args.top} sur {total} offres :")
    for rang, job in enumerate(resultats[:args.top], start=1):
        print(f"{rang:2}. {job['score']:.4f}  {job['title']} | {job['company']} | {job['location']}")

    rangs = [r for r, job in enumerate(resultats, start=1)
             if any(m in (job["title"] or "").lower() for m in MOTS_ELECTRIQUE)]
    print(f"\nOffres liées à l'électrique : {len(rangs)} sur {total}")
    if rangs:
        dans_top = sum(r <= args.top for r in rangs)
        print(f"Dans le top {args.top} : {dans_top}")
        print(f"Meilleur rang : #{rangs[0]}")

    scores = [r["score"] for r in resultats]
    if len(scores) >= args.top:
        print(f"\nÉcart entre le 1er et le {args.top}e score : {scores[0] - scores[args.top - 1]:.4f}")


if __name__ == "__main__":
    main()
