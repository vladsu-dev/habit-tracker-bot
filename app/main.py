from fastapi import FastAPI

from app import auth, habits

app = FastAPI(title="Трекер привычек")
app.include_router(auth.router)
app.include_router(habits.router)


@app.get("/health")
def check_health() -> dict[str, str]:
    return {"status": "ok"}
