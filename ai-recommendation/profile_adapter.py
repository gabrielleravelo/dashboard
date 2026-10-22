
import json
from pathlib import Path


def adapt_profile(profile):
    """Convertir un profil structuré Tail'ed vers le format du moteur."""

    education = profile.get("education", [])
    skills = profile.get("skillsStructured", [])

    formation = " ".join(
        f"{item.get('program', '')} {item.get('school', '')}"
        for item in education
    ).strip()

    competences = [
        item["name"]
        for item in skills
        if item.get("name")
    ]

    # Les postes recherchés ne sont pas encore structurés
    # dans notre JSON : ne pas les inventer.
    postes_recherches = []

    return {
        "formation": formation,
        "competences": competences,
        "postes_recherches": postes_recherches
    }


if __name__ == "__main__":
    profile_path = Path("student_profile.json")

    with profile_path.open("r", encoding="utf-8") as file:
        profile = json.load(file)

    adapted_profile = adapt_profile(profile)

    print("\nPROFIL POUR LE MOTEUR DE RECOMMANDATION :")
    print(json.dumps(
        adapted_profile,
        ensure_ascii=False,
        indent=2
    ))
