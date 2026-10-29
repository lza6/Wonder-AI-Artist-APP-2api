"""End-to-end AI avatar (dreambooth) run: 3 distinct inputs -> create -> poll -> document."""
import json
import time

import wonder_client as wc

BASE = "https://dreambooth-api.wonderai.app/api/v1/dreambooth/"
NAMES = [
    "input-images/wonderaf89c3809e924a45b0768250cf336248.jpg",
    "input-images/wonder582435861e3142e6b0dac5d91adf833a.jpg",
    "input-images/wonder13d9940d9e404bdbbf593eb3c3c03a4b.jpg",
]

client = wc.WonderClient()
print("uid", client.user_id, "credits", client.user()["remainingCredits"], flush=True)
images = [f"https://storage.googleapis.com/ai-avatar-temporary-images/{n}" for n in NAMES]
created = client._post(
    BASE + "create",
    {"userId": client.user_id, "packType": "pack1", "images": images, "gender": "female", "platform": "android"},
)
print("create", created, flush=True)
doc = created["docId"]

for i in range(40):
    time.sleep(20)
    try:
        data = client._post(BASE + "get", {"userId": client.user_id})["data"]
    except Exception as exc:  # noqa: BLE001
        print(f"[{i}] ERR {str(exc)[:150]}", flush=True)
        continue
    row = next((d for d in data if d["docId"] == doc), None)
    print(f"[{i}] {json.dumps(row)}", flush=True)
    if row is None:
        print("消失（训练失败被清理）", flush=True)
        break
    if row["status"] not in ("PROCESSING", "PENDING"):
        print("终态", row, flush=True)
        try:
            print("doc", json.dumps(client._post(BASE + "get-document", {"userId": client.user_id, "docId": doc}))[:2000], flush=True)
        except Exception as exc:  # noqa: BLE001
            print("doc ERR", str(exc)[:200], flush=True)
        break

print("final credits", client.user()["remainingCredits"], flush=True)
