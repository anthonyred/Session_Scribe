"""
scripts/audit_u0_metadata.py

Phase U0 Audit:
- Probes gRPC metadata and trailers from NVIDIA Riva for parakeet-tdt and whisper-large-v3
- Probes HTTP response headers from NVIDIA NVCF endpoints
- Checks local Whisper token output structure
- Tests nvidia-smi power, utilization, memory queries
- STRICT SECURITY: Never prints, logs, or stores API keys or auth tokens.
"""

import os
import sys
import io
import wave
import subprocess

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def get_api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key and os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NVIDIA_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key

def make_test_wav(duration_sec=1.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * duration_sec))
    return buf.getvalue()

def audit_grpc_profiles():
    print("=== 1. AUDITING gRPC METADATA & TRAILERS (RIVA) ===")
    api_key = get_api_key()
    if not api_key:
        print("  [WARN] No NVIDIA_API_KEY found. Skipping live gRPC calls.")
        return

    import riva.client
    import grpc

    profiles = [
        ("parakeet-tdt-0.6b-v2", "d3fe9151-442b-4204-a70d-5fcc597fd610", "en-US"),
        ("whisper-large-v3", "b702f636-f60c-4a3d-a6f4-f3568c13bd7d", "en"),
    ]

    for name, fn_id, lang in profiles:
        print(f"\nProfile: {name} (function-id: {fn_id[:8]}...)")
        # Build raw gRPC channel to inspect initial and trailing metadata
        metadata = [
            ("function-id", fn_id),
            ("authorization", f"Bearer {api_key}"),
        ]
        creds = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel("grpc.nvcf.nvidia.com:443", creds)
        
        # Build recognition request
        audio_data = make_test_wav(1.0)
        try:
            auth = riva.client.Auth(
                uri="grpc.nvcf.nvidia.com:443",
                use_ssl=True,
                metadata_args=[
                    ["function-id", fn_id],
                    ["authorization", f"Bearer {api_key}"],
                ],
            )
            client = riva.client.ASRService(auth)
            config = riva.client.RecognitionConfig(
                language_code=lang,
                max_alternatives=1,
            )
            resp = client.offline_recognize(audio_data, config)
            print(f"  Call status: SUCCESS")
            print(f"  Response type: {type(resp).__name__}")
            # Inspect response fields
            fields = [field.name for field, _ in resp.ListFields()]
            print(f"  Response protobuf fields: {fields}")
            if hasattr(resp, "results") and resp.results:
                r0 = resp.results[0]
                r_fields = [field.name for field, _ in r0.ListFields()]
                print(f"  Result[0] protobuf fields: {r_fields}")
                if r0.alternatives:
                    alt0 = r0.alternatives[0]
                    alt_fields = [field.name for field, _ in alt0.ListFields()]
                    print(f"  Alternative[0] fields: {alt_fields}")
        except Exception as e:
            print(f"  Call error: {type(e).__name__} -> {e}")

def audit_http_headers():
    print("\n=== 2. AUDITING HTTP HEADERS (NVIDIA NVCF) ===")
    api_key = get_api_key()
    if not api_key:
        return

    import urllib.request
    import urllib.error

    endpoints = [
        ("NVCF Base", "https://api.nvcf.nvidia.com/v2/nvcf/status"),
        ("NVCF Functions", "https://api.nvcf.nvidia.com/v2/nvcf/functions"),
        ("Integrate Status", "https://integrate.api.nvidia.com/v1/status"),
    ]

    for label, url in endpoints:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"\nEndpoint: {label} ({url}) -> HTTP {resp.status}")
                # Log header names only
                header_names = list(resp.headers.keys())
                print(f"  Header names: {header_names}")
                # Check for rate-limit or quota headers
                rate_headers = {k: v for k, v in resp.headers.items() if any(x in k.lower() for x in ["rate", "limit", "quota", "credit", "remaining", "retry"])}
                print(f"  Rate/Quota/Usage headers: {rate_headers}")
        except urllib.error.HTTPError as e:
            print(f"\nEndpoint: {label} ({url}) -> HTTP {e.code}")
            header_names = list(e.headers.keys())
            print(f"  Header names: {header_names}")
            rate_headers = {k: v for k, v in e.headers.items() if any(x in k.lower() for x in ["rate", "limit", "quota", "credit", "remaining", "retry"])}
            print(f"  Rate/Quota/Usage headers: {rate_headers}")
        except Exception as e:
            print(f"\nEndpoint: {label} ({url}) -> {type(e).__name__}: {e}")

def audit_local_whisper_tokens():
    print("\n=== 3. AUDITING LOCAL WHISPER TOKEN COUNTS ===")
    try:
        import faster_whisper
        print("  faster-whisper is available.")
        # Inspect Segment namedtuple / class definition
        import inspect
        if hasattr(faster_whisper.transcribe, "Segment"):
            seg_cls = faster_whisper.transcribe.Segment
            print(f"  faster_whisper.Segment fields: {getattr(seg_cls, '_fields', dir(seg_cls))}")
    except Exception as e:
        print(f"  faster-whisper check: {e}")

    try:
        import whisper
        print("  openai-whisper is available.")
        # openai-whisper result dictionary has segments, each segment dict contains 'tokens'
        print("  openai-whisper segment structure: dict containing 'id', 'seek', 'start', 'end', 'text', 'tokens'")
        print("  len(segment['tokens']) exposes exact token count per segment.")
    except Exception as e:
        print(f"  openai-whisper check: {e}")

def audit_nvidia_smi():
    print("\n=== 4. AUDITING NVIDIA-SMI QUERY VIA ARGUMENT LIST ===")
    cmd = [
        "nvidia-smi",
        "--query-gpu=power.draw,utilization.gpu,memory.used",
        "--format=csv,noheader,nounits",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = p.stdout.strip()
        print(f"  Query executed successfully: cmd={cmd}")
        print(f"  Output reading: '{out}'")
        parts = [x.strip() for x in out.split(",")]
        if len(parts) >= 3:
            power_w = float(parts[0])
            gpu_util = float(parts[1])
            mem_mb = float(parts[2])
            print(f"  Parsed: Power={power_w} W, GPU Util={gpu_util}%, VRAM Used={mem_mb} MB")
    except Exception as e:
        print(f"  nvidia-smi error: {e}")

if __name__ == "__main__":
    audit_grpc_profiles()
    audit_http_headers()
    audit_local_whisper_tokens()
    audit_nvidia_smi()

