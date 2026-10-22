"""
recommendation_engine.py — Moteur de recommandation Tail'ed.

Compare le profil d'un étudiant aux titres des offres grâce à un modèle
SentenceTransformer multilingue (similarité cosinus entre vecteurs).

Sources d'offres (voir jobs_source.py) :
    "sample" : 100 offres de test (data/sample_jobs_100.json), rapide, hors ligne
    "live"   : les ~28 000 vraies offres de Tail'ed (jobs.json, mis en cache 24 h)

Les vecteurs des offres sont mis en cache (data/cache/) : le premier calcul
sur les vraies offres prend quelques minutes, ensuite seules les nouvelles
offres sont calculées.

La source peut aussi être choisie avec la variable d'environnement
JOBS_SOURCE (utile pour api.py) :
    $env:JOBS_SOURCE = "live"; uvicorn api:app
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

from jobs_source import CACHE_DIR, load_jobs

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class RecommendationEngine:
    def __init__(
        self,
        source: str | None = None,
        data_path: str | Path | None = None,
        canada_only: bool = False,
        use_cache: bool = True,
        verbose: bool = False,
        model=None,
    ):
        """
        source      : "sample" (défaut), "live" ou chemin d'un fichier JSON
        data_path   : ancien paramètre, équivalent à source=<chemin>
        canada_only : ne garder que les offres au Canada
        use_cache   : réutiliser les vecteurs déjà calculés
        model       : modèle déjà chargé (sert surtout aux tests)
        """
        if data_path is not None:
            source = str(data_path)
        source = source or os.environ.get("JOBS_SOURCE", "sample")
        self.verbose = verbose

        # Charger les offres
        self.jobs = load_jobs(source, canada_only=canada_only)
        print(f"Offres chargées : {len(self.jobs)} (source : {source})")

        # Charger le modèle une seule fois
        if model is None:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(MODEL_NAME)
        self.model = model

        # Vecteurs des offres (avec cache)
        self.job_embeddings = self._encode_jobs(use_cache)

    # ------------------------------------------------------------------
    # Vecteurs des offres
    # ------------------------------------------------------------------
    @staticmethod
    def _job_key(job: dict) -> str:
        # Empreinte de l'id + du titre : si le titre change, on recalcule.
        raw = f"{job.get('id', '')}|{job.get('title', '')}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cache_path(self) -> Path:
        slug = hashlib.md5(MODEL_NAME.encode()).hexdigest()[:8]
        return CACHE_DIR / f"job_embeddings_{slug}.npz"

    def _encode_jobs(self, use_cache: bool) -> np.ndarray:
        keys = [self._job_key(j) for j in self.jobs]
        cached: dict[str, np.ndarray] = {}
        path = self._cache_path()

        if use_cache and path.exists():
            data = np.load(path, allow_pickle=False)
            cached = dict(zip(data["keys"].tolist(), data["vectors"].astype(np.float32)))

        title_of = {k: j.get("title", "") for k, j in zip(keys, self.jobs)}
        missing = [k for k in dict.fromkeys(keys) if k not in cached]
        if missing:
            print(f"Calcul des vecteurs pour {len(missing)} offre(s)...")
            titles = [title_of[k] for k in missing]
            vectors = self.model.encode(
                titles,
                batch_size=128,
                normalize_embeddings=True,
                show_progress_bar=len(missing) > 500,
            )
            cached.update(zip(missing, np.asarray(vectors, dtype=np.float32)))

            if use_cache:
                # On ne garde que les offres actuelles (les anciennes sont retirées).
                path.parent.mkdir(parents=True, exist_ok=True)
                current = list(dict.fromkeys(keys))
                # float16 : fichier deux fois plus léger, précision suffisante
                np.savez(path, keys=np.array(current),
                         vectors=np.stack([cached[k] for k in current]).astype(np.float16))
        elif self.jobs:
            print("Vecteurs des offres chargés depuis le cache.")

        if not self.jobs:
            return np.zeros((0, 1), dtype=np.float32)
        return np.stack([cached[k] for k in keys]).astype(np.float32)

    # ------------------------------------------------------------------
    # Recommandation
    # ------------------------------------------------------------------
    def recommend(self, student_profile: dict, job_type: str | None = None, top_k: int = 10):
        profile_parts = [
            student_profile.get("formation", ""),
            ", ".join(student_profile.get("competences", [])),
            " ".join(student_profile.get("postes_recherches", [])),
        ]
        profile_parts = [part for part in profile_parts if part.strip()]
        if not profile_parts or len(self.jobs) == 0:
            return []

        if self.verbose:
            print("\nLONGUEUR DES PARTIES DU PROFIL :")
            for index, part in enumerate(profile_parts, start=1):
                n = len(self.model.tokenizer(part, add_special_tokens=True,
                                             truncation=False)["input_ids"])
                print(f"Partie {index} : {n} tokens (limite : {self.model.max_seq_length})")

        # Encoder chaque partie séparément, puis faire la moyenne
        embeddings = np.asarray(
            self.model.encode(profile_parts, normalize_embeddings=True), dtype=np.float32)
        student_embedding = embeddings.mean(axis=0)
        student_embedding /= np.linalg.norm(student_embedding)

        # Vecteurs normalisés : la similarité cosinus est un simple produit scalaire
        scores = self.job_embeddings @ student_embedding

        if job_type:
            mask = np.array([j.get("type") == job_type for j in self.jobs])
            scores = np.where(mask, scores, -np.inf)

        k = min(top_k, int(np.isfinite(scores).sum()))
        if k <= 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]

        return [
            {
                "id": self.jobs[i].get("id"),
                "title": self.jobs[i].get("title"),
                "company": self.jobs[i].get("company"),
                "location": self.jobs[i].get("location"),
                "type": self.jobs[i].get("type"),
                "url": self.jobs[i].get("url"),
                "score": round(float(scores[i]), 4),
            }
            for i in top
        ]
