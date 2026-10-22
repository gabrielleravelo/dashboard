"""
evaluate_parser.py — Mesurer la qualité du parseur de CV.

Compare, pour chaque étudiant, les données extraites automatiquement du CV
(profils_structures/{uid}.json, produit par cv_to_structured.py) avec les
données déjà enregistrées dans Firestore (profils_firestore/{uid}.json,
produit par fetch_cvs.py).

Utilisation :
    python evaluate_parser.py
    python evaluate_parser.py --parsed profils_structures --truth ../profils_firestore
"""

import argparse
import json
from pathlib import Path

from cv_to_structured import canonical_skill_key, normalize_for_match


def skill_set(profile):
    return {canonical_skill_key(s["name"]) for s in profile.get("skillsStructured") or [] if s.get("name")}


def org_set(profile):
    return {normalize_for_match(e.get("organization", "")).strip()
            for e in profile.get("experiences") or [] if e.get("organization")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default="profils_structures")
    ap.add_argument("--truth", default="../profils_firestore")
    args = ap.parse_args()

    parsed_dir, truth_dir = Path(args.parsed), Path(args.truth)
    rows = []
    for truth_file in sorted(truth_dir.glob("*.json")):
        parsed_file = parsed_dir / truth_file.name
        if not parsed_file.exists():
            continue
        truth = json.loads(truth_file.read_text(encoding="utf-8"))
        parsed = json.loads(parsed_file.read_text(encoding="utf-8"))

        ts, ps = skill_set(truth), skill_set(parsed)
        to, po = org_set(truth), org_set(parsed)
        rows.append({
            "uid": truth_file.stem,
            "status": parsed.get("_meta", {}).get("status"),
            "skills_rappel": len(ts & ps) / len(ts) if ts else None,
            "orgs_rappel": len(to & po) / len(to) if to else None,
            "exp_vrai/parse": f"{len(truth.get('experiences') or [])}/{len(parsed['experiences'])}",
            "form_vrai/parse": f"{len(truth.get('education') or [])}/{len(parsed['education'])}",
        })

    if not rows:
        print("Aucun profil en commun. Lance d'abord fetch_cvs.py puis cv_to_structured.py.")
        return

    for r in rows:
        sk = "-" if r["skills_rappel"] is None else f"{r['skills_rappel']:.0%}"
        og = "-" if r["orgs_rappel"] is None else f"{r['orgs_rappel']:.0%}"
        print(f"{r['uid'][:12]:12}  {r['status'] or '?':8} compétences={sk:5} "
              f"organisations={og:5} exp={r['exp_vrai/parse']} form={r['form_vrai/parse']}")

    def mean(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return f"{sum(vals) / len(vals):.0%} (sur {len(vals)} profils)" if vals else "n/a"

    print(f"\nProfils comparés : {len(rows)}")
    print(f"Rappel moyen des compétences   : {mean('skills_rappel')}")
    print(f"Rappel moyen des organisations : {mean('orgs_rappel')}")


if __name__ == "__main__":
    main()
