"""Tests du parseur : mêmes cas que dashboard/functions/src/lib/__tests__/resume-parser.test.ts
Lancer : python -m pytest test_cv_to_structured.py -v
"""
from cv_to_structured import parse_resume_text


def test_title_first():
    r = parse_resume_text("""
Jordan Smith
jordan@example.com

EXPERIENCE
Software Engineer, Acme Technologies Inc.
June 2022 - Present
- Built internal tooling used by 40 engineers
- Reduced build time by 30%

EDUCATION
University of Toronto
Bachelor of Science in Computer Science
2022
""")
    e = r["experiences"]
    assert len(e) == 1
    assert e[0]["title"] == "Software Engineer"
    assert e[0]["organization"] == "Acme Technologies Inc."
    assert e[0]["startDate"] == "2022-06"
    assert e[0]["current"] is True and e[0]["endDate"] is None
    assert e[0]["source"] == "resume-parse"


def test_company_first():
    e = parse_resume_text("""
EXPERIENCE
ACME TECHNOLOGIES INC.
Software Engineer
Jan 2021 - Dec 2022
- Shipped features end to end

Beta Robotics Corp.
Marketing Coordinator
2019 - 2020
- Ran the summer campaign
""")["experiences"]
    assert len(e) == 2
    assert (e[0]["title"], e[0]["organization"]) == ("Software Engineer", "ACME TECHNOLOGIES INC.")
    assert (e[1]["title"], e[1]["organization"]) == ("Marketing Coordinator", "Beta Robotics Corp.")


def test_location_stripped():
    e = parse_resume_text("""
EXPERIENCE
Product Designer, Bright Studio Inc., Toronto, ON
March 2020 - August 2021
- Led design for the mobile app
""")["experiences"]
    assert len(e) == 1
    assert e[0]["title"] == "Product Designer"
    assert e[0]["organization"] == "Bright Studio Inc."
    assert e[0]["location"] == "Toronto, ON"


def test_french_resume():
    r = parse_resume_text("""
EXPÉRIENCE PROFESSIONNELLE
Développeuse logicielle, Solutions Numériques Inc.
de janvier 2022 à présent
- A conçu des applications web

FORMATION
Université de Montréal
Baccalauréat en informatique
2021
""")
    e, ed = r["experiences"], r["education"]
    assert len(e) == 1
    assert e[0]["title"] == "Développeuse logicielle"
    assert e[0]["organization"] == "Solutions Numériques Inc."
    assert e[0]["current"] is True and e[0]["startDate"] == "2022-01"
    assert len(ed) == 1
    assert "Université de Montréal" in ed[0]["school"]
    assert "Baccalauréat" in ed[0]["program"]
    assert ed[0]["graduationYear"] == "2021"


def test_season_and_single_year():
    e = parse_resume_text("""
EXPERIENCE
Marketing Intern, Northwind Co.
Summer 2023
- Assisted with campaign launches

Research Assistant, State University Lab
2021
- Collected and analyzed survey data
""")["experiences"]
    assert len(e) == 2
    s = next(x for x in e if x["organization"] == "Northwind Co.")
    assert s["title"] == "Marketing Intern" and s["employmentType"] == "internship"
    assert "startDate" not in s and "Summer 2023" in s["description"]
    y = next(x for x in e if x["organization"] == "State University Lab")
    assert y["title"] == "Research Assistant"


def test_synonym_heading():
    e = parse_resume_text("""
CAREER HISTORY
Business Analyst, Contoso Ltd.
2020 - 2022
- Modeled quarterly forecasts
""")["experiences"]
    assert len(e) == 1
    assert (e[0]["title"], e[0]["organization"]) == ("Business Analyst", "Contoso Ltd.")


def test_skill_categories():
    s = parse_resume_text("""
SKILLS
Languages: Python, JavaScript, SQL
Frameworks: React, Django
Tools: Docker, Git, Figma
Soft Skills: Leadership, Communication
""")["skillsStructured"]
    cat = {x["name"].lower(): x.get("category") for x in s}
    assert cat["python"] == cat["javascript"] == "language"
    assert cat["react"] == cat["django"] == "framework"
    assert cat["docker"] == cat["git"] == "tool"
    assert cat["leadership"] == cat["communication"] == "soft"
    assert all("level" not in x for x in s)


def test_employment_type():
    e = parse_resume_text("""
EXPERIENCE
Software Engineering Intern, Acme Inc.
Summer 2022
- Built features

Volunteer Coordinator, Local Food Bank
2021 - 2022
- Organized weekly drives

Co-op Student, Big Corp
Jan 2023 - Apr 2023
- Worked on the platform team

Product Manager, Steady Co.
2019 - 2020
- Owned the roadmap
""")["experiences"]
    by = lambda org: next(x for x in e if org in x["organization"])
    assert by("Acme")["employmentType"] == "internship"
    assert by("Food Bank")["employmentType"] == "volunteer"
    assert by("Big Corp")["employmentType"] == "co-op"
    assert "employmentType" not in by("Steady Co.")


def test_school_only():
    ed = parse_resume_text("""
EDUCATION
Humber College
Data Analytics
2023
""")["education"]
    assert len(ed) == 1
    assert (ed[0]["school"], ed[0]["program"], ed[0]["graduationYear"]) == (
        "Humber College", "Data Analytics", "2023")


def test_degree_only():
    ed = parse_resume_text("""
EDUCATION
Bachelor of Arts in Psychology
Springfield Campus
2020
""")["education"]
    assert len(ed) == 1
    assert "Bachelor of Arts" in ed[0]["program"]
    assert ed[0]["fieldOfStudy"] == "Psychology"


def test_garbage_input():
    for t in ["", "   ", "asdkjh asd \n\n\n ---- \t\t", "🎉🎉🎉\n\n"]:
        assert parse_resume_text(t) == {
            "experiences": [], "education": [], "projects": [], "skillsStructured": []}


def test_location_line_not_org():
    e = parse_resume_text("""
EXPERIENCE
Sales Associate
Toronto, ON
Retailer Group Inc.
May 2018 - Sep 2019
- Handled customer inquiries
""")["experiences"]
    assert len(e) == 1
    assert e[0]["title"] == "Sales Associate"
    assert e[0]["organization"] == "Retailer Group Inc."
    assert e[0]["location"] == "Toronto, ON"


# --- Tests ajoutés (format de ton CV : « Programme — École | années ») ---
def test_one_line_education():
    ed = parse_resume_text("""
FORMATION
Baccalauréat en intelligence artificielle — Université de Montréal | 2023 – 2026
""")["education"]
    assert len(ed) == 1
    assert ed[0]["school"] == "Université de Montréal"
    assert ed[0]["program"].startswith("Baccalauréat")
    assert ed[0]["startYear"] == "2023" and ed[0]["graduationYear"] == "2026"


# --- Tests ajoutés après les essais sur de vrais CV ---
def test_plural_french_headings():
    r = parse_resume_text("""
COMPÉTENCES
Python, SQL

EXPÉRIENCES PROFESSIONNELLES
Analyste de données, Groupe Exemple Inc.
Janvier 2023 - Août 2024
- Tableaux de bord

PROJETS ACADÉMIQUES
Moteur de recommandation | 2025
""")
    assert len(r["skillsStructured"]) == 2
    assert len(r["experiences"]) == 1 and r["experiences"][0]["endDate"] == "2024-08"
    assert r["projects"][0]["name"] == "Moteur de recommandation"


def test_orphan_and_wrapped_bullets():
    e = parse_resume_text("""
EXPERIENCE
Tour Guide, City Museum Inc.
2021 - 2022
– Led guided tours, delivered presentations, and
strengthened public communication skills.

– Supported event organization and developed leadership.
""")["experiences"]
    assert len(e) == 1
    assert "strengthened public communication" in e[0]["description"]
    assert "Supported event organization" in e[0]["description"]


def test_project_titles_are_not_headings():
    r, meta = parse_resume_text("""
Projets
Projet personnel full-stack
- Application React et Node.js
GitHub | Projet personnel
- Script Python
Expériences et projets
Formation
Baccalauréat en informatique, Université Laval, 2025
""", with_meta=True)
    assert meta["headings"] == ["Projets", "Expériences et projets", "Formation"]
    assert r["projects"][0]["name"] == "Projet personnel full-stack"


def test_plural_first_word_heading():
    r, meta = parse_resume_text("""
Compétences techniques
Python, FastAPI, Pydantic
Projets de développement fullstack
Projet personnel full-stack
- Application React
Projet personnel en Data Science
- Analyse avec Pandas
Formation
Baccalauréat en génie informatique, Polytechnique Montréal, 2026
""", with_meta=True)
    assert meta["headings"] == ["Compétences techniques", "Projets de développement fullstack", "Formation"]
    names = [p["name"] for p in r["projects"]]
    assert names == ["Projet personnel full-stack", "Projet personnel en Data Science"]
    assert len(r["skillsStructured"]) == 3


def test_project_descriptions_not_split():
    p = parse_resume_text("""
PROJETS
Jeu RPG Tactique Multijoueur
- Moteur de combat au tour par tour

Conception et développement complet frontend et backend avec WebSockets.

API REST de Gestion de Budget Personnel

Développement autonome d’une API RESTful avec FastAPI et PostgreSQL
8 endpoints authentifiés (JWT/bcrypt), CRUD complet.

Suite d’Outils Cryptographiques (Crypto Toolkit)
- Chiffrement AES et RSA
Construction complète d’une application en ligne de commande
Tests pytest avec 93%+ de couverture. Tests de régression.

Système de Détection d’Intrusions par Machine Learning
Développement autonome d’un système IDS avec scikit-learn
""")["projects"]
    assert [x["name"] for x in p] == [
        "Jeu RPG Tactique Multijoueur",
        "API REST de Gestion de Budget Personnel",
        "Suite d’Outils Cryptographiques (Crypto Toolkit)",
        "Système de Détection d’Intrusions par Machine Learning",
    ]
    assert "8 endpoints" in p[1]["description"]
    assert "93%" in p[2]["description"]


# --- Tests ajoutés après les 20 CV de la base de dev ---
def test_summary_of_skills_template():
    r, meta = parse_resume_text("""
JANE DOE
jane@example.com

SUMMARY OF SKILLS AND QUALIFICATIONS
• Proficient in Python, Java and SQL with experience in React
• Strong communication and teamwork skills

EXPERIENCE
Software Developer Intern, Acme Inc.
Jan 2024 - Apr 2024
• Built REST APIs
Technical skills: Docker, PostgreSQL

VOLUNTEER WORK
Tutor, Community Centre
Sep 2022 - Present
• Helped students with math

EXTRA-CURRICULAR ACTIVITIES
Hackathon Club Member
""", with_meta=True)
    names = {s["name"].lower() for s in r["skillsStructured"]}
    assert {"python", "java", "sql", "react", "communication", "docker", "postgresql"} <= names
    assert not any("proficient" in n for n in names)
    exps = r["experiences"]
    assert [e["organization"] for e in exps] == ["Acme Inc.", "Community Centre"]
    assert exps[1]["employmentType"] == "volunteer"
    assert "JANE DOE" not in meta["unknown"]   # jamais le nom de l'étudiant
    assert meta["warnings"] == []