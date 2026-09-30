"""Concord Python 后端的统一启动入口。"""

import asyncio
import os
import sys

import uvicorn

from api import main as api_main

app = api_main.app


def main() -> None:
    """启动 Web API；传入 ``--cli`` 时进入交互式命令行。"""
    if "--cli" in sys.argv:
        asyncio.run(api_main.run_cli())
        return

    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("APP_ENV") == "development",
    )


if __name__ == "__main__":
    main()
