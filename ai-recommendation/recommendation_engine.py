
import json
import numpy as np
from pathlib import Path


from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


DATA_PATH = Path(__file__).resolve().parent / "data" / "sample_jobs_100.json"

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class RecommendationEngine:

    def __init__(self, data_path=DATA_PATH):
        # Charger les offres
        with open(data_path, "r", encoding="utf-8-sig") as file:
            self.jobs = json.load(file)

        # Charger le modèle une seule fois
        self.model = SentenceTransformer(MODEL_NAME)

        # Préparer les embeddings des offres une seule fois
        self.job_embeddings = self.model.encode(
            [job["title"] for job in self.jobs],
            normalize_embeddings=True
        )

    def recommend(self, student_profile, job_type=None, top_k=10):

        # Construire le texte du profil étudiant
        student_text = (
            student_profile.get("formation", "")
            + ". Compétences : "
            + ", ".join(student_profile.get("competences", []))
            + ". Postes recherchés : "
            + ", ".join(student_profile.get("postes_recherches", []))
        )

                
        profile_parts = [
            student_profile.get("formation", ""),
            ", ".join(student_profile.get("competences", [])),
            " ".join(student_profile.get("postes_recherches", []))
        ]

        # Ignorer les parties vides
        profile_parts = [
            part for part in profile_parts if part.strip()
        ]

        print("\nLONGUEUR DES PARTIES DU PROFIL :")

        for index, part in enumerate(profile_parts, start=1):
            token_ids = self.model.tokenizer(
                part,
                add_special_tokens=True,
                truncation=False
            )["input_ids"]

            print(
                f"Partie {index} : {len(token_ids)} tokens "
                f"(limite : {self.model.max_seq_length})"
            )

        # Encoder chaque partie séparément
        embeddings = self.model.encode(
            profile_parts,
            normalize_embeddings=True
        )

        # Combiner les embeddings
        student_embedding = np.mean(embeddings, axis=0)

        # Normaliser le vecteur final
        student_embedding = student_embedding / np.linalg.norm(student_embedding)

        # Calculer la similarité avec toutes les offres
        scores = cosine_similarity(
            student_embedding.reshape(1, -1),
            self.job_embeddings
        )[0]

        recommendations = []

        for job, score in zip(self.jobs, scores):

            # Filtre facultatif
            if job_type and job.get("type") != job_type:
                continue

            recommendations.append({
                "id": job.get("id"),
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "type": job.get("type"),
                "url": job.get("url"),
                "score": round(float(score), 4)
            })

        # Classer les offres
        recommendations.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        return recommendations[:top_k]
