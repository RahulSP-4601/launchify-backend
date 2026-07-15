from app.api.routes import health_check


async def main() -> None:
    payload = await health_check()
    assert payload["status"] == "ok"
    assert payload["service"] == "launchify-backend"


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
