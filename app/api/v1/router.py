from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, projects, review_rules

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(projects.router, tags=["projects"])
api_router.include_router(review_rules.router, tags=["rules"])
