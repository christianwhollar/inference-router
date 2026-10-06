import asyncio
import json
import tempfile
from pathlib import Path
from .auth import Identity
from .ledger import Ledger
from .models import Completion, Model
from .service import Router


async def demo():
    with tempfile.TemporaryDirectory() as temp:
        models = [
            Model(
                name="small-fixture",
                protocol="fixture",
                quality=0.6,
                input_per_million=0.1,
                output_per_million=0.2,
                approved_for_confidential=True,
            ),
            Model(
                name="large-fixture",
                protocol="fixture",
                quality=0.9,
                input_per_million=1,
                output_per_million=2,
            ),
        ]
        router = Router(models, Ledger(Path(temp) / "usage.db"))
        actor = Identity("alpha", "analyst", "analyst")
        request = Completion(prompt="Summarize this synthetic settlement discrepancy.")
        first = await router.complete(actor, request)
        second = await router.complete(actor, request)
        print(json.dumps({"first": first, "repeat": second}, indent=2))


if __name__ == "__main__":
    asyncio.run(demo())
