"""
fetch_cvs.py — Récupérer les CV de la base de données de DEV de Tail'ed.

Où sont les CV (d'après dashboard/functions/src/routes/profile.ts) :
    Firestore : profiles/{uid}  -> champ resume.id  (+ experiences, education,
                                    projects, skillsStructured si déjà remplis)
    Storage   : profiles/{uid}/resumes/main_resume/{resume.id}.pdf
    Bucket    : tailed-community-dev.firebasestorage.app

Ce que fait le script :
    1. Télécharge chaque CV PDF dans  cv_tests_prives/{uid}.pdf
    2. Sauvegarde les données structurées DÉJÀ présentes dans Firestore dans
       profils_firestore/{uid}.json  -> utile comme « vérité terrain » pour
       mesurer la qualité du parseur, et comme entrée directe du moteur.

Authentification (UNE des deux) :
    A) Clé de compte de service (demander à Franck, ne JAMAIS la committer) :
         python fetch_cvs.py --key chemin/cle-service.json
    B) Google Cloud CLI installé + compte autorisé sur le projet :
         gcloud auth application-default login
         python fetch_cvs.py

Options :
    --limit 20            ne récupérer que 20 profils
    --storage-only        lister directement le bucket (sans lire Firestore)
    --parse-direct        lire les CV EN MÉMOIRE et ne garder que le JSON structuré
                          (aucun PDF écrit sur le disque)
    --parse-direct --no-save   ne rien enregistrer du tout : statistiques seulement
    --profiles-only       lire TOUS les profils Firestore sans télécharger de CV
                          (champs utiles au matching seulement, sans nom/courriel)

Installation :  pip install firebase-admin
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore, storage

PROJECT_ID = "tailed-community-dev"
BUCKET = "tailed-community-dev.firebasestorage.app"
STRUCTURED_FIELDS = ["experiences", "education", "projects", "skillsStructured", "skills"]


def init_firebase(key_path):
    cred = credentials.Certificate(key_path) if key_path else credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": PROJECT_ID, "storageBucket": BUCKET})


def to_jsonable(value):
    """Convertit les Timestamps Firestore en texte pour json.dump."""
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def fetch_from_firestore(cv_dir, struct_dir, limit):
    db = firestore.client()
    bucket = storage.bucket()
    stats = {"profils": 0, "cv": 0, "sans_cv": 0, "cv_introuvable": 0, "deja_structures": 0}

    for doc in db.collection("profiles").stream():
        if limit and stats["cv"] >= limit:
            break
        stats["profils"] += 1
        data = doc.to_dict() or {}
        uid = doc.id

        # Données structurées déjà saisies (formulaire ou parse accepté)
        structured = {f: data.get(f) for f in STRUCTURED_FIELDS if data.get(f)}
        if any(structured.get(f) for f in ("experiences", "education", "skillsStructured")):
            stats["deja_structures"] += 1
            (struct_dir / f"{uid}.json").write_text(
                json.dumps(to_jsonable(structured), ensure_ascii=False, indent=2),
                encoding="utf-8")

        resume = data.get("resume") or {}
        if not resume.get("id"):
            stats["sans_cv"] += 1
            continue

        blob = bucket.blob(f"profiles/{uid}/resumes/main_resume/{resume['id']}.pdf")
        if not blob.exists():
            stats["cv_introuvable"] += 1
            continue
        blob.download_to_filename(cv_dir / f"{uid}.pdf")
        stats["cv"] += 1
        print(f"  CV téléchargé : {uid}.pdf")
    return stats


def parse_in_memory(struct_dir, limit, save=True):
    """Télécharger chaque CV EN MÉMOIRE, le passer au parseur, puis l'oublier.
    Aucun PDF n'est écrit sur le disque. Avec save=True, seul le JSON structuré
    (sans nom ni coordonnées) est gardé ; avec save=False, rien n'est gardé :
    on affiche seulement les statistiques du parseur."""
    from cv_to_structured import parse_resume_pdf

    db = firestore.client()
    bucket = storage.bucket()
    stats = {"profils": 0, "cv": 0, "sans_cv": 0, "cv_introuvable": 0,
             "ok": 0, "partiel": 0, "echec": 0}

    for doc in db.collection("profiles").stream():
        if limit and stats["cv"] >= limit:
            break
        stats["profils"] += 1
        uid = doc.id
        resume = (doc.to_dict() or {}).get("resume") or {}
        if not resume.get("id"):
            stats["sans_cv"] += 1
            continue
        blob = bucket.blob(f"profiles/{uid}/resumes/main_resume/{resume['id']}.pdf")
        if not blob.exists():
            stats["cv_introuvable"] += 1
            continue

        pdf_bytes = blob.download_as_bytes()          # en mémoire seulement
        try:
            profile = parse_resume_pdf(pdf_bytes, name=f"{uid}.pdf")
        except Exception as exc:                      # PDF corrompu, etc.
            profile = {"_meta": {"status": "echec", "reason": str(exc), "counts": {}}}
        finally:
            del pdf_bytes                             # le CV n'est plus gardé nulle part

        stats["cv"] += 1
        meta = profile["_meta"]
        stats[meta["status"]] = stats.get(meta["status"], 0) + 1
        c = meta.get("counts", {})
        print(f"  [{meta['status'].upper():7}] {uid[:8]}…  exp={c.get('experiences', 0)} "
              f"form={c.get('education', 0)} proj={c.get('projects', 0)} "
              f"comp={c.get('skillsStructured', 0)}")
        if save:
            (struct_dir / f"{uid}.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    if stats["cv"]:
        print(f"\n{stats['ok']}/{stats['cv']} CV exploitables pour les recommandations.")
    return stats


# Champs utiles au matching. JAMAIS : nom, courriel, téléphone, adresse.
MATCHING_FIELDS = [
    # Champs structurés (spec 08)
    "experiences", "education", "projects", "skillsStructured",
    # Champs simples plus anciens, souvent remplis
    "school", "program", "graduationYear", "skills",
    # Pour l'enrichissement GitHub / Devpost (étape suivante du stage)
    "githubUsername", "github", "devpostUsername",
    # Indique s'il y a un CV, sans l'URL
    "resume",
]


def fetch_profiles_only(struct_dir, limit):
    """Lire les profils Firestore (sans télécharger de CV) et sauvegarder
    uniquement les champs utiles au matching."""
    db = firestore.client()
    stats = {"profils": 0, "sauvegardes": 0}
    counts = {f: 0 for f in MATCHING_FIELDS}

    for doc in db.collection("profiles").stream():
        if limit and stats["profils"] >= limit:
            break
        stats["profils"] += 1
        data = doc.to_dict() or {}

        profile = {}
        for field in MATCHING_FIELDS:
            value = data.get(field)
            if not value:
                continue
            if field == "resume":
                value = {"id": value.get("id")} if isinstance(value, dict) else None
                if not value or not value["id"]:
                    continue
            profile[field] = value
            counts[field] += 1

        if profile:
            (struct_dir / f"{doc.id}.json").write_text(
                json.dumps(to_jsonable(profile), ensure_ascii=False, indent=2),
                encoding="utf-8")
            stats["sauvegardes"] += 1

    print(f"\nProfils lus : {stats['profils']}  |  sauvegardés : {stats['sauvegardes']}")
    print("Nombre de profils où chaque champ est rempli :")
    for field, n in counts.items():
        print(f"  {field:18} {n:5}  ({n / max(stats['profils'], 1):.0%})")
    return stats


def fetch_from_storage(cv_dir, limit):
    """Plan B : parcourir le bucket directement (si Firestore n'est pas accessible)."""
    bucket = storage.bucket()
    count = 0
    for blob in bucket.list_blobs(prefix="profiles/"):
        if "/resumes/main_resume/" not in blob.name or not blob.name.endswith(".pdf"):
            continue
        uid = blob.name.split("/")[1]
        blob.download_to_filename(cv_dir / f"{uid}.pdf")
        count += 1
        print(f"  CV téléchargé : {uid}.pdf")
        if limit and count >= limit:
            break
    return {"cv": count}


def main():
    ap = argparse.ArgumentParser(description="Télécharger les CV de la base de dev Tail'ed")
    ap.add_argument("--key", help="Chemin de la clé JSON du compte de service")
    ap.add_argument("--limit", type=int, default=0, help="Nombre max de CV (0 = tous)")
    ap.add_argument("--storage-only", action="store_true")
    ap.add_argument("--profiles-only", action="store_true",
                    help="Lire seulement les profils Firestore, sans télécharger de CV")
    ap.add_argument("--parse-direct", action="store_true",
                    help="Lire les CV en mémoire sans les enregistrer ; garder seulement le JSON structuré")
    ap.add_argument("--no-save", action="store_true",
                    help="Avec --parse-direct : ne rien enregistrer, afficher seulement les statistiques")
    ap.add_argument("--cv-dir", default="../cv_tests_prives")
    ap.add_argument("--struct-dir", default="../profils_firestore")
    args = ap.parse_args()

    cv_dir, struct_dir = Path(args.cv_dir), Path(args.struct_dir)
    direct = args.parse_direct
    if not (direct and args.no_save):
        struct_dir.mkdir(parents=True, exist_ok=True)
    if not (args.profiles_only or direct):  # aucun dossier de CV si on ne garde pas les PDF
        cv_dir.mkdir(parents=True, exist_ok=True)

    init_firebase(args.key)
    print(f"Connexion au projet {PROJECT_ID}...")
    if direct:
        stats = parse_in_memory(struct_dir, args.limit, save=not args.no_save)
    elif args.profiles_only:
        stats = fetch_profiles_only(struct_dir, args.limit)
    elif args.storage_only:
        stats = fetch_from_storage(cv_dir, args.limit)
    else:
        stats = fetch_from_firestore(cv_dir, struct_dir, args.limit)

    print("\nRésumé :", json.dumps(stats, ensure_ascii=False))
    if direct and args.no_save:
        print("Aucun fichier enregistré : les CV ont été lus en mémoire seulement.")
    elif direct:
        print(f"Aucun PDF enregistré. Profils structurés dans : {struct_dir.resolve()}")
    elif args.profiles_only:
        print(f"Profils dans : {struct_dir.resolve()}")
    else:
        print(f"CV dans : {cv_dir.resolve()}")
    if not (direct and args.no_save):
        print("Rappel : ces fichiers sont des données personnelles, ne pas les committer.")


if __name__ == "__main__":
    main()
