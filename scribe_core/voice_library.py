"""
Voice Library & People Model for Session Scribe
- Manages local voice library under D:\\Session_Scribe_Data\\voice_library (configurable)
- Strictly isolated: refuses any path inside the git repo or D:\\Events
- Atomic writes for index (library.json) and cache
- Extracts 16 kHz mono WAV via ffmpeg argument lists
- Verification report: quality, single-voice check, reference match, cross-person check
- Unconfirmed by default; user confirmation required
- Enforces at most 5 samples per person
- Roles are per session: Host, Speaker, Panelist, Moderator, Guest
- Output transcript line formatting: [Name] for single speaker, [Name (Role)] for multi-speaker
"""

import difflib
import json
import math
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
if not hasattr(np, "NaN"):
    np.NaN = np.nan
import soundfile as sf
import torch

DEFAULT_VOICE_LIBRARY_DIR = r"D:\Events\_voice_library"
MAX_SAMPLES_PER_PERSON = 5
MIN_SPEECH_DURATION_SECONDS = 10.0
RECOMMENDED_MIN_SPEECH_SECONDS = 20.0
RECOMMENDED_MAX_SPEECH_SECONDS = 30.0

EMBEDDING_MODEL_NAME = "pyannote/wespeaker-voxceleb-resnet34-LM"
EMBEDDING_MODEL_VERSION = "wespeaker-resnet34-LM-v1"

STRONG_MATCH_THRESHOLD = 0.75
POSSIBLE_MATCH_THRESHOLD = 0.50
CROSS_SPEAKER_ALERT_MARGIN = 0.05

VALID_ROLES = ("Host", "Speaker", "Panelist", "Moderator", "Guest")


def validate_library_path(path: str, repo_root: Optional[str] = None) -> str:
    """
    Validate that voice library path is outside the git repository,
    and if within D:\\Events, it must be D:\\Events\\_voice_library.
    Raises ValueError if path violates isolation boundaries.
    """
    abs_path = os.path.abspath(path.strip())
    if repo_root is None:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    repo_root = os.path.abspath(repo_root)

    # Check repository boundary
    try:
        common = os.path.commonpath([abs_path, repo_root])
        if common == repo_root:
            raise ValueError(f"Voice library location cannot be inside repository: {abs_path}")
    except ValueError as e:
        if "cannot be inside repository" in str(e):
            raise

    # Check D:\Events boundary: allowed only under D:\Events\_voice_library
    events_dir = os.path.abspath(r"D:\Events")
    allowed_voice_lib = os.path.abspath(r"D:\Events\_voice_library")
    try:
        common_ev = os.path.commonpath([abs_path, events_dir])
        if common_ev == events_dir:
            try:
                common_lib = os.path.commonpath([abs_path, allowed_voice_lib])
                if common_lib != allowed_voice_lib:
                    raise ValueError(f"Voice library inside D:\\Events must be strictly under D:\\Events\\_voice_library: {abs_path}")
            except ValueError:
                raise ValueError(f"Voice library inside D:\\Events must be strictly under D:\\Events\\_voice_library: {abs_path}")
    except ValueError as e:
        if "must be strictly under D:\\Events\\_voice_library" in str(e):
            raise

    return abs_path


@dataclass
class Person:
    id: str
    display_name: str
    aliases: List[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    permission_noted: bool = False
    permission_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "aliases": self.aliases,
            "created_at": self.created_at,
            "permission_noted": self.permission_noted,
            "permission_date": self.permission_date,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Person":
        return cls(
            id=data["id"],
            display_name=data["display_name"],
            aliases=data.get("aliases", []),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            permission_noted=data.get("permission_noted", False),
            permission_date=data.get("permission_date", None),
        )


@dataclass
class VoiceSample:
    id: str
    person_id: str
    file: str  # relative to library_dir, e.g. people/<person_id>/<sample_id>.wav
    source_event: Optional[str] = None
    clip: Optional[str] = None
    range: Optional[List[float]] = None  # [start_s, end_s]
    date_added: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    duration: float = 0.0
    quality_rating: str = "Good"  # Good, Fair, Poor
    verification_status: str = "Strong"  # Strong, Possible, No match
    confirmed_by_user: bool = False
    consent: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "person_id": self.person_id,
            "file": self.file,
            "source_event": self.source_event,
            "clip": self.clip,
            "range": self.range,
            "date_added": self.date_added,
            "duration": round(self.duration, 2),
            "quality_rating": self.quality_rating,
            "verification_status": self.verification_status,
            "confirmed_by_user": self.confirmed_by_user,
            "consent": self.consent,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VoiceSample":
        return cls(
            id=data["id"],
            person_id=data["person_id"],
            file=data["file"],
            source_event=data.get("source_event"),
            clip=data.get("clip"),
            range=data.get("range"),
            date_added=data.get("date_added", datetime.now(timezone.utc).isoformat()),
            duration=data.get("duration", 0.0),
            quality_rating=data.get("quality_rating", "Good"),
            verification_status=data.get("verification_status", "Strong"),
            confirmed_by_user=data.get("confirmed_by_user", False),
            consent=data.get("consent", True),
        )


@dataclass
class VerificationReport:
    sample_id: str
    person_id: str
    quality_rating: str
    speech_duration: float
    total_duration: float
    clipping_ratio: float
    noise_floor_db: float
    spectral_flatness: float
    single_voice_check: Dict[str, Any]
    reference_match: Dict[str, Any]
    cross_person_check: Dict[str, Any]
    context_transcript: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "person_id": self.person_id,
            "quality_rating": self.quality_rating,
            "speech_duration": round(self.speech_duration, 2),
            "total_duration": round(self.total_duration, 2),
            "clipping_ratio": round(self.clipping_ratio, 4),
            "noise_floor_db": round(self.noise_floor_db, 2),
            "spectral_flatness": round(self.spectral_flatness, 4),
            "single_voice_check": self.single_voice_check,
            "reference_match": self.reference_match,
            "cross_person_check": self.cross_person_check,
            "context_transcript": self.context_transcript,
        }


def format_transcript_line(
    speaker_name: str,
    role: Optional[str],
    is_multi_speaker: bool,
    start_ts: str,
    end_ts: str,
    text: str,
) -> str:
    """Format transcript lines per session mode."""
    if is_multi_speaker and role and role in VALID_ROLES:
        tag = f"[{speaker_name} ({role})]"
    else:
        tag = f"[{speaker_name}]"
    return f"{tag} [{start_ts} - {end_ts}] {text}"


class VoiceLibrary:
    """
    Manages local people index, voice sample files, embeddings cache,
    and verification reports.
    """

    def __init__(
        self,
        library_dir: str = DEFAULT_VOICE_LIBRARY_DIR,
        repo_root: Optional[str] = None,
        prefer_gpu: bool = True,
    ):
        self.library_dir = validate_library_path(library_dir, repo_root=repo_root)
        self.prefer_gpu = prefer_gpu

        self.people_dir = os.path.join(self.library_dir, "people")
        self.cache_dir = os.path.join(
            self.library_dir, "cache", EMBEDDING_MODEL_VERSION
        )
        self.index_file = os.path.join(self.library_dir, "library.json")

        os.makedirs(self.people_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)

        self._people: Dict[str, Person] = {}
        self._samples: Dict[str, VoiceSample] = {}
        self._model = None
        self._inference = None
        self._device = None

        self._load_index()

    # ------------------------------------------------------------------
    # Index & Persistence (Atomic Writes)
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        if not os.path.isfile(self.index_file):
            self._save_index()
            return
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._people = {
                p["id"]: Person.from_dict(p) for p in data.get("people", [])
            }
            self._samples = {
                s["id"]: VoiceSample.from_dict(s) for s in data.get("samples", [])
            }
        except Exception:
            self._people = {}
            self._samples = {}

    def _save_index(self) -> None:
        """Atomic write of library.json using a temp file."""
        data = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model_version": EMBEDDING_MODEL_VERSION,
            "people": [p.to_dict() for p in self._people.values()],
            "samples": [s.to_dict() for s in self._samples.values()],
        }
        tmp_file = f"{self.index_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, self.index_file)

    def _save_cache_atomic(self, sample_id: str, embedding: np.ndarray) -> str:
        """Atomic write of embedding numpy array."""
        cache_path = os.path.join(self.cache_dir, f"{sample_id}.npy")
        tmp_path = os.path.join(self.cache_dir, f"{sample_id}_tmp.npy")
        np.save(tmp_path, embedding)
        os.replace(tmp_path, cache_path)
        return cache_path

    def _load_cache(self, sample_id: str) -> Optional[np.ndarray]:
        cache_path = os.path.join(self.cache_dir, f"{sample_id}.npy")
        if os.path.isfile(cache_path):
            try:
                return np.load(cache_path)
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------
    # Embedding Model (Lazy Loading & VRAM Isolation)
    # ------------------------------------------------------------------

    def _ensure_model(self):
        """Loads wespeaker embedding model with CPU fallback."""
        if self._model is not None and self._inference is not None:
            return

        from pyannote.audio import Model, Inference

        use_cuda = self.prefer_gpu and torch.cuda.is_available()
        self._device = torch.device("cuda" if use_cuda else "cpu")
        try:
            self._model = Model.from_pretrained(EMBEDDING_MODEL_NAME)
            self._model.to(self._device)
            self._inference = Inference(self._model, window="whole")
        except Exception:
            # Fall back to CPU if CUDA fails
            self._device = torch.device("cpu")
            self._model = Model.from_pretrained(EMBEDDING_MODEL_NAME)
            self._model.to(self._device)
            self._inference = Inference(self._model, window="whole")

    def release_model(self):
        """Explicitly release model from VRAM and run garbage collection."""
        self._model = None
        self._inference = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def get_model_info(self) -> Dict[str, Any]:
        """Report model name, version, device, and VRAM consumption."""
        vram_mb = 0.0
        if torch.cuda.is_available() and self._device and self._device.type == "cuda":
            vram_mb = torch.cuda.memory_allocated() / (1024 * 1024)
        return {
            "model_name": EMBEDDING_MODEL_NAME,
            "model_version": EMBEDDING_MODEL_VERSION,
            "device": str(self._device) if self._device else "unloaded",
            "vram_mb": round(vram_mb, 2),
            "cpu_fallback_supported": True,
        }

    def compute_embedding(self, audio_data: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Compute 256-dimensional speaker embedding from 1D audio array."""
        self._ensure_model()
        waveform = torch.from_numpy(audio_data.astype(np.float32)).unsqueeze(0)
        emb = self._inference({"waveform": waveform, "sample_rate": sample_rate})
        norm = np.linalg.norm(emb)
        if norm > 1e-8:
            emb = emb / norm
        return emb.astype(np.float32)

    # ------------------------------------------------------------------
    # People Operations
    # ------------------------------------------------------------------

    def add_person(
        self,
        display_name: str,
        aliases: Optional[List[str]] = None,
        permission_noted: bool = False,
        permission_date: Optional[str] = None,
    ) -> Person:
        """Add a person to the library."""
        name_clean = display_name.strip()
        if not name_clean:
            raise ValueError("Display name cannot be empty.")
        person_id = f"person_{uuid.uuid4().hex[:12]}"
        if permission_noted and not permission_date:
            permission_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        person = Person(
            id=person_id,
            display_name=name_clean,
            aliases=[a.strip() for a in (aliases or []) if a.strip()],
            permission_noted=permission_noted,
            permission_date=permission_date,
        )
        self._people[person_id] = person
        os.makedirs(os.path.join(self.people_dir, person_id), exist_ok=True)
        self._save_index()
        return person

    def set_person_permission(
        self, person_id: str, noted: bool = True, date_str: Optional[str] = None
    ) -> None:
        """Update permission noted status and date for a person."""
        person = self.get_person(person_id)
        if not person:
            raise KeyError(f"Person not found: {person_id}")
        person.permission_noted = noted
        if noted:
            person.permission_date = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            person.permission_date = None
        self._save_index()

    def get_person(self, person_id: str) -> Optional[Person]:
        return self._people.get(person_id)

    def list_people(self) -> List[Person]:
        return list(self._people.values())

    def search_people(self, query: str) -> List[Person]:
        """Case-insensitive substring search and fuzzy matching."""
        q = query.strip().lower()
        if not q:
            return self.list_people()

        results = []
        names = {}
        for p in self._people.values():
            names[p.display_name] = p
            for alias in p.aliases:
                names[alias] = p

            # Substring check
            if q in p.display_name.lower() or any(q in a.lower() for a in p.aliases):
                results.append(p)

        # Fuzzy matching
        close_matches = difflib.get_close_matches(query, names.keys(), n=5, cutoff=0.6)
        for m in close_matches:
            matched_person = names[m]
            if matched_person not in results:
                results.append(matched_person)

        return results

    def delete_person(self, person_id: str) -> Dict[str, Any]:
        """
        Deletes person, all their sample WAV files, cache entries, and index.
        Returns a summary of removed artifacts.
        """
        if person_id not in self._people:
            raise KeyError(f"Person not found: {person_id}")

        removed_samples = [s for s in self._samples.values() if s.person_id == person_id]
        removed_files = []

        # Remove person directory
        p_dir = os.path.join(self.people_dir, person_id)
        if os.path.isdir(p_dir):
            for root, _, files in os.walk(p_dir):
                for f in files:
                    removed_files.append(os.path.join(root, f))
            shutil.rmtree(p_dir, ignore_errors=True)

        # Remove cache files
        for s in removed_samples:
            cache_file = os.path.join(self.cache_dir, f"{s.id}.npy")
            if os.path.isfile(cache_file):
                removed_files.append(cache_file)
                try:
                    os.remove(cache_file)
                except OSError:
                    pass
            self._samples.pop(s.id, None)

        removed_person = self._people.pop(person_id)
        self._save_index()

        return {
            "person_id": person_id,
            "display_name": removed_person.display_name,
            "removed_samples_count": len(removed_samples),
            "removed_files_count": len(removed_files),
            "removed_files": removed_files,
        }

    # ------------------------------------------------------------------
    # Sample Operations & Verification
    # ------------------------------------------------------------------

    def list_samples(self, person_id: Optional[str] = None) -> List[VoiceSample]:
        if person_id:
            return [s for s in self._samples.values() if s.person_id == person_id]
        return list(self._samples.values())

    def get_sample(self, sample_id: str) -> Optional[VoiceSample]:
        return self._samples.get(sample_id)

    def get_sample_path(self, sample_id: str) -> str:
        s = self.get_sample(sample_id)
        if not s:
            raise KeyError(f"Sample not found: {sample_id}")
        return os.path.join(self.library_dir, s.file)

    def confirm_sample(self, sample_id: str) -> VoiceSample:
        s = self.get_sample(sample_id)
        if not s:
            raise KeyError(f"Sample not found: {sample_id}")
        s.confirmed_by_user = True
        self._save_index()
        return s

    def delete_sample(self, sample_id: str) -> Dict[str, Any]:
        """Removes WAV, cache file, and index entry for a sample."""
        s = self.get_sample(sample_id)
        if not s:
            raise KeyError(f"Sample not found: {sample_id}")

        wav_path = os.path.join(self.library_dir, s.file)
        cache_path = os.path.join(self.cache_dir, f"{sample_id}.npy")
        removed = []
        if os.path.isfile(wav_path):
            try:
                os.remove(wav_path)
                removed.append(wav_path)
            except OSError:
                pass
        if os.path.isfile(cache_path):
            try:
                os.remove(cache_path)
                removed.append(cache_path)
            except OSError:
                pass

        self._samples.pop(sample_id, None)
        self._save_index()
        return {"sample_id": sample_id, "removed_files": removed}

    def add_sample(
        self,
        person_id: str,
        source_audio_path: str,
        range_s: Optional[Tuple[float, float]] = None,
        source_event: Optional[str] = None,
        clip: Optional[str] = None,
        consent: bool = True,
        context_transcript: Optional[str] = None,
        event_roster_person_ids: Optional[List[str]] = None,
    ) -> Tuple[VoiceSample, VerificationReport]:
        """
        Add a new sample from an audio/video file or range.
        Enforces maximum 5 samples per person limit.
        Extracts 16 kHz mono WAV, runs quality & verification report.
        Sample is 'Unconfirmed' (confirmed_by_user = False) by default.
        """
        if person_id not in self._people:
            raise KeyError(f"Person not found: {person_id}")

        existing = self.list_samples(person_id)
        if len(existing) >= MAX_SAMPLES_PER_PERSON:
            raise ValueError(
                f"Person already has maximum of {MAX_SAMPLES_PER_PERSON} samples. "
                "Delete an existing sample before adding a new one."
            )

        if not os.path.isfile(source_audio_path):
            raise FileNotFoundError(f"Source file not found: {source_audio_path}")

        sample_id = f"sample_{uuid.uuid4().hex[:12]}"
        person_folder = os.path.join(self.people_dir, person_id)
        os.makedirs(person_folder, exist_ok=True)
        target_wav = os.path.join(person_folder, f"{sample_id}.wav")

        # 1. Extract 16 kHz mono WAV with ffmpeg (explicit argument list)
        ffmpeg_cmd = ["ffmpeg", "-y"]
        if range_s:
            start_s, end_s = range_s
            ffmpeg_cmd.extend(["-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}"])
        ffmpeg_cmd.extend([
            "-i", os.path.abspath(source_audio_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", "16000",
            target_wav,
        ])

        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.isfile(target_wav):
            raise RuntimeError(f"FFmpeg failed to extract sample: {proc.stderr}")

        # 2. Load audio and compute quality metrics
        audio_data, sr = sf.read(target_wav)
        if len(audio_data) == 0:
            raise ValueError("Extracted audio contains 0 samples.")

        total_duration = len(audio_data) / sr

        # Speech activity detection via frame RMS
        frame_len = int(sr * 0.03)  # 30 ms
        num_frames = len(audio_data) // frame_len
        if num_frames == 0:
            raise ValueError("Audio duration too short for analysis.")

        frames = audio_data[: num_frames * frame_len].reshape(num_frames, frame_len)
        frame_rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)

        # Active speech frames are frames with energy above -40 dB (0.01)
        speech_thresh = 0.01
        speech_frames = np.sum(frame_rms >= speech_thresh)
        speech_duration = float(speech_frames * 0.03)

        # Estimate background noise from silence/pause frames below speech threshold
        silence_frames = frame_rms[frame_rms < speech_thresh]
        if len(silence_frames) > 0:
            noise_floor = float(np.mean(silence_frames))
        else:
            noise_floor = float(np.min(frame_rms)) * 0.1
        noise_floor_db = float(20 * math.log10(noise_floor + 1e-9))

        # Clipping ratio (samples within 1% of full scale)
        clipping_samples = np.sum(np.abs(audio_data) >= 0.99)
        clipping_ratio = float(clipping_samples / len(audio_data))

        # Spectral flatness (variance of power spectrum)
        fft_vals = np.abs(np.fft.rfft(audio_data[: min(len(audio_data), 16000 * 5)]))
        fft_power = fft_vals**2 + 1e-12
        geom_mean = np.exp(np.mean(np.log(fft_power)))
        arith_mean = np.mean(fft_power)
        spectral_flatness = float(geom_mean / arith_mean)

        # Quality Rating: Good, Fair, or Poor
        if speech_duration < MIN_SPEECH_DURATION_SECONDS or clipping_ratio > 0.05 or noise_floor_db > -18.0:
            quality_rating = "Poor"
        elif speech_duration < RECOMMENDED_MIN_SPEECH_SECONDS or clipping_ratio > 0.01 or noise_floor_db > -25.0:
            quality_rating = "Fair"
        else:
            quality_rating = "Good"

        # 3. Embedding computation and caching
        full_embedding = self.compute_embedding(audio_data, sample_rate=sr)
        self._save_cache_atomic(sample_id, full_embedding)

        # 4. Single-Voice Check (sliding 3s window with 1.5s hop)
        win_len = int(sr * 3.0)
        hop_len = int(sr * 1.5)
        window_sims = []
        single_voice_passed = True
        single_voice_warning = None

        if len(audio_data) >= win_len:
            for start in range(0, len(audio_data) - win_len + 1, hop_len):
                w_audio = audio_data[start : start + win_len]
                w_rms = np.sqrt(np.mean(w_audio**2))
                if w_rms >= speech_thresh:
                    w_emb = self.compute_embedding(w_audio, sample_rate=sr)
                    sim = float(np.dot(full_embedding, w_emb))
                    window_sims.append(round(sim, 3))

            if window_sims:
                min_sim = min(window_sims)
                if min_sim < 0.50:
                    single_voice_passed = False
                    single_voice_warning = (
                        f"Potential second voice or mixed speakers detected "
                        f"(minimum sub-window similarity: {min_sim:.2f})."
                    )

        # 5. Reference Match against existing confirmed samples
        person_confirmed = [
            s for s in self.list_samples(person_id) if s.confirmed_by_user and s.id != sample_id
        ]
        if person_confirmed:
            scores = []
            for cs in person_confirmed:
                cached_emb = self._load_cache(cs.id)
                if cached_emb is not None:
                    s_sim = float(np.dot(full_embedding, cached_emb))
                    scores.append(s_sim)
            ref_score = float(np.mean(scores)) if scores else 1.0
            if ref_score >= STRONG_MATCH_THRESHOLD:
                ref_status = "Strong"
            elif ref_score >= POSSIBLE_MATCH_THRESHOLD:
                ref_status = "Possible"
            else:
                ref_status = "No match"
        else:
            ref_score = 1.0
            ref_status = "Strong"  # First baseline sample

        # 6. Cross-Person Check against other people in roster/library
        other_people_ids = event_roster_person_ids or [
            pid for pid in self._people.keys() if pid != person_id
        ]
        cross_warning = None
        closest_other_person = None
        highest_cross_score = 0.0

        for other_pid in other_people_ids:
            if other_pid == person_id:
                continue
            other_samples = [s for s in self.list_samples(other_pid) if s.confirmed_by_user]
            for osamp in other_samples:
                o_emb = self._load_cache(osamp.id)
                if o_emb is not None:
                    sim = float(np.dot(full_embedding, o_emb))
                    if sim > highest_cross_score:
                        highest_cross_score = sim
                        other_p = self.get_person(other_pid)
                        closest_other_person = other_p.display_name if other_p else other_pid

        if highest_cross_score > (ref_score + CROSS_SPEAKER_ALERT_MARGIN) and highest_cross_score > 0.60:
            cross_warning = (
                f"Sample appears closer to '{closest_other_person}' "
                f"(score: {highest_cross_score:.2f}) than target person (score: {ref_score:.2f})."
            )

        # 7. Create VerificationReport
        report = VerificationReport(
            sample_id=sample_id,
            person_id=person_id,
            quality_rating=quality_rating,
            speech_duration=speech_duration,
            total_duration=total_duration,
            clipping_ratio=clipping_ratio,
            noise_floor_db=noise_floor_db,
            spectral_flatness=spectral_flatness,
            single_voice_check={
                "passed": single_voice_passed,
                "warning": single_voice_warning,
                "window_similarities": window_sims,
            },
            reference_match={
                "status": ref_status,
                "score": round(ref_score, 3),
            },
            cross_person_check={
                "warning": cross_warning,
                "closest_other_person": closest_other_person,
                "score": round(highest_cross_score, 3),
            },
            context_transcript=context_transcript,
        )

        # 8. Create VoiceSample (always unconfirmed initially)
        sample = VoiceSample(
            id=sample_id,
            person_id=person_id,
            file=os.path.relpath(target_wav, self.library_dir).replace("\\", "/"),
            source_event=source_event,
            clip=clip,
            range=list(range_s) if range_s else [0.0, round(total_duration, 2)],
            duration=round(total_duration, 2),
            quality_rating=quality_rating,
            verification_status=ref_status,
            confirmed_by_user=False,
            consent=consent,
        )
        self._samples[sample_id] = sample
        self._save_index()

        return sample, report
