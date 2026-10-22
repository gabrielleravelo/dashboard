"""Tests du moteur et du chargement des offres, SANS télécharger le vrai modèle.

Un faux modèle (sac de mots) remplace SentenceTransformer : les tests sont
rapides et vérifient la logique (cache, filtres, tri), pas la qualité du modèle.
Lancer : python -m pytest test_recommendation_engine.py -v
"""
import json
import re

import numpy as np
import pytest

import jobs_source
import recommendation_engine as re_mod
from jobs_source import is_canada, load_jobs
from recommendation_engine import RecommendationEngine

JOBS = [
    {"id": "1", "title": "Electrical Engineering Intern", "company": "A", "location": "Montréal, QC", "type": "internship", "active": True},
    {"id": "2", "title": "Software Engineer Intern", "company": "B", "location": "San Diego, Canada", "type": "internship", "active": True},
    {"id": "3", "title": "Data Analyst New Grad", "company": "C", "location": "Toronto, ON", "type": "new_grad", "active": True},
    {"id": "4", "title": "Marketing Intern", "company": "D", "location": "Paris", "type": "internship", "active": False},
    {"id": "1", "title": "Electrical Engineering Intern", "company": "A", "location": "Montréal, QC", "type": "internship", "active": True},
]


class FakeModel:
    """Vecteur = sac de mots haché sur 64 dimensions."""
    def __init__(self):
        self.calls = 0

    def encode(self, texts, normalize_embeddings=True, **kw):
        self.calls += 1
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", t.lower()):
                out[i, hash(w) % 64] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


@pytest.fixture
def jobs_file(tmp_path, monkeypatch):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(JOBS), encoding="utf-8")
    monkeypatch.setattr(jobs_source, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(re_mod, "CACHE_DIR", tmp_path / "cache")
    return path


def test_load_jobs_filters_inactive_and_duplicates(jobs_file):
    jobs = load_jobs(str(jobs_file))
    assert [j["id"] for j in jobs] == ["1", "2", "3"]


def test_canada_filter_ignores_california(jobs_file):
    assert not is_canada("San Diego, Canada")      # « CA » = Californie
    assert is_canada("Montréal, QC") and is_canada("Toronto, ON, Canada")
    jobs = load_jobs(str(jobs_file), canada_only=True)
    assert [j["id"] for j in jobs] == ["1", "3"]


def test_recommend_ranks_and_filters(jobs_file):
    engine = RecommendationEngine(source=str(jobs_file), model=FakeModel())
    profile = {"formation": "electrical engineering", "competences": ["intern"]}
    results = engine.recommend(profile, top_k=3)
    assert results[0]["id"] == "1"
    assert results == sorted(results, key=lambda r: -r["score"])
    only_new_grad = engine.recommend(profile, job_type="new_grad", top_k=10)
    assert [r["id"] for r in only_new_grad] == ["3"]


def test_embeddings_are_cached(jobs_file):
    model = FakeModel()
    RecommendationEngine(source=str(jobs_file), model=model)
    assert model.calls == 1
    RecommendationEngine(source=str(jobs_file), model=model)  # tout vient du cache
    assert model.calls == 1


def test_empty_profile_returns_nothing(jobs_file):
    engine = RecommendationEngine(source=str(jobs_file), model=FakeModel())
    assert engine.recommend({"formation": "", "competences": []}) == []
