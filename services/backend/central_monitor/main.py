from fastapi import FastAPI, HTTPException, status

from .health import check_dependencies

app = FastAPI(title="Central Monitor API", version="0.1.0")


@app.get("/api/v1/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "up"}


@app.get("/api/v1/health/ready", tags=["health"])
def ready() -> dict[str, str | bool]:
    dependencies = check_dependencies()
    if not dependencies.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=dependencies.as_dict(),
        )
    return dependencies.as_dict()

