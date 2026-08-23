import uvicorn
from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="Atlas", version="0.1.0")


app = create_app()


def run() -> None:
    uvicorn.run("atlas.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
