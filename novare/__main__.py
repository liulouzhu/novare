"""novare/__main__.py — python -m novare 入口"""

import asyncio
from novare.cli import main

if __name__ == "__main__":
    asyncio.run(main())
