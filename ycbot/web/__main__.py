from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    uvicorn.run("ycbot.web.app:build_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
