"""Record LLM usage with the Python SDK, then read the customer's margin back.

The "LLM" here is a stand-in that returns OpenAI-shaped responses, so the example runs
offline; swap in a real client and keep the `with ue.llm_call(...)` block as is.

    UE_API_KEY=ue_sk_... python examples/quickstart.py
"""

import os
import random
import time
from typing import Any

import httpx
from unit_economics import UnitEconomics

API_URL = os.environ.get("UE_API_URL", "http://127.0.0.1:8130")
API_KEY = os.environ["UE_API_KEY"]


def fake_completion(model: str, prompt_tokens: int) -> dict[str, Any]:
    cached = prompt_tokens // 3 if random.random() < 0.4 else 0
    return {
        "model": model,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": random.randint(50, 900),
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


def main() -> None:
    customers = ["cus_0001", "cus_0002", "cus_0003"]
    started = time.perf_counter()
    with UnitEconomics(API_KEY, base_url=API_URL, batch_size=250) as ue:
        for i in range(3_000):
            customer = customers[i % len(customers)]
            feature, model = random.choice([("chat", "gpt-4o-mini"), ("agent", "gpt-4o")])
            with ue.llm_call(customer_id=customer, feature=feature, model=model) as call:
                response = fake_completion(model, random.randint(500, 8_000))
                call.record(response=response)
        result = ue.flush()
    print(
        f"sent 3,000 events in {time.perf_counter() - started:.2f}s: "
        f"accepted={result.accepted} duplicates={result.duplicates} invalid={result.invalid} failed={result.failed}"
    )

    time.sleep(1.0)  # the rollup worker runs within a second or so
    with httpx.Client(base_url=API_URL, headers={"authorization": f"Bearer {API_KEY}"}) as api:
        for customer in customers:
            c = api.get(f"/v1/economics/customers/{customer}", params={"days": 30}).json()["customer"]
            print(
                f"{customer}  {c['name']:<18} revenue ${float(c['revenue']):>8.2f}  "
                f"llm cost ${float(c['cost']):>7.2f}  margin {c['margin_pct']:>6}%"
            )


if __name__ == "__main__":
    main()
