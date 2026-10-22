"""Tests du script d'évaluation (faux modèle, aucune donnée réelle).
Lancer : python -m pytest test_evaluate_recommendations.py -v
"""
import csv

import pytest

import evaluate_recommendations as ev
import recommendation_engine as re_mod
from test_recommendation_engine import FakeModel

JOBS = [
    {"id": "1", "title": "Electrical Engineering Intern"},
    {"id": "2", "title": "Financial Analyst Intern"},
    {"id": "3", "title": "Stagiaire en génie électrique"},
    {"id": "4", "title": "Internal Controls Audit Intern"},
]
PROFILES = [{
    "id": "electrique", "domaine": "Génie électrique",
    "profil": {"education": [{"program": "electrical engineering", "school": "X"}],
               "skillsStructured": [{"name": "intern"}]},
    "pertinent_si": "electrical|[ée]lectrique|controls",
    "sauf_si": "audit",
}]


def test_metrics():
    assert ev.precision_at_k([1, 0, 1, 0], 4) == 0.5
    assert ev.reciprocal_rank([0, 0, 1], 3) == pytest.approx(1 / 3)
    assert ev.reciprocal_rank([0, 0, 0], 3) == 0.0


def test_auto_relevance_with_exclusion():
    p = PROFILES[0]
    assert ev.auto_relevance(p, JOBS[0]) == 1
    assert ev.auto_relevance(p, JOBS[2]) == 1   # titre en français
    assert ev.auto_relevance(p, JOBS[3]) == 0   # « controls » mais « audit » : exclu
    assert ev.auto_relevance(p, JOBS[1]) == 0


def test_evaluate_with_embedding_and_tfidf(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod, "CACHE_DIR", tmp_path)
    rankers = [ev.TfidfRanker(JOBS), ev.EmbeddingRanker(JOBS, "minilm", model=FakeModel())]
    results = ev.evaluate(PROFILES, JOBS, rankers, k=2)
    assert results["tfidf"]["electrique"]["mrr"] == 1.0
    assert results["minilm"]["electrique"]["top"][0] == "Electrical Engineering Intern"


def test_export_and_manual_labels_roundtrip(tmp_path):
    rankers = [ev.TfidfRanker(JOBS), ev.RandomRanker(JOBS)]
    csv_path = tmp_path / "a_annoter.csv"
    ev.export_for_annotation(PROFILES, JOBS, rankers, k=4, path=csv_path)

    # Annoter comme le ferait l'étudiante : seule l'offre 1 est pertinente
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    assert len(rows) == 4  # chaque paire une seule fois, même proposée par 2 méthodes
    for row in rows:
        row["pertinent"] = "1" if row["job_id"] == "1" else "0"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    labels = ev.read_labels(csv_path)
    assert labels[("electrique", "1")] == 1 and labels[("electrique", "2")] == 0
    results = ev.evaluate(PROFILES, JOBS, [ev.TfidfRanker(JOBS)], k=1, labels=labels)
    assert results["tfidf"]["electrique"]["precision"] == 1.0
