import uvicorn

from .settings import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "codex_bridge_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
