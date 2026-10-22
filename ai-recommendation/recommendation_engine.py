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
        model_name: str = MODEL_NAME,
        jobs: list[dict] | None = None,
    ):
        """
        source      : "sample" (défaut), "live" ou chemin d'un fichier JSON
        data_path   : ancien paramètre, équivalent à source=<chemin>
        canada_only : ne garder que les offres au Canada
        use_cache   : réutiliser les vecteurs déjà calculés
        model       : modèle déjà chargé (sert surtout aux tests)
        model_name  : modèle SentenceTransformer à utiliser
        jobs        : liste d'offres déjà chargée (remplace source)
        """
        if data_path is not None:
            source = str(data_path)
        source = source or os.environ.get("JOBS_SOURCE", "sample")
        self.verbose = verbose

        self.model_name = model_name
        # Les modèles E5 attendent un préfixe « query: » / « passage: »
        is_e5 = "e5" in model_name.lower()
        self.query_prefix = "query: " if is_e5 else ""
        self.doc_prefix = "passage: " if is_e5 else ""

        # Charger les offres
        if jobs is not None:
            self.jobs = jobs
            # Liste fournie de l'extérieur : on ne sait pas si elle est complète
            self._prune_cache = False
        else:
            self.jobs = load_jobs(source, canada_only=canada_only)
            # On ne nettoie le cache que si on a TOUTES les offres réelles,
            # sinon un essai filtré (ex. Canada) effacerait les autres vecteurs.
            self._prune_cache = source == "live" and not canada_only
            print(f"Offres chargées : {len(self.jobs)} (source : {source})")

        # Charger le modèle une seule fois
        if model is None:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(model_name)
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
        slug = hashlib.md5(self.model_name.encode()).hexdigest()[:8]
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
            titles = [self.doc_prefix + title_of[k] for k in missing]
            vectors = self.model.encode(
                titles,
                batch_size=128,
                normalize_embeddings=True,
                show_progress_bar=len(missing) > 500,
            )
            cached.update(zip(missing, np.asarray(vectors, dtype=np.float32)))

            if use_cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                if self._prune_cache:
                    # Toutes les offres réelles : on retire les offres disparues.
                    current = list(dict.fromkeys(keys))
                else:
                    current = list(cached)
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
    def profile_scores(self, student_profile: dict) -> np.ndarray | None:
        """Similarité du profil avec CHAQUE offre (même ordre que self.jobs).
        Retourne None si le profil est vide."""
        profile_parts = [
            student_profile.get("formation", ""),
            ", ".join(student_profile.get("competences", [])),
            " ".join(student_profile.get("postes_recherches", [])),
        ]
        profile_parts = [part for part in profile_parts if part.strip()]
        if not profile_parts or len(self.jobs) == 0:
            return None

        if self.verbose:
            print("\nLONGUEUR DES PARTIES DU PROFIL :")
            for index, part in enumerate(profile_parts, start=1):
                n = len(self.model.tokenizer(part, add_special_tokens=True,
                                             truncation=False)["input_ids"])
                print(f"Partie {index} : {n} tokens (limite : {self.model.max_seq_length})")

        # Encoder chaque partie séparément, puis faire la moyenne
        embeddings = np.asarray(
            self.model.encode([self.query_prefix + part for part in profile_parts],
                              normalize_embeddings=True), dtype=np.float32)
        student_embedding = embeddings.mean(axis=0)
        student_embedding /= np.linalg.norm(student_embedding)

        # Vecteurs normalisés : la similarité cosinus est un simple produit scalaire
        return self.job_embeddings @ student_embedding

    def recommend(self, student_profile: dict, job_type: str | None = None, top_k: int = 10):
        scores = self.profile_scores(student_profile)
        if scores is None:
            return []

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
