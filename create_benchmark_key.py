import requests
try:
    r = requests.post("http://127.0.0.1:5000/api/v1/keys", json={"org_name": "Benchmark Org", "tier": "enterprise"})
    r.raise_for_status()
    print(r.json()["plaintext_key"])
except Exception as e:
    print(f"Error: {e}")
