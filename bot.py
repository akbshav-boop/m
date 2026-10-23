"""FadeHost-compatible entry point.
Run the same application as `python -m app.main`."""
import asyncio
from app.main import main

if __name__ == "__main__":
    asyncio.run(main())
