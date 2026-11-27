import os
import wave

res_dir = os.path.join("scribe_core", "resources")
os.makedirs(res_dir, exist_ok=True)
sample_path = os.path.join(res_dir, "sample_5s.wav")

with wave.open(sample_path, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"\x00\x00" * (16000 * 5))

print(f"Created {sample_path}, size: {os.path.getsize(sample_path)} bytes")

