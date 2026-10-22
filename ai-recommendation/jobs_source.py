"""
jobs_source.py — Charger les offres d'emploi de Tail'ed.

Source : la liste communautaire
    https://github.com/tailed-community/tailed-internships-new-grad
    fichier data/jobs.json, mis à jour automatiquement chaque jour.
C'est la même URL que celle utilisée par le dashboard
(functions/src/lib/jobs-feed.ts et src/lib/external-jobs.ts).

Le fichier (~15 Mo, ~28 000 offres) est téléchargé une fois puis gardé en
cache local (data/cache/jobs.json) pendant 24 h. Si le téléchargement échoue,
on réutilise le cache, même expiré, plutôt que de planter.

Utilisation :
    from jobs_source import load_jobs
    jobs = load_jobs()                                  # toutes les offres actives
    jobs = load_jobs(job_type="internship", canada_only=True)

    python jobs_source.py                               # statistiques rapides
    python jobs_source.py --refresh                     # forcer le téléchargement
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from collections import Counter
from pathlib import Path

JOBS_URL = (
    "https://raw.githubusercontent.com/tailed-community/"
    "tailed-internships-new-grad/refs/heads/main/data/jobs.json"
)
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data" / "cache"
CACHE_FILE = CACHE_DIR / "jobs.json"
SAMPLE_FILE = BASE_DIR / "data" / "sample_jobs_100.json"
MAX_AGE_HOURS = 24


# ---------------------------------------------------------------------------
# Téléchargement + cache
# ---------------------------------------------------------------------------
def _cache_age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600


def _download(url: str, dest: Path, timeout: int = 60) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "tailed-ai-recommendation"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        out.write(resp.read())
    json.loads(tmp.read_text(encoding="utf-8"))  # vérifier que c'est du JSON valide
    tmp.replace(dest)


def fetch_jobs_file(refresh: bool = False, max_age_hours: float = MAX_AGE_HOURS) -> Path:
    """Retourne le chemin d'un jobs.json à jour (télécharge si besoin)."""
    fresh = CACHE_FILE.exists() and _cache_age_hours(CACHE_FILE) < max_age_hours
    if fresh and not refresh:
        return CACHE_FILE
    try:
        print("Téléchargement des offres Tail'ed...")
        _download(JOBS_URL, CACHE_FILE)
        print(f"Offres enregistrées dans {CACHE_FILE}")
    except Exception as exc:
        if CACHE_FILE.exists():
            print(f"Téléchargement impossible ({exc}). Utilisation du cache existant.")
        else:
            raise RuntimeError(f"Impossible de télécharger les offres : {exc}") from exc
    return CACHE_FILE


# ---------------------------------------------------------------------------
# Filtre Canada
# ---------------------------------------------------------------------------
# ATTENTION : dans jobs.json, l'abréviation « CA » (Californie) est parfois
# convertie en « Canada » (scripts/core/normalize.py du dépôt des offres).
# « San Diego, Canada » est donc une offre en Californie. On ne se fie pas au
# mot « Canada » seul : il faut une province, une ville canadienne connue ou
# un format sans ambiguïté.
_PROVINCES = r"QC|ON|BC|AB|MB|NS|NB|SK|NL|PE|YT|NT|NU"
_CITIES = (
    r"montr[ée]al|toronto|vancouver|ottawa|calgary|edmonton|waterloo|mississauga|"
    r"burnaby|qu[ée]bec|laval|winnipeg|halifax|victoria|kitchener|markham|gatineau|"
    r"sherbrooke|saskatoon|regina|hamilton|oakville|brampton|richmond hill|"
    r"saint-laurent|dorval|longueuil|l[ée]vis|trois-rivi[èe]res|kanata|guelph|"
    r"fredericton|moncton|st\.? john'?s|charlottetown|surrey|kelowna|windsor"
)
CANADA_RE = re.compile(
    rf"(?:,\s*(?:{_PROVINCES})\b)"           # « Toronto, ON »
    rf"|(?:^\s*(?:{_PROVINCES}),\s*Canada)"  # « QC, Canada »
    rf"|\b(?:{_CITIES})\b"                    # ville canadienne connue
    rf"|^Ca\.(?:qc|on|bc|ab|mb|ns|nb|sk|nl|pe)\."  # « Ca.qc.montréal... »
    rf"|^\s*Canada\s*(?:$|[-(/]|\s{{2}})"     # « Canada », « Canada  Ontario »
    rf"|remote[^/]*canada|canada[^/]*remote",
    re.I,
)


def is_canada(location: str) -> bool:
    return bool(CANADA_RE.search(location or ""))


# ---------------------------------------------------------------------------
# Chargement + filtres
# ---------------------------------------------------------------------------
def load_jobs(
    source: str = "live",
    job_type: str | None = None,
    canada_only: bool = False,
    refresh: bool = False,
) -> list[dict]:
    """Charger les offres.

    source      : "live" (vrai jobs.json, avec cache) ou "sample" (100 offres de test)
                  ou un chemin vers un fichier JSON.
    job_type    : "internship", "new_grad" ou None (tous).
    canada_only : garder seulement les offres au Canada (voir is_canada).
    """
    if source == "live":
        path = fetch_jobs_file(refresh=refresh)
    elif source == "sample":
        path = SAMPLE_FILE
    else:
        path = Path(source)

    jobs = json.loads(path.read_text(encoding="utf-8-sig"))

    seen, out = set(), []
    for job in jobs:
        title = (job.get("title") or "").strip()
        if not title or job.get("active") is False:
            continue
        if job_type and job.get("type") != job_type:
            continue
        if canada_only and not is_canada(job.get("location", "")):
            continue
        key = job.get("id") or (job.get("company"), title, job.get("location"))
        if key in seen:  # doublons éventuels
            continue
        seen.add(key)
        out.append(job)
    return out


def main():
    ap = argparse.ArgumentParser(description="Charger et résumer les offres Tail'ed")
    ap.add_argument("--source", default="live", help="live, sample ou chemin d'un JSON")
    ap.add_argument("--refresh", action="store_true", help="Forcer le téléchargement")
    args = ap.parse_args()

    jobs = load_jobs(args.source, refresh=args.refresh)
    canada = [j for j in jobs if is_canada(j.get("location", ""))]
    print(f"\nOffres actives : {len(jobs)}")
    print("Par type      :", dict(Counter(j.get("type") for j in jobs)))
    print(f"Au Canada     : {len(canada)}")
    print("Exemples au Canada :")
    for j in canada[:5]:
        print(f"  - {j['title']} | {j['company']} | {j['location']}")


if __name__ == "__main__":
    main()
