"""Heardle clone: single-player Spotify-backed music guessing game."""

__version__ = "0.1.0"


def main() -> None:
    """Console entry point registered in pyproject.toml; launches the FastAPI server."""
    import uvicorn

    uvicorn.run("heardle.api:app", host="127.0.0.1", port=8000, reload=False)
