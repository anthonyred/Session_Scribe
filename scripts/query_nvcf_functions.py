import os
import sys
import json
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scribe_core import cloud_engine

def main():
    api_key = cloud_engine.get_nvidia_api_key()
    if not api_key:
        print("No API key found!")
        return

    url = "https://api.nvcf.nvidia.com/v2/nvcf/functions"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Failed to query functions list: {e}")
        return

    functions = data.get("functions", [])
    print(f"Total NVCF functions returned: {len(functions)}")

    target_ids = {
        "d3fe9151-442b-4204-a70d-5fcc597fd610": "Parakeet TDT 0.6B v2",
        "b702f636-f60c-4a3d-a6f4-f3568c13bd7d": "Whisper Large v3",
    }

    found = set()
    for fn in functions:
        fn_id = fn.get("id")
        name = fn.get("name", "")
        status = fn.get("status", "")
        if fn_id in target_ids:
            found.add(fn_id)
            print(f"\n[FOUND TARGET] {target_ids[fn_id]}")
            print(f"  ID: {fn_id}")
            print(f"  Name: {name}")
            print(f"  Status: {status}")
            print(f"  Health: {fn.get('health', {})}")
            print(f"  Keys: {list(fn.keys())}")
            # Query function details directly if endpoint exists
            detail_url = f"https://api.nvcf.nvidia.com/v2/nvcf/functions/{fn_id}"
            req_d = urllib.request.Request(detail_url, headers={"Authorization": f"Bearer {api_key}"})
            try:
                with urllib.request.urlopen(req_d, timeout=10) as r_d:
                    detail = json.loads(r_d.read().decode("utf-8"))
                    print(f"  Detail status: {detail.get('function', {}).get('status')}")
                    print(f"  Detail info: {json.dumps(detail.get('function', {}), indent=2)[:500]}")
            except Exception as e_d:
                print(f"  Detail query error: {e_d}")

    for t_id, t_name in target_ids.items():
        if t_id not in found:
            print(f"\n[TARGET NOT IN GENERAL LIST] {t_name} ({t_id}) - attempting direct fetch...")
            detail_url = f"https://api.nvcf.nvidia.com/v2/nvcf/functions/{t_id}"
            req_d = urllib.request.Request(detail_url, headers={"Authorization": f"Bearer {api_key}"})
            try:
                with urllib.request.urlopen(req_d, timeout=10) as r_d:
                    detail = json.loads(r_d.read().decode("utf-8"))
                    print(f"  Direct query success: status={detail.get('function', {}).get('status')}")
                    print(f"  Detail: {json.dumps(detail.get('function', {}), indent=2)[:500]}")
            except urllib.error.HTTPError as e_h:
                print(f"  Direct query HTTP {e_h.code}: {e_h.read().decode('utf-8')[:300]}")
            except Exception as e_d:
                print(f"  Direct query error: {e_d}")

if __name__ == "__main__":
    main()

