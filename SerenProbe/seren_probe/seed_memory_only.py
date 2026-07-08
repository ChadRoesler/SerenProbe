"""Seed Memory service with synthetic data — standalone to avoid buffering issues."""
import sys, httpx, json
from .dataset import MEMORY_SHORT, MEMORY_NEAR, MEMORY_LONG

url = "http://localhost:7420"

def post(path, body):
    r = httpx.post(f"{url}{path}", json=body, timeout=30.0)
    return r.json()

# Short-term
print(f"Short: {len(MEMORY_SHORT)}")
for i, s in enumerate(MEMORY_SHORT):
    body = {"content": s["content"]}
    if s.get("topic"): body["topic"] = s["topic"]
    post("/short", body)
    if (i+1) % 50 == 0: print(f"  short {i+1}/{len(MEMORY_SHORT)}")

# Near-term
print(f"Near: {len(MEMORY_NEAR)}")
for i, n in enumerate(MEMORY_NEAR):
    body = {"intent": n["intent"]}
    if n.get("topic"): body["topic"] = n["topic"]
    post("/near", body)
    if (i+1) % 10 == 0: print(f"  near {i+1}/{len(MEMORY_NEAR)}")

# Long-term via promote+delete
print(f"Long: {len(MEMORY_LONG)}")
for i, lf in enumerate(MEMORY_LONG):
    body = {"content": lf["content"]}
    if lf.get("topic"): body["topic"] = lf["topic"]
    r = post("/short", body)
    sid = r.get("id", "")
    if sid:
        post(f"/short/{sid}/promote", {})
        httpx.delete(f"{url}/short/{sid}", timeout=10.0)
    if (i+1) % 25 == 0: print(f"  long {i+1}/{len(MEMORY_LONG)}")

print("Memory seeding done")
