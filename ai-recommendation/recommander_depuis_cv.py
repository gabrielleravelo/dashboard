"""
recommander_depuis_cv.py — Pipeline complet : CV (PDF) -> offres recommandées.

    CV PDF
      │  cv_to_structured.py   (données structurées, format Firestore)
      ▼
    Profil structuré  ──►  statut « ok » ?  ── non ──► pas de recommandations
      │  profile_adapter.py
      ▼
    Moteur de recommandation (recommendation_engine.py)
      ▼
    Top des offres

Règle du projet : on ne transmet jamais le CV brut à l'algorithme, seulement
des données structurées. Si le CV n'est pas assez standard pour être
structuré (statut différent de « ok »), l'étudiant ne reçoit pas de
recommandations.

Utilisation :
    python recommander_depuis_cv.py mon_cv.pdf                     # 100 offres de test
    python recommander_depuis_cv.py mon_cv.pdf --live              # vraies offres Tail'ed
    python recommander_depuis_cv.py mon_cv.pdf --live --canada --type internship
    python recommander_depuis_cv.py profil.json --live             # profil déjà structuré
    python recommander_depuis_cv.py mon_cv.pdf --live --json resultats.json

Confidentialité : le script n'affiche jamais le nom ni les coordonnées de
l'étudiant, seulement les éléments utiles au matching.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from profile_adapter import adapt_profile


def load_structured_profile(path: Path) -> dict:
    """PDF -> parseur ; JSON -> profil déjà structuré (ex. sortie de cv_to_structured.py
    ou profil Firestore récupéré avec fetch_cvs.py --profiles-only)."""
    if path.suffix.lower() == ".pdf":
        from cv_to_structured import parse_resume_pdf
        return parse_resume_pdf(path)
    profile = json.loads(path.read_text(encoding="utf-8"))
    # Un profil saisi à la main n'a pas de statut : on le considère exploitable.
    profile.setdefault("_meta", {"status": "ok", "recommendable": True, "reason": ""})
    return profile


def summarize(profile: dict) -> None:
    meta = profile.get("_meta", {})
    print("\nPROFIL STRUCTURÉ")
    print(f"  Statut          : {meta.get('status', '?')} {meta.get('reason', '')}".rstrip())
    print(f"  Expériences     : {len(profile.get('experiences') or [])}")
    for edu in profile.get("education") or []:
        print(f"  Formation       : {edu.get('program', '')} | {edu.get('school', '')}")
    skills = [s.get("name") for s in profile.get("skillsStructured") or [] if s.get("name")]
    print(f"  Projets         : {len(profile.get('projects') or [])}")
    print(f"  Compétences ({len(skills)}) : {', '.join(skills[:15])}{' …' if len(skills) > 15 else ''}")


def main():
    ap = argparse.ArgumentParser(description="CV -> offres recommandées (Tail'ed)")
    ap.add_argument("cv", help="CV en PDF, ou profil structuré en JSON")
    ap.add_argument("--live", action="store_true", help="Utiliser les vraies offres Tail'ed")
    ap.add_argument("--canada", action="store_true", help="Offres au Canada seulement")
    ap.add_argument("--type", choices=["internship", "new_grad"], help="Type de poste")
    ap.add_argument("--top", type=int, default=10, help="Nombre d'offres à afficher")
    ap.add_argument("--json", help="Enregistrer les recommandations dans ce fichier")
    ap.add_argument("--force", action="store_true",
                    help="Recommander même si le CV n'est pas « ok » (tests seulement)")
    args = ap.parse_args()

    path = Path(args.cv)
    if not path.is_file():
        sys.exit(f"Fichier introuvable : {path}")

    # 1. CV -> données structurées
    profile = load_structured_profile(path)
    summarize(profile)

    # 2. Règle du projet : CV non standard -> pas de recommandations
    if not profile.get("_meta", {}).get("recommendable", False) and not args.force:
        print("\nCe CV n'a pas pu être structuré de façon fiable : aucune recommandation.")
        print("L'étudiant peut compléter son profil à la main dans Tail'ed.")
        sys.exit(2)

    # 3. Profil structuré -> entrée du moteur
    engine_profile = adapt_profile(profile)
    if not engine_profile.get("formation") and not engine_profile.get("competences"):
        sys.exit("\nNi formation ni compétences dans le profil : impossible de recommander.")

    # 4. Recommandations
    from recommendation_engine import RecommendationEngine
    engine = RecommendationEngine(source="live" if args.live else "sample",
                                  canada_only=args.canada)
    results = engine.recommend(engine_profile, job_type=args.type, top_k=args.top)

    print(f"\nTOP {len(results)} DES OFFRES RECOMMANDÉES")
    for rank, job in enumerate(results, start=1):
        print(f"{rank:2}. {job['score']:.3f}  {job['title']}")
        print(f"           {job['company']} | {job['location']} | {job['type']}")
        print(f"           {job['url']}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nRecommandations enregistrées dans : {args.json}")


if __name__ == "__main__":
    main()
