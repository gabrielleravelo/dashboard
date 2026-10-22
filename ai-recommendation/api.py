
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from recommendation_engine import RecommendationEngine


# Moteur IA chargé une seule fois au démarrage
engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = RecommendationEngine()
    yield
    engine = None


app = FastAPI(
    title="Tail'ed AI Recommendation API",
    version="0.1.0",
    lifespan=lifespan
)


# Structure du profil envoyé par le dashboard
class StudentProfile(BaseModel):
    formation: str = ""
    competences: list[str] = Field(default_factory=list)
    postes_recherches: list[str] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    profile: StudentProfile
    job_type: Literal["internship", "new_grad"] | None = None
    top_k: int = Field(default=10, ge=1, le=50)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/recommend")
def recommend(request: RecommendationRequest):
    results = engine.recommend(
        student_profile=request.profile.model_dump(),
        job_type=request.job_type,
        top_k=request.top_k
    )

    return {
        "count": len(results),
        "recommendations": results
    }

