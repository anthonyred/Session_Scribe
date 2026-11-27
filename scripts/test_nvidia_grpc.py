"""
scripts/test_nvidia_grpc.py

Probes whether speech_contexts (phrase boosting) is accepted by parakeet and whisper endpoints.
"""

import os
import wave
import io
import riva.client

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

def test_boosting(name, fn_id, lang, api_key):
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
    # Add boosting for technical terms
    terms = ["Kubernetes", "NeMo", "Triton", "Microservices"]
    riva.client.add_word_boosting_to_config(config, boosted_lm_words=terms, boosted_lm_score=10.0)
    audio = make_test_wav(1.0)
    try:
        resp = client.offline_recognize(audio, config)
        print(f"  {name}: ACCEPTED phrase boosting ({len(terms)} terms, score=10.0)")
    except Exception as e:
        print(f"  {name}: REJECTED phrase boosting -> {e}")

def main():
    api_key = get_api_key()
    fn_parakeet = "d3fe9151-442b-4204-a70d-5fcc597fd610"
    fn_whisper = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
    
    print("=== TESTING PHRASE BOOSTING SUPPORT ===")
    test_boosting("parakeet-tdt-0.6b-v2", fn_parakeet, "en-US", api_key)
    test_boosting("whisper-large-v3", fn_whisper, "en", api_key)

if __name__ == "__main__":
    main()
