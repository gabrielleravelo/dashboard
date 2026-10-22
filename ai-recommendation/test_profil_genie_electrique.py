"""
test_profil_genie_electrique.py — Est-ce qu'un étudiant en génie électrique
est recommandé aux offres d'électrique ?

Profil FICTIF, déjà au format structuré Tail'ed (comme la sortie de
cv_to_structured.py), passé au moteur via profile_adapter.

Utilisation : python test_profil_genie_electrique.py
"""

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
    profil = adapt_profile(profil_structure)
    print("Profil envoyé au moteur :", profil)

    engine = RecommendationEngine()
    total = len(engine.jobs)
    resultats = engine.recommend(profil, job_type=None, top_k=total)

    print(f"\nTOP 10 sur {total} offres :")
    for rang, job in enumerate(resultats[:10], start=1):
        print(f"{rang:2}. {job['score']:.4f}  {job['title']} | {job['company']}")

    print("\nRang des offres liées à l'électrique :")
    trouve = False
    for rang, job in enumerate(resultats, start=1):
        if any(m in (job["title"] or "").lower() for m in MOTS_ELECTRIQUE):
            trouve = True
            print(f"  #{rang:3} / {total}  {job['score']:.4f}  {job['title']} | {job['company']}")
    if not trouve:
        print("  Aucune offre avec un mot-clé d'électrique dans le titre.")

    scores = [r["score"] for r in resultats]
    print(f"\nÉcart entre le 1er et le 10e score : {scores[0] - scores[9]:.4f}")
    print(f"Écart entre le 1er et le dernier   : {scores[0] - scores[-1]:.4f}")


if __name__ == "__main__":
    main()
