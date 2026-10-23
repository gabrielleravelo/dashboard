"""
cv_to_structured.py — Transformer un CV (PDF) en données structurées Tail'ed.

Portage Python fidèle du parseur officiel de Tail'ed :
    dashboard/functions/src/lib/resume-parser.ts  (POST /profile/parse-resume)

Pourquoi un portage ?
    Les données produites ici ont EXACTEMENT le même schéma que ce que la
    plateforme enregistre dans Firestore (profiles/{uid}) :
        experiences[]      -> Experience  (src/lib/profile.ts §4.1)
        education[]        -> Education   (§4.2)
        projects[]         -> Project     (§4.3)
        skillsStructured[] -> SkillEntry  (§4.4)
    Le moteur de recommandation peut donc être testé en local sur des données
    structurées identiques à celles de la prod (principe de Franck : on passe
    des DONNÉES STRUCTURÉES à l'algorithme, jamais le CV brut).

Ajouts par rapport au parseur TypeScript (signalés « AJOUT ») :
    1. Deux modes d'extraction PDF (texte standard + reconstruction de la mise
       en page) ; on garde automatiquement le meilleur résultat.
    2. Un bloc `_meta` avec un statut de qualité (ok / partiel / echec) et
       `recommendable` : si le CV n'est pas standard, pas de recommandations.
    3. Formation sur une seule ligne « Programme — École » : on sépare les deux.
    4. Mode lot : un dossier entier de CV -> un JSON par CV + un rapport.

Utilisation :
    python cv_to_structured.py mon_cv.pdf
    python cv_to_structured.py ../cv_tests_prives            (tout un dossier)
    python cv_to_structured.py ../cv_tests_prives --out ../profils_structures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Normalisation (insensible aux accents et à la ponctuation)
# ---------------------------------------------------------------------------


def strip_accents(s: str) -> str:
    return re.sub(r"[̀-ͯ]", "", unicodedata.normalize("NFD", s))


def normalize_for_match(s: str) -> str:
    """Minuscules, sans accents, ponctuation -> espace, entouré d'espaces."""
    core = strip_accents(s).lower()
    core = re.sub(r"[^a-z0-9']+", " ", core)
    core = re.sub(r"\s+", " ", core).strip()
    return f" {core} "


def contains_phrase(normalized: str, keyword: str) -> bool:
    return f" {keyword} " in normalized


def contains_any(normalized: str, keywords) -> bool:
    return any(contains_phrase(normalized, k) for k in keywords)


def to_year_month(d):
    """{'year': 2022, 'month': 6} -> '2022-06' (None si pas de mois)."""
    if not d or not d.get("month") or not 1 <= d["month"] <= 12:
        return None
    return f"{d['year']}-{d['month']:02d}"


# ---------------------------------------------------------------------------
# Dates (EN + FR : mois, saisons, trimestres, années, « depuis »)
# ---------------------------------------------------------------------------
MONTHS = {
    "jan": 1, "january": 1, "janv": 1, "janvier": 1,
    "feb": 2, "february": 2, "fev": 2, "fevr": 2, "fevrier": 2,
    "mar": 3, "march": 3, "mars": 3,
    "apr": 4, "april": 4, "avr": 4, "avril": 4,
    "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juin": 6,
    "jul": 7, "july": 7, "juil": 7, "juillet": 7,
    "aug": 8, "august": 8, "aout": 8,
    "sep": 9, "sept": 9, "september": 9, "septembre": 9,
    "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11,
    "dec": 12, "december": 12, "decembre": 12,
}

SEASON_MONTHS = {
    "spring": 3, "printemps": 3,
    "summer": 6, "ete": 6,
    "fall": 9, "autumn": 9, "automne": 9,
    "winter": 12, "hiver": 12,
}


def parse_date_token(raw: str):
    s = strip_accents(raw.strip().lower())
    m = re.match(r"^([a-z]{3,9})\.?\s+(\d{4})$", s)  # accents déjà retirés
    if m and m.group(1) in MONTHS:
        return {"year": int(m.group(2)), "month": MONTHS[m.group(1)]}
    m = re.match(r"^([a-z]{3,10})\s+(\d{4})$", s)
    if m and m.group(1) in SEASON_MONTHS:
        return {"year": int(m.group(2))}
    m = re.match(r"^[qt][1-4]\s+(\d{4})$", s)
    if m:
        return {"year": int(m.group(1))}
    m = re.match(r"^(\d{1,2})[/-](\d{4})$", s)
    if m:
        mo = int(m.group(1))
        return {"year": int(m.group(2)), "month": mo if 1 <= mo <= 12 else None}
    m = re.match(r"^(\d{4})[-/](\d{1,2})$", s)
    if m:
        mo = int(m.group(2))
        return {"year": int(m.group(1)), "month": mo if 1 <= mo <= 12 else None}
    m = re.match(r"^(\d{4})$", s)
    if m:
        return {"year": int(m.group(1))}
    return None


# AJOUT : lettres accentuées acceptées (« Août 2024 », « février 2023 »)
MONTH_NAME = r"(?<![A-Za-zÀ-ÿ])[A-Za-zÀ-ÿ]{3,9}\.?"  # et pas au milieu d'un mot (« University 2022 »)
SEASON_NAME = r"(?:spring|summer|fall|autumn|winter|printemps|[ée]t[ée]|automne|hiver)"
DATE_TOKEN = (
    rf"(?:{MONTH_NAME}\s+\d{{4}}|{SEASON_NAME}\s+\d{{4}}|[QT][1-4]\s+\d{{4}}"
    r"|\d{1,2}[/-]\d{4}|\d{4}[-/]\d{1,2}|(?:19|20)\d{2})"
)
PRESENT = (
    r"(?:present|current|ongoing|now|today|pr[ée]sent[e]?|actuel(?:le)?"
    r"|aujourd['’]?hui|en\s+cours)"  # AJOUT : apostrophe typographique ’ (Word)
)
RANGE_RE = re.compile(
    rf"({DATE_TOKEN})\s*(?:[-–—]|to|until|through|thru|jusqu'?[àa]|jusqu'?en|[àa]|au)"
    rf"\s*({PRESENT}|{DATE_TOKEN})",
    re.I,
)
SINCE_RE = re.compile(rf"\b(?:since|depuis)\s+({DATE_TOKEN})", re.I)
SINGLE_DATE_RE = re.compile(rf"({DATE_TOKEN})", re.I)
PRESENT_RE = re.compile(PRESENT, re.I)


def clean_remainder(line: str, start: int, length: int) -> str:
    s = line[:start] + " " + line[start + length:]
    s = re.sub(r"[|,•·\-–—]+\s*$", "", s)
    s = re.sub(r"^\s*[|,•·\-–—]+", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def detect_date_range(line: str):
    m = RANGE_RE.search(line)
    if m:
        end_raw = m.group(2)
        current = bool(PRESENT_RE.search(end_raw))
        return {
            "start": parse_date_token(m.group(1)),
            "end": None if current else parse_date_token(end_raw),
            "current": current,
            "remainder": clean_remainder(line, m.start(), len(m.group(0))),
            "label": m.group(0).strip(),
        }
    m = SINCE_RE.search(line)
    if m:
        return {
            "start": parse_date_token(m.group(1)),
            "end": None,
            "current": True,
            "remainder": clean_remainder(line, m.start(), len(m.group(0))),
            "label": m.group(0).strip(),
        }
    m = SINGLE_DATE_RE.search(line)
    if m:
        start = parse_date_token(m.group(1))
        if not start:
            return None
        return {
            "start": start,
            "end": None,
            "current": False,
            "remainder": clean_remainder(line, m.start(), len(m.group(0))),
            "label": m.group(0).strip(),
        }
    return None


# ---------------------------------------------------------------------------
# Détection des sections (titres approximatifs EN + FR)
# ---------------------------------------------------------------------------
HEADING_SYNONYMS = {
    "experience": [
        "experience", "experiences", "work experience", "professional experience",
        "employment", "employment history", "work history", "relevant experience",
        "professional background", "career history", "career summary",
        "internship experience", "internships", "co op experience",
        "where i've worked", "where i have worked", "experience professionnelle",
        "experience de travail", "parcours professionnel", "historique d'emploi",
        "emplois", "stages",
    ],
    "education": [
        "education", "academic background", "education & training",
        "education and training", "academics", "academic history", "formation",
        "formation academique", "scolarite", "parcours academique",
        "parcours scolaire", "cursus", "diplomes",
    ],
    "projects": [
        "projects", "personal projects", "academic projects", "selected projects",
        "side projects", "notable projects", "portfolio", "projets",
        "projets personnels", "realisations",
        # AJOUT
        "projets academiques", "projets scolaires", "projets universitaires",
        "projets techniques", "school projects", "technical projects",
        "experiences academiques", "realisations academiques",
    ],
    "skills": [
        "skills", "technical skills", "technologies", "competencies",
        "core competencies", "skills & technologies", "skills and technologies",
        "technical proficiencies", "tools & technologies", "tools and technologies",
        "competences", "competences techniques", "outils", "langues et competences",
        # AJOUT
        "competences informatiques", "competences cles", "technical skills & tools",
        "competences numeriques", "competences personnelles",
        "competences transversales", "competences professionnelles",
        "outils et technologies", "technical expertise",
        # AJOUT (modèle de CV très fréquent dans la base Tail'ed)
        "summary of skills and qualifications", "summary of skills",
        "summary of qualifications", "skills and qualifications", "qualifications",
        "highlights of qualifications", "core skills", "key skills", "skills summary",
        "skill summary", "technical summary", "skills and abilities",
    ],
    # AJOUT : sections reconnues mais ignorées, pour qu'elles ne soient pas
    # avalées par la section précédente (ex. « LANGUES » après « COMPÉTENCES »).
    "_ignore": [
        "profil", "profile", "summary", "professional summary", "about me", "a propos",
        "objectif", "objectifs", "objective", "career objective", "langues", "languages",
        "langues et objectif", "references", "certifications", "certificats",
        "interets", "interests", "loisirs", "hobbies",
        "activites", "activities", "distinctions", "awards", "prix", "contact",
        "informations complementaires", "additional information", "publications",
        # AJOUT (vrais CV Tail'ed)
        "extra curricular activities", "extracurricular activities", "extra curricular",
        "awards distinctions", "awards and distinctions", "honors and awards",
        "honours and awards", "honors", "honours", "achievements", "scholarships",
        "professional associations", "associations", "memberships",
        "leadership and involvement", "involvement", "activites parascolaires",
        "prix et distinctions", "bourses", "implication", "engagement",
    ],
    # AJOUT : bénévolat -> expériences avec employmentType « volunteer »
    "volunteer": [
        "volunteer work", "volunteer experience", "volunteering", "volunteer",
        "community involvement", "community service", "benevolat",
        "experience de benevolat", "implication communautaire",
    ],
}

MAX_HEADING_LEN = 40
MAX_HEADING_WORDS = 6


def _singular(text: str) -> str:
    """AJOUT : « experiences professionnelles » == « experience professionnelle »."""
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in text.split())


def _heading_text(line: str) -> str:
    normalized = normalize_for_match(line).strip()
    words = normalized.split()
    # AJOUT : titres espacés « E X P É R I E N C E »
    if len(words) >= 4 and all(len(w) == 1 for w in words):
        normalized = "".join(words)
    return _singular(normalized)


_SYNONYMS_SINGULAR = {key: [_singular(s) for s in syns] for key, syns in HEADING_SYNONYMS.items()}


PLURAL_SECTION_WORDS = {
    "projets": "projects", "projects": "projects", "realisations": "projects",
    "experiences": "experience", "emplois": "experience", "stages": "experience",
    "internships": "experience",
    "competences": "skills", "skills": "skills", "technologies": "skills",
    "formations": "education", "diplomes": "education",
}


def _exact_heading(normalized: str):
    for key, synonyms in _SYNONYMS_SINGULAR.items():
        if normalized in synonyms:
            return key
    return None


def match_heading(line: str):
    trimmed = line.strip().strip(":").strip()
    if not trimmed or len(trimmed) > MAX_HEADING_LEN:
        return None
    if len(trimmed.split()) > MAX_HEADING_WORDS and not re.fullmatch(r"(?:\S\s+){3,}\S", trimmed):
        return None
    if "|" in trimmed:  # « GitHub | Projet personnel » est une entrée, pas un titre
        return None
    normalized = _heading_text(trimmed)
    if not normalized:
        return None

    # 1. Correspondance exacte (« Compétences techniques », « Formation »)
    key = _exact_heading(normalized)
    if key:
        return key

    # 2. AJOUT : titre combiné « Expériences et projets » -> première partie
    parts = [p.strip() for p in re.split(r"\s+(?:et|and)\s+", normalized) if p.strip()]
    if len(parts) >= 2:
        keys = [_exact_heading(p) for p in parts]
        if all(keys):
            return keys[0]

    # 3. AJOUT : titre qui commence par un mot de section AU PLURIEL
    #    « Projets de développement fullstack » -> section projets
    #    « Projet personnel full-stack »        -> une entrée (singulier)
    first = normalize_for_match(trimmed).split()[0] if normalized else ""
    if (first in PLURAL_SECTION_WORDS and len(trimmed.split()) <= 5
            and not re.search(r"\d", trimmed)):
        return PLURAL_SECTION_WORDS[first]

    # 4. Containment (« CAREER HISTORY & IMPACT ») seulement pour les titres
    #    en MAJUSCULES, sinon « Projet personnel full-stack » serait pris
    #    pour un titre de section.
    if not is_all_capsish(trimmed):
        return None
    best = None
    for key, synonyms in _SYNONYMS_SINGULAR.items():
        for synonym in synonyms:
            if " " in synonym and synonym in normalized:
                if best is None or len(synonym) > best[1]:
                    best = (key, len(synonym))
    return best[0] if best else None


def looks_like_heading(line: str) -> bool:
    """AJOUT (diagnostic) : ligne courte en MAJUSCULES, sans chiffres."""
    t = line.strip().strip(":").strip()
    return (3 <= len(t) <= MAX_HEADING_LEN and len(t.split()) <= 5
            and not re.search(r"\d|@|http", t) and is_all_capsish(t))


# AJOUT : « Technical skills: React, Node.js » dans un projet ou une expérience
# -> envoyé vers les compétences.
SKILL_LABEL_RE = re.compile(
    r"^\s*(?:[-–—*•·▪◦]\s*)?(?:technical skills|key skills|skills|core competencies|"
    r"competencies|technologies(?: used)?|tech stack|stack|tools(?: used)?|outils|"
    r"comp[ée]tences(?: techniques)?|technologies utilis[ée]es|environnement technique)"
    r"\s*:\s*\S", re.I)


def segment_sections(lines):
    sections = {"experience": [], "education": [], "projects": [], "skills": [],
                "volunteer": [], "_ignore": []}
    headings_found, unknown = [], []
    current = None
    for line in lines:
        heading = match_heading(line)
        if heading:
            current = heading
            headings_found.append(line.strip())
            continue
        # Diagnostic seulement après le 1er titre : le nom de l'étudiant (en
        # MAJUSCULES en haut du CV) n'apparaît jamais dans la sortie.
        if current and looks_like_heading(line):
            unknown.append(line.strip())
        if current and current != "skills" and SKILL_LABEL_RE.match(line):
            sections["skills"].append(line)
            continue
        if current:
            sections[current].append(line)
    return sections, headings_found, unknown


# ---------------------------------------------------------------------------
# Puces
# ---------------------------------------------------------------------------
BULLET_RE = re.compile(r"^[\s]*[-–—*•·▪◦►▶]+\s*")


def is_bullet(line: str) -> bool:
    return bool(BULLET_RE.match(line))


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line, count=1).strip()


# ---------------------------------------------------------------------------
# Lieux (« Montréal, QC », « Remote ») — à retirer avant titre/organisation
# ---------------------------------------------------------------------------
PROVINCE_STATE_ABBR = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
COUNTRY_NAMES = [
    "canada", "united states", "usa", "u s a", "us", "france", "mexico",
    "united kingdom", "uk", "germany", "china", "india", "australia", "japan",
    "brazil", "spain", "italy", "netherlands", "belgium", "switzerland",
]
REMOTE_WORDS = ["remote", "hybrid", "on site", "onsite", "a distance", "teletravail"]


def pure_location_line(line: str):
    trimmed = line.strip()
    if not trimmed or len(trimmed) > 60:
        return None
    normalized = normalize_for_match(trimmed).strip()
    if contains_any(f" {normalized} ", REMOTE_WORDS) and len(trimmed.split()) <= 3:
        return trimmed
    m = re.match(r"^[A-Za-zÀ-ÿ.'\- ]{2,40},\s*([A-Za-z]{2})(?:,\s*[A-Za-zÀ-ÿ ]+)?$", trimmed)
    if m and m.group(1).upper() in PROVINCE_STATE_ABBR:
        return trimmed
    parts = [p.strip() for p in trimmed.split(",")]
    if len(parts) == 2 and parts[0]:
        if normalize_for_match(parts[1]).strip() in COUNTRY_NAMES:
            return trimmed
    return None


def strip_location_from_line(line: str):
    text, location = line, None

    m = re.search(r",\s*([A-Za-zÀ-ÿ.'\- ]{2,40}),\s*([A-Za-z]{2})\b\.?(?:,\s*[A-Za-zÀ-ÿ ]+)?", text)
    if m and m.group(2).upper() in PROVINCE_STATE_ABBR:
        location = re.sub(r"^,\s*", "", m.group(0)).strip()
        text = text[:m.start()] + text[m.end():]

    if not location:
        countries = "|".join(c.replace(" ", r"\s+") for c in COUNTRY_NAMES)
        m = re.search(rf",\s*({countries})\b", text, re.I)
        if m:
            location = re.sub(r"^,\s*", "", m.group(0)).strip()
            text = text[:m.start()] + text[m.end():]

    m = re.search(r"[(,-]?\s*\b(remote|hybrid|on-?site|[àa]\s+distance|t[ée]l[ée]travail)\b\)?", text, re.I)
    if m:
        if not location:
            location = re.sub(r"\)$", "", re.sub(r"^[(,-]\s*", "", m.group(0))).strip()
        text = text[:m.start()] + text[m.end():]

    text = re.sub(r"[|,•·\-–—]+\s*$", "", text)
    text = re.sub(r"^\s*[|,•·\-–—]+", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text, location


# ---------------------------------------------------------------------------
# Titre du poste vs organisation
# ---------------------------------------------------------------------------
TITLE_KEYWORDS = [
    "engineer", "developer", "dev", "intern", "internship", "analyst", "manager",
    "designer", "scientist", "consultant", "coordinator", "assistant",
    "associate", "specialist", "lead", "director", "officer", "researcher",
    "architect", "administrator", "technician", "representative", "agent",
    "strategist", "producer", "editor", "writer", "marketer", "recruiter",
    "accountant", "auditor", "planner", "supervisor", "president", "founder",
    "ceo", "cto", "cfo", "vp", "vice president", "co-founder", "cofounder",
    "student", "trainee", "fellow", "volunteer",
    "stagiaire", "developpeur", "developpeuse", "ingenieur", "ingenieure",
    "analyste", "gestionnaire", "concepteur", "conceptrice", "chercheur",
    "chercheuse", "coordonnateur", "coordonnatrice", "assistante", "chef",
    "responsable", "directeur", "directrice", "fondateur", "fondatrice",
    "consultante", "redacteur", "redactrice", "charge de projet",
    "chargee de projet",
    # AJOUT : métiers étudiants fréquents
    "guide", "tutor", "tuteur", "tutrice", "mentor", "teacher", "instructor",
    "enseignant", "enseignante", "animateur", "animatrice", "cashier", "caissier",
    "caissiere", "server", "serveur", "serveuse", "barista", "clerk", "commis",
    "vendeur", "vendeuse", "operator", "operateur", "operatrice", "worker",
    "employe", "employee", "preparateur", "preparatrice", "programmer",
    "programmeur", "programmeuse", "teaching assistant", "auxiliaire",
    "technicienne", "member", "membre", "ambassador", "ambassadeur", "ambassadrice",
    "receptionist", "receptionniste", "conseiller", "conseillere", "associe",
]
ORG_KEYWORDS = [
    "inc", "ltd", "llc", "corp", "corporation", "co", "company", "technologies",
    "technology", "labs", "laboratories", "university", "universite", "college",
    "college", "ecole", "group", "groupe", "solutions", "systems", "systemes",
    "partners", "consulting", "studio", "studios", "agency", "agences",
    "foundation", "fondation", "institute", "institut", "holdings",
    "enterprises", "industries", "gmbh", "sa", "srl", "plc", "bank", "capital",
    # AJOUT
    "museum", "musee", "club", "association", "restaurant", "store", "magasin",
    "hospital", "hopital", "centre", "center", "lab", "laboratory", "laboratoire",
    "cafe", "cegep", "ville", "city", "government", "gouvernement", "ministere",
    "hotel", "clinic", "clinique", "society", "societe", "organization", "organisme",
]


def is_all_capsish(text: str) -> bool:
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", text)
    if len(letters) < 2:
        return False
    return letters == letters.upper() and letters != letters.lower()


def classify_side(text: str):
    normalized = normalize_for_match(text)
    title_score = sum(contains_phrase(normalized, kw) for kw in TITLE_KEYWORDS)
    org_score = sum(contains_phrase(normalized, kw) for kw in ORG_KEYWORDS)
    if is_all_capsish(text):
        org_score += 1
    return title_score, org_score


def determine_order(left: str, right: str):
    lt, lo = classify_side(left)
    rt, ro = classify_side(right)
    if lt > lo and ro >= rt:
        return left, right
    if rt > ro and lo >= lt:
        return right, left
    return left, right


def split_title_org_text(text: str):
    line = text.strip()
    if not line:
        return None, None

    m = re.search(r"\s+(?:at|chez)\s+", line, re.I)
    if m and m.start() > 0:
        title, org = line[:m.start()].strip(), line[m.end():].strip()
        if len(title) >= 2 and len(org) >= 2:
            return title, org

    for sep in (" | ", " – ", " — ", " - "):
        idx = line.find(sep)
        if idx > 0:
            left, right = line[:idx].strip(), line[idx + len(sep):].strip()
            if len(left) >= 2 and len(right) >= 2:
                return determine_order(left, right)

    idx = line.find(", ")
    if idx > 0:
        left, right = line[:idx].strip(), line[idx + 2:].strip()
        if (len(left) >= 2 and len(right) >= 2
                and not pure_location_line(right) and not pure_location_line(left)):
            lt, lo = classify_side(left)
            rt, ro = classify_side(right)
            # AJOUT : un signal d'organisation suffit aussi (« Guide, Musée X Inc. »)
            if lt > 0 or rt > 0 or lo > 0 or ro > 0:
                return determine_order(left, right)

    return line, None


def parse_header(header_lines):
    clean = [strip_bullet(l).strip() for l in header_lines]
    clean = [l for l in clean if l]
    location = None
    remaining = []
    for l in clean:
        loc = pure_location_line(l)
        if loc:
            location = location or loc
            continue
        remaining.append(l)
    if not remaining:
        return None, None, location

    stripped = [strip_location_from_line(l) for l in remaining]
    for _, loc in stripped:
        if loc and not location:
            location = loc
    texts = [t for t, _ in stripped if t]
    if not texts:
        return None, None, location

    title, org = split_title_org_text(texts[0])
    if title and org:
        return title, org, location
    if len(texts) == 1:
        return texts[0], None, location
    title, org = determine_order(texts[0], texts[1])
    return title, org, location


def infer_employment_type(title: str, description: str):
    n = normalize_for_match(f"{title} {description}")
    if contains_any(n, ["intern", "internship", "stage", "stagiaire"]):
        return "internship"
    if contains_any(n, ["co op", "coop", "alternance"]):
        return "co-op"
    if contains_any(n, ["volunteer", "benevole"]):
        return "volunteer"
    if contains_any(n, ["part time", "temps partiel"]):
        return "part-time"
    if contains_any(n, ["full time", "temps plein"]):
        return "full-time"
    return None


# ---------------------------------------------------------------------------
# Découpage en blocs : ligne vide, ou ligne normale après des puces
# ---------------------------------------------------------------------------
_CONTINUES = re.compile(
    r"(?:[,;&/+\-]|\b(?:and|et|with|avec|de|des|du|la|le|les|of|the|for|pour|in|en|to|a|à|using|via))$",
    re.I)


def split_blocks(lines):
    blocks, cur, prev_bullet = [], [], False
    for raw in lines:
        line = raw.strip()
        if not line:
            if cur:
                blocks.append(cur)
            cur, prev_bullet = [], False
            continue
        bullet = is_bullet(line)
        # AJOUT : une puce trop longue continue sur la ligne suivante
        if (not bullet and prev_bullet and cur
                and (line[:1].islower() or _CONTINUES.search(cur[-1]))):
            cur[-1] += " " + line
            continue
        if not bullet and prev_bullet and cur:
            blocks.append(cur)
            cur = []
        cur.append(line)
        prev_bullet = bullet
    if cur:
        blocks.append(cur)
    return blocks


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Expériences
# ---------------------------------------------------------------------------
def _attach_orphan_bullets(blocks):
    """AJOUT : un bloc qui ne contient que des puces (séparé par une ligne vide)
    appartient à l'expérience précédente."""
    merged = []
    for block in blocks:
        if merged and all(is_bullet(l) for l in block):
            merged[-1].extend(block)
        else:
            merged.append(block)
    return merged


def parse_experiences(lines, warnings=None):
    out = []
    for block in _attach_orphan_bullets(split_blocks(lines)):
        header_lines, body_lines, seen_bullet = [], [], False
        for raw in block:
            bullet = is_bullet(raw)
            if bullet:
                seen_bullet = True
            if not bullet and not seen_bullet:
                header_lines.append(raw)
            else:
                body_lines.append(strip_bullet(raw) if bullet else raw.strip())
        if not header_lines and body_lines:
            header_lines.append(body_lines.pop(0))

        date_range, cleaned, consumed = None, [], False
        for line in header_lines:
            if not consumed:
                found = detect_date_range(line)
                if found:
                    date_range, consumed = found, True
                    if found["remainder"]:
                        cleaned.append(found["remainder"])
                    continue
            cleaned.append(line)

        title, organization, location = parse_header(cleaned)
        # Tail'ed refuse une expérience sans organisation (PATCH /profile/update).
        if not title or not organization:
            if warnings is not None:
                warnings.append(f"Expérience ignorée (titre ou organisation manquant) : {' / '.join(block[:2])}")
            continue

        description = "\n".join(l.strip() for l in body_lines if l.strip())
        obj = {"id": new_id(), "title": title, "organization": organization,
               "source": "resume-parse"}
        if location:
            obj["location"] = location

        if date_range:
            start = to_year_month(date_range["start"])
            end = None if date_range["current"] else to_year_month(date_range["end"])
            if start:
                obj["startDate"] = start
            if date_range["current"]:
                obj["current"] = True
                obj["endDate"] = None
            else:
                obj["endDate"] = end  # None si pas de mois exploitable
            lossy_start = date_range["start"] and not start
            lossy_end = (not date_range["current"]) and date_range["end"] and not end
            parts = []
            if lossy_start or lossy_end:
                parts.append(f"({date_range['label']})")
            if description:
                parts.append(description)
            final = "\n".join(parts).strip()
            if final:
                obj["description"] = final
        elif description:
            obj["description"] = description

        emp = infer_employment_type(title, description)
        if emp:
            obj["employmentType"] = emp
        out.append(obj)
    return out


# ---------------------------------------------------------------------------
# Formation
# ---------------------------------------------------------------------------
DEGREE_RE = re.compile(
    r"\b(bachelor(?:'s)?|master(?:'s)?|b\.?\s?sc|b\.?\s?a|m\.?\s?sc|m\.?\s?a|b\.?eng|m\.?eng"
    r"|mba|ph\.?\s?d|doctorate|doctorat|diploma|dipl[oô]me|certificate|certificat|associate"
    r"|d\.?e\.?c|baccalaur[ée]at|licence|ma[iî]trise)\b", re.I)
SCHOOL_RE = re.compile(
    r"\b(university|universit[ée]|college|coll[èe]ge|institute|institut|school|[ée]cole"
    r"|polytechnic|polytechnique|academy|cegep|c[ée]gep)\b", re.I)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
FIELD_OF_STUDY_RE = re.compile(r"\b(?:in|en)\s+([A-Za-zÀ-ÿ&,'\- ]{3,60})$", re.I)


def _split_one_line_education(line: str):
    """AJOUT : « Programme — École | 2022 – 2025 » sur une seule ligne."""
    text = YEAR_RE.sub("", line)
    text = re.sub(r"\b(en cours|present|présent|current)\b", "", text, flags=re.I)
    text = re.sub(r"[|–—\-\s]+$", "", text).strip()
    parts = [p.strip() for p in re.split(r"\s+[—–|]\s+|\s+-\s+|,\s+", text) if p.strip()]
    if len(parts) < 2:
        return None
    school = next((p for p in parts if SCHOOL_RE.search(p)), None)
    program = next((p for p in parts if p != school and DEGREE_RE.search(p)), None)
    if school and not program:
        program = next((p for p in parts if p != school), None)
    if program and not school:
        school = next((p for p in parts if p != program), None)
    if school and program:
        return school, program
    return None


def _strip_dates(line: str) -> str:
    """AJOUT : « McGill University      2022 – 2026 » -> « McGill University »."""
    for rx in (RANGE_RE, SINCE_RE):
        m = rx.search(line)
        if m:
            line = clean_remainder(line, m.start(), len(m.group(0)))
    return re.sub(r"\s{2,}", " ", line).strip(" |,–—-")


def parse_education(lines):
    out = []
    for block in split_blocks(lines):
        clean = [_strip_dates(strip_bullet(l)) for l in block]  # AJOUT : retirer les dates
        clean = [l for l in clean if l]
        if not clean:
            continue
        school_line = next((l for l in clean if SCHOOL_RE.search(l)), None)
        degree_line = next((l for l in clean if DEGREE_RE.search(l)), None)
        if not school_line and not degree_line:
            continue

        school = school_line or next((l for l in clean if l != degree_line), "")
        program = degree_line or next((l for l in clean if l != school_line), "")

        if school == program:  # AJOUT : les deux sur la même ligne
            split = _split_one_line_education(school)
            if split:
                school, program = split

        field = None
        m = FIELD_OF_STUDY_RE.search(program)
        if m:
            field = m.group(1).strip()

        years = [int(y) for l in block for y in YEAR_RE.findall(l)]
        obj = {"id": new_id(), "school": school, "program": program,
               "source": "resume-parse"}
        if field:
            obj["fieldOfStudy"] = field
        # AJOUT : formation en cours (« Sept. 2022 – Aujourd’hui », « présent »…)
        current = bool(PRESENT_RE.search(" ".join(block)))
        if current:
            obj["current"] = True
        if years:
            if current and len(set(years)) == 1:
                # Une seule année + « en cours » : c'est l'année de début, pas de fin
                obj["startYear"] = str(years[0])
            else:
                obj["graduationYear"] = str(max(years))
                if len(set(years)) >= 2:
                    obj["startYear"] = str(min(years))  # AJOUT (champ prévu par Education)
        out.append(obj)
    return out


# ---------------------------------------------------------------------------
# Projets
# ---------------------------------------------------------------------------
URL_RE = re.compile(r"https?://[^\s)]+", re.I)


ACTION_WORDS = {
    # FR
    "developpement", "conception", "construction", "creation", "realisation",
    "implementation", "mise", "integration", "deploiement", "tests", "test",
    "analyse", "utilisation", "ajout", "gestion", "optimisation", "automatisation",
    "redaction", "entrainement", "collecte", "nettoyage", "modelisation",
    "developpe", "concu", "cree", "realise", "implemente", "deploye",
    # EN
    "built", "developed", "designed", "created", "implemented", "deployed",
    "wrote", "used", "integrated", "trained", "added", "tested", "achieved",
    "development", "design", "implementation", "deployment", "testing",
}


def _looks_like_description(line: str) -> bool:
    """AJOUT : une ligne de description plutôt qu'un titre de projet."""
    text = strip_bullet(line)
    if not text:
        return False
    words = text.split()
    first = normalize_for_match(words[0]).strip()
    if text[0].isdigit() or text[0].islower():
        return True
    if text.rstrip().endswith((".", ";")) or len(words) > 12:
        return True
    if first in ACTION_WORDS:
        return True
    # Proportion de mots (> 3 lettres) commençant par une majuscule
    long_words = [re.sub(r"^[^A-Za-zÀ-ÿ]*(?:[dlDL]['’])?", "", w) for w in words]
    long_words = [w for w in long_words if len(w) > 3]
    if long_words:
        caps = sum(w[0].isupper() for w in long_words)
        if caps / len(long_words) < 0.5:
            return True
    return False


def _merge_project_descriptions(blocks):
    """AJOUT : rattacher au projet précédent les blocs qui commencent par une
    ligne de description (« Conception et développement… », « 8 endpoints… »)."""
    merged = []
    for block in blocks:
        if merged and _looks_like_description(block[0]):
            merged[-1].extend(block)
        else:
            merged.append(block)
    return merged


def parse_projects(lines):
    out = []
    for block in _merge_project_descriptions(split_blocks(lines)):
        clean = [l.strip() for l in block if l.strip()]
        if not clean:
            continue
        first = strip_bullet(clean[0])
        name, inline = first, ""
        m = re.match(r"^(.{2,}?)\s*[-–—:|]\s+(.+)$", first)
        if m:
            name, inline = m.group(1).strip(), m.group(2).strip()
        name = re.sub(r"[\s:–—-]+$", "", name).strip()
        if not name:
            continue
        # AJOUT : « Projet X | 2025 » -> l'année n'est pas une description
        if inline and detect_date_range(inline) and not detect_date_range(inline)["remainder"]:
            inline = ""

        url = URL_RE.search(" ".join(block))
        rest = [strip_bullet(l).strip() for l in clean[1:]]
        parts = [p for p in [inline, *rest] if p]
        obj = {"id": new_id(), "name": name, "source": "resume-parse"}
        if url:
            obj["url"] = re.sub(r"[.,;)]+$", "", url.group(0))
        description = "\n".join(
            p for p in parts if not URL_RE.search(p) or URL_RE.sub("", p).strip()
        ).strip()
        if description:
            obj["description"] = description
        out.append(obj)
    return out


# ---------------------------------------------------------------------------
# Compétences (avec catégorie ; le niveau n'est jamais deviné)
# ---------------------------------------------------------------------------
LANGUAGE_SKILLS = [
    "python", "java", "javascript", "js", "typescript", "ts", "c", "c++", "c#",
    "csharp", "ruby", "go", "golang", "rust", "swift", "kotlin", "php", "r",
    "matlab", "scala", "perl", "dart", "sql", "html", "html5", "css", "css3",
    "bash", "shell", "powershell", "objective-c", "objectivec", "julia",
    "haskell", "lua", "assembly", "vba", "elixir", "clojure", "groovy",
    "fortran", "cobol", "sas", "sass", "scss",
]
FRAMEWORK_SKILLS = [
    "react", "react.js", "reactjs", "angular", "angularjs", "vue", "vue.js",
    "vuejs", "next.js", "nextjs", "nuxt", "node.js", "nodejs", "express",
    "expressjs", "django", "flask", "fastapi", "spring", "spring boot",
    "springboot", "rails", "ruby on rails", ".net", "dotnet", "asp.net",
    "aspnet", "laravel", "symfony", "tensorflow", "pytorch", "keras",
    "scikit-learn", "scikitlearn", "pandas", "numpy", "jquery", "bootstrap",
    "tailwind", "tailwindcss", "redux", "graphql", "svelte", "sveltekit",
    "ember", "flutter", "react native", "reactnative", "unity",
    "unreal engine", "unrealengine", "xamarin", "ionic", "electron",
]
TOOL_SKILLS = [
    "git", "github", "gitlab", "bitbucket", "docker", "kubernetes", "k8s",
    "aws", "azure", "gcp", "google cloud", "figma", "jira", "confluence",
    "sketch", "adobe xd", "adobexd", "photoshop", "illustrator", "excel",
    "microsoft excel", "powerpoint", "word", "tableau", "power bi", "powerbi",
    "postman", "webpack", "vite", "npm", "yarn", "linux", "unix", "windows",
    "macos", "vs code", "vscode", "visual studio", "intellij", "eclipse",
    "terraform", "jenkins", "circleci", "github actions", "githubactions",
    "salesforce", "sap", "notion", "slack", "trello", "asana", "firebase",
    "mongodb", "postgresql", "postgres", "mysql", "sqlite", "redis", "oracle",
    "splunk", "kafka", "rabbitmq", "nginx", "apache", "heroku", "vercel",
    "netlify",
]
SOFT_SKILLS = [
    "communication", "leadership", "teamwork", "team work", "collaboration",
    "problem solving", "problem-solving", "critical thinking",
    "time management", "adaptability", "creativity", "public speaking",
    "presentation", "negotiation", "conflict resolution", "mentoring",
    "coaching", "project management", "organization", "organizational",
    "attention to detail", "work ethic", "interpersonal", "multitasking",
    "decision making", "decision-making", "flexibility", "empathy",
    "customer service",
]


def canonical_skill_key(s: str) -> str:
    return re.sub(r"[\s._-]+", "", strip_accents(s).lower())


SKILL_CATEGORY_MAP = {}
for _names, _cat in ((LANGUAGE_SKILLS, "language"), (FRAMEWORK_SKILLS, "framework"),
                     (TOOL_SKILLS, "tool"), (SOFT_SKILLS, "soft")):
    for _n in _names:
        SKILL_CATEGORY_MAP[canonical_skill_key(_n)] = _cat

LABEL_CATEGORY_HINTS = [
    (["soft skill", "interpersonal"], "soft"),
    (["programming language", "language", "langue"], "language"),
    (["framework", "librar"], "framework"),
    (["tool", "technolog", "platform", "outil"], "tool"),
]


def infer_label_category(label: str):
    n = normalize_for_match(label)
    for keywords, cat in LABEL_CATEGORY_HINTS:
        if any(kw in n for kw in keywords):
            return cat
    return None


# AJOUT : extraction des compétences connues dans une phrase
# (« Proficient in Python and Java » -> Python, Java)
_AMBIGUOUS = {"c", "r", "go", "ts", "js", "word", "spring", "express", "rails",
              "electron", "ember", "notion", "slack", "sketch", "shell", "assembly",
              "presentation", "organization", "organizational", "windows", "unity",
              "apache", "oracle", "sap", "vite", "dart", "lua", "ionic", "co op",
              "flexibility", "creativity", "empathy", "coaching", "mentoring"}
_LEXICON = []
for _names in (LANGUAGE_SKILLS, FRAMEWORK_SKILLS, TOOL_SKILLS, SOFT_SKILLS):
    for _n in _names:
        if _n not in _AMBIGUOUS:
            _LEXICON.append(re.compile(
                r"(?<![A-Za-z0-9+#.])" + re.escape(_n).replace(r"\ ", r"[\s-]?") + r"(?![A-Za-z0-9+#])",
                re.I))
_FILLER = {"in", "with", "of", "and", "using", "experience", "experienced", "proficient",
           "proficiency", "knowledge", "familiar", "strong", "skilled", "excellent",
           "ability", "able", "de", "en", "avec", "maitrise", "connaissance", "bonne",
           "solid", "working", "the", "to", "for", "pour", "des", "et"}


def _skills_in_sentence(text):
    found = []
    for rx in _LEXICON:
        m = rx.search(text)
        if m:
            found.append(m.group(0))
    return found


def parse_skills(lines):
    seen, out = set(), []

    def add(name, label_hint):
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        entry = {"name": name}
        cat = SKILL_CATEGORY_MAP.get(canonical_skill_key(name)) or label_hint
        if cat:
            entry["category"] = cat
        out.append(entry)

    for raw in lines:
        line = strip_bullet(raw)
        label_hint = None
        colon = line.find(":")
        if 0 < colon <= 30 and "," not in line[:colon]:
            label_hint = infer_label_category(line[:colon])
            line = line[colon + 1:]
        for token in re.split(r"[,|/•·;\t]|\s{2,}", line):
            v = re.sub(r"^[\s\-–—*•·]+", "", token).strip().rstrip(".")
            if len(v) < 2:
                continue
            words = normalize_for_match(v).split()
            if canonical_skill_key(v) in SKILL_CATEGORY_MAP:
                add(v, label_hint)
            elif len(v) <= 40 and len(words) <= 4 and not (set(words) & _FILLER):
                add(v, label_hint)
            else:  # AJOUT : phrase -> seulement les compétences reconnues
                for name in _skills_in_sentence(v):
                    add(name, label_hint)
    return out


# ---------------------------------------------------------------------------
# Texte -> données structurées (fonction pure, testable sans PDF)
# ---------------------------------------------------------------------------
def _volunteer(experiences):
    for e in experiences:
        e.setdefault("employmentType", "volunteer")
    return experiences


def parse_resume_text(text: str, with_meta: bool = False):
    try:
        lines = re.sub(r"\r\n?", "\n", text or "").split("\n")
        lines = [re.sub(r"[ \t]+$", "", l).lstrip() for l in lines]
        sections, headings, unknown = segment_sections(lines)
        warnings = []
        result = {
            "experiences": parse_experiences(sections["experience"], warnings)
                           + _volunteer(parse_experiences(sections["volunteer"], warnings)),
            "education": parse_education(sections["education"]),
            "projects": parse_projects(sections["projects"]),
            "skillsStructured": parse_skills(sections["skills"]),
        }
        if len(result["skillsStructured"]) > 60:  # AJOUT : signal d'un titre manqué
            warnings.append(f"{len(result['skillsStructured'])} compétences : une autre section "
                            "a probablement été absorbée (titre non reconnu ?)")
    except Exception as exc:  # ne jamais planter sur un CV bizarre
        result = {"experiences": [], "education": [], "projects": [], "skillsStructured": []}
        headings, unknown, warnings = [], [], [f"Erreur de parsing : {exc}"]
    if with_meta:
        return result, {"headings": headings, "unknown": unknown, "warnings": warnings}
    return result


# ---------------------------------------------------------------------------
# AJOUT — Extraction PDF : deux modes, on garde le meilleur
# ---------------------------------------------------------------------------
def extract_text_standard(doc) -> str:
    return "\n".join(page.get_text(sort=True) for page in doc)


def extract_text_layout(doc) -> str:
    """Reconstruit les lignes visuelles (titre + date alignée à droite sur la
    même ligne) et insère une ligne vide quand l'espace vertical est grand,
    ce qui aide le découpage en blocs."""
    out = []
    for page in doc:
        rows = []
        for block in page.get_text("dict", sort=True)["blocks"]:
            for line in block.get("lines", []):
                txt = "".join(span["text"] for span in line["spans"]).strip()
                if txt:
                    x0, y0, x1, y1 = line["bbox"]
                    rows.append([y0, y1, x0, txt])
        rows.sort(key=lambda r: (round(r[0]), r[2]))
        merged = []
        for y0, y1, x0, txt in rows:
            if merged and abs(merged[-1][0] - y0) < 3:
                merged[-1][3] += "  " + txt
                merged[-1][1] = max(merged[-1][1], y1)
            else:
                merged.append([y0, y1, x0, txt])
        prev = None
        for y0, y1, _, txt in merged:
            if prev is not None:
                height = max(prev[1] - prev[0], 1)
                if y0 - prev[1] > 0.8 * height:
                    out.append("")
            out.append(txt)
            prev = (y0, y1)
        out.append("")
    return "\n".join(out)


def _score(result) -> float:
    return (3 * len(result["experiences"]) + 3 * len(result["education"])
            + 2 * len(result["projects"]) + 0.5 * min(len(result["skillsStructured"]), 20))


def quality_status(result, text: str):
    """ok : formation ou expérience + compétences ; partiel : quelque chose ;
    echec : rien d'exploitable -> pas de recommandations (consigne de Franck)."""
    if not text.strip():
        return "echec", "Aucun texte extractible (PDF scanné/image ?)"
    has_core = bool(result["education"] or result["experiences"])
    has_skills = bool(result["skillsStructured"])
    if has_core and has_skills:
        return "ok", ""
    if has_core or has_skills or result["projects"]:
        missing = [n for n, v in (("formation/expérience", has_core), ("compétences", has_skills)) if not v]
        return "partiel", "Manquant : " + ", ".join(missing)
    return "echec", "Aucune section reconnue (CV non standard)"


def parse_resume_pdf(pdf_path, name: str | None = None) -> dict:
    """pdf_path : chemin d'un PDF, ou directement son contenu (bytes).
    AJOUT : avec des bytes, le CV est lu en mémoire, sans jamais être écrit sur le disque."""
    import pymupdf  # importé ici pour que parse_resume_text reste testable sans PyMuPDF

    if isinstance(pdf_path, (bytes, bytearray)):
        opened = pymupdf.open(stream=bytes(pdf_path), filetype="pdf")
        name = name or "cv_en_memoire.pdf"
    else:
        opened = pymupdf.open(pdf_path)
        name = name or Path(pdf_path).name

    with opened as doc:
        candidates = []
        for mode, fn in (("standard", extract_text_standard), ("layout", extract_text_layout)):
            text = fn(doc)
            result, meta = parse_resume_text(text, with_meta=True)
            candidates.append((_score(result), mode, text, result, meta))
    score, mode, text, result, meta = max(candidates, key=lambda c: c[0])
    status, reason = quality_status(result, text)
    result["_meta"] = {
        "file": name,
        "status": status,
        "recommendable": status == "ok",
        "reason": reason,
        "extractionMode": mode,
        "sectionsDetected": meta["headings"],
        "unrecognizedHeadings": meta["unknown"],
        "counts": {k: len(result[k]) for k in ("experiences", "education", "projects", "skillsStructured")},
        "warnings": meta["warnings"],
    }
    return result


# ---------------------------------------------------------------------------
# Programme principal (un fichier ou un dossier)
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="CV PDF -> données structurées Tail'ed")
    ap.add_argument("source", help="Un fichier PDF ou un dossier de PDF")
    ap.add_argument("--out", default="profils_structures", help="Dossier de sortie")
    ap.add_argument("--show-text", "--debug", dest="show_text", action="store_true",
                    help="Afficher les sections reconnues (un seul CV)")
    args = ap.parse_args()

    src = Path(args.source)
    pdfs = sorted(src.rglob("*.pdf")) if src.is_dir() else [src]
    if not pdfs or not pdfs[0].is_file():
        sys.exit(f"Aucun PDF trouvé : {src}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []

    for pdf in pdfs:
        try:
            profile = parse_resume_pdf(pdf)
        except Exception as exc:
            print(f"[ERREUR] {pdf.name} : {exc}")
            report.append({"file": pdf.name, "status": "echec", "reason": str(exc)})
            continue
        meta = profile["_meta"]
        out_path = out_dir / f"{pdf.stem}.json"
        out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        c = meta["counts"]
        print(f"[{meta['status'].upper():7}] {pdf.name:35} exp={c['experiences']} "
              f"form={c['education']} proj={c['projects']} comp={c['skillsStructured']} "
              f"({meta['extractionMode']}) {meta['reason']}")
        for w in meta["warnings"]:
            print(f"          ! {w}")
        if meta["unrecognizedHeadings"]:
            print(f"          ? Titres non reconnus : {meta['unrecognizedHeadings']}")
        if args.show_text and len(pdfs) == 1:
            print("          Sections reconnues :", meta["sectionsDetected"])
        report.append({k: meta[k] for k in ("file", "status", "recommendable", "reason", "counts")})

    (out_dir / "_rapport.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(r["status"] == "ok" for r in report)
    print(f"\n{ok}/{len(report)} CV exploitables pour les recommandations. "
          f"Résultats dans : {out_dir.resolve()}")


if __name__ == "__main__":
    main()
