"""
scribe_core/enrollment_manager.py
Coordinates multi-sample biometric enrollment into Voice Vault v2.

Core Requirements & Invariants:
1. Target: 3-5 approved samples per person (2-3 minutes total speech recommended).
2. Per-sample quality verification and explicit user approval before commitment.
3. Cross-sample consistency: flags samples differing noticeably from others in the session.
4. Duplicate profile warning: detects high similarity to existing profiles (name shown only when unlocked).
5. Dual-zone storage:
   - Zone 1 (Identity): Real name, session roles, auditable consent record.
   - Zone 2 (Biometric): Combined profile embedding, individual embeddings, encrypted WAVs, model version.
6. Re-embed path: supports batch re-embedding of all enrolled profiles from encrypted reference samples.
7. VRAM mutual exclusion: embedding model runs locally and never together with Whisper in VRAM.
8. Zero disk plaintext: audio buffers and embedding vectors remain strictly in memory
   until committed to disk as AES-256-GCM ciphertext.
9. Records audit entries to Keyed-MAC audit log with anonymous spk_ ID only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

from scribe_core.audio_extractor import extract_audio_in_memory, AudioExtractionError
from scribe_core.embedding_engine import LocalEmbeddingEngine
from scribe_core.vault_crypto import generate_speaker_id
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.voice_quality_checker import evaluate_sample_quality, QualityCheckResult

logger = logging.getLogger("scribe_core.enrollment")

MIN_ENROLLMENT_SAMPLES = 3
MAX_ENROLLMENT_SAMPLES = 5

CROSS_SAMPLE_CONSISTENCY_THRESHOLD = 0.65
DUPLICATE_PROFILE_SIMILARITY_THRESHOLD = 0.82


def compute_cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Computes cosine similarity between two 1D vectors."""
    n1 = float(np.linalg.norm(vec1))
    n2 = float(np.linalg.norm(vec2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    return float(np.dot(vec1, vec2) / (n1 * n2))


@dataclass
class PendingSample:
    """An uncommitted voice sample held strictly in memory during enrollment."""
    sample_index: int
    sample_name: str
    source_path: str
    time_range: Optional[Tuple[float, float]]
    wav_bytes: bytes
    audio_data: np.ndarray
    quality_report: QualityCheckResult
    embedding: np.ndarray
    approved_by_user: bool = False
    is_borderline_accepted: bool = False
    is_inconsistent: bool = False
    consistency_similarity: float = 1.0


@dataclass
class EnrollmentSession:
    """State of an in-progress enrollment session for one person."""
    spk_id: str
    full_name: str
    roles: List[str]
    consent_noted: bool
    consent_date_utc: str
    notes: Optional[str] = None
    samples: List[PendingSample] = field(default_factory=list)
    duplicate_warning: Optional[str] = None

    @property
    def approved_sample_count(self) -> int:
        return sum(1 for s in self.samples if s.approved_by_user)

    @property
    def total_speech_duration(self) -> float:
        return sum(s.quality_report.speech_duration_sec for s in self.samples if s.approved_by_user)


class EnrollmentManager:
    """
    Orchestrates the voice profile enrollment lifecycle into Vault v2.
    """

    def __init__(
        self,
        storage_engine: VaultV2StorageEngine,
        embedding_engine: Optional[LocalEmbeddingEngine] = None,
    ):
        self.storage = storage_engine
        self.vault = storage_engine.vault
        self.embedding_engine = embedding_engine or LocalEmbeddingEngine()

    def start_session(
        self,
        full_name: str,
        roles: Optional[List[str]] = None,
        consent_noted: bool = False,
        notes: Optional[str] = None,
    ) -> EnrollmentSession:
        """
        Initializes a fresh enrollment session for a person.
        Requires explicit consent flag per security invariants.
        """
        name_clean = full_name.strip()
        if not name_clean:
            raise ValueError("Person full name cannot be empty.")

        if not consent_noted:
            raise ValueError("Explicit consent flag must be noted prior to creating an enrollment session.")

        spk_id = generate_speaker_id()
        now_utc = datetime.now(timezone.utc).isoformat()

        session = EnrollmentSession(
            spk_id=spk_id,
            full_name=name_clean,
            roles=roles or ["Speaker"],
            consent_noted=True,
            consent_date_utc=now_utc,
            notes=notes,
        )
        return session

    def check_duplicate_profile(self, new_emb: np.ndarray) -> Optional[Tuple[str, Optional[str], float]]:
        """
        Scans existing enrolled profiles in Biometric Zone for high voice similarity.
        Returns: (spk_id, resolved_name_or_none, similarity) if similarity >= 0.82.
        Names are only resolved if the vault is currently unlocked.
        """
        try:
            bio_ids = self.storage.list_biometric_ids(include_quarantined=False)
            highest_sim = 0.0
            matched_spk: Optional[str] = None

            for spk_id in bio_ids:
                existing_bytes = self.storage.get_biometric_embedding_for_matching(spk_id)
                if existing_bytes is not None:
                    existing_arr = np.frombuffer(existing_bytes, dtype=np.float32)
                    if len(existing_arr) == len(new_emb):
                        sim = compute_cosine_similarity(new_emb, existing_arr)
                        if sim > highest_sim:
                            highest_sim = sim
                            matched_spk = spk_id

            if matched_spk is not None and highest_sim >= DUPLICATE_PROFILE_SIMILARITY_THRESHOLD:
                resolved_name: Optional[str] = None
                if self.vault.is_unlocked():
                    resolved_name = self.storage.resolve_speaker_name(matched_spk)
                return matched_spk, resolved_name, highest_sim
        except Exception as e:
            logger.warning("Duplicate profile check error: %s", e)
        return None

    def inspect_sample(
        self,
        session: EnrollmentSession,
        source_media_path: str,
        time_range: Optional[Tuple[float, float]] = None,
        override_borderline: bool = False,
        confirm_inconsistency: bool = False,
    ) -> Tuple[bool, str, Optional[PendingSample], Optional[QualityCheckResult]]:
        """
        Inspects a sample in memory (FFmpeg extraction, quality checks, embedding,
        consistency and duplicate checks). Does NOT stage into session.samples.
        """
        if len(session.samples) >= MAX_ENROLLMENT_SAMPLES:
            return False, f"Maximum of {MAX_ENROLLMENT_SAMPLES} samples already reached for this session.", None, None

        # 1. Extract 16 kHz mono WAV via FFmpeg pipe into memory
        try:
            wav_bytes = extract_audio_in_memory(source_media_path, time_range=time_range)
        except Exception as e:
            logger.error("Audio extraction failed: %s", e)
            return False, f"Audio extraction error: {e}", None, None

        # 2. Acoustic Quality Checks
        quality = evaluate_sample_quality(wav_bytes, override_borderline=override_borderline)
        if quality.verdict == "CLEAR_FAIL":
            return False, quality.failure_reason or "Acoustic quality check failed.", None, quality

        if quality.verdict == "BORDERLINE" and not override_borderline:
            return False, f"Borderline quality ({quality.failure_reason}). Check 'Accept Anyway' to allow.", None, quality

        # 3. Decode audio data in memory for neural embedding
        try:
            audio_data, sr = sf.read(io.BytesIO(wav_bytes))
            if audio_data.ndim > 1:
                audio_data = np.mean(audio_data, axis=1)
            audio_data = audio_data.astype(np.float32)
        except Exception as e:
            return False, f"Failed to parse audio array: {e}", None, quality

        # 4. Neural embedding computation (hardware-isolated, VRAM-guarded)
        try:
            emb = self.embedding_engine.compute_embedding(audio_data, sample_rate=sr)
        except Exception as e:
            logger.error("Embedding computation failed: %s", e)
            return False, f"Neural embedding error: {e}", None, quality

        # 5. Cross-Sample Consistency Check
        is_inconsistent = False
        consistency_sim = 1.0
        if len(session.samples) > 0:
            existing_embs = [s.embedding for s in session.samples]
            centroid = np.mean(existing_embs, axis=0)
            consistency_sim = compute_cosine_similarity(emb, centroid)
            if consistency_sim < CROSS_SAMPLE_CONSISTENCY_THRESHOLD:
                is_inconsistent = True
                if not confirm_inconsistency:
                    return (
                        False,
                        f"Cross-sample inconsistency: Embedding similarity to other samples is {consistency_sim:.2f} "
                        f"(< {CROSS_SAMPLE_CONSISTENCY_THRESHOLD:.2f} threshold). Please confirm if this is the same speaker.",
                        None,
                        quality,
                    )

        # 6. Duplicate-Profile Scan against existing vault profiles
        dup_info = self.check_duplicate_profile(emb)
        if dup_info is not None:
            dup_spk, dup_name, dup_sim = dup_info
            if dup_name:
                session.duplicate_warning = (
                    f"Voice is {dup_sim * 100:.1f}% similar to enrolled profile '{dup_name}' ({dup_spk})."
                )
            else:
                session.duplicate_warning = (
                    f"Voice is {dup_sim * 100:.1f}% similar to enrolled profile {dup_spk} (Unlock vault to view name)."
                )

        sample_idx = len(session.samples) + 1
        sample_name = f"sample_{sample_idx}"
        pending = PendingSample(
            sample_index=sample_idx,
            sample_name=sample_name,
            source_path=source_media_path,
            time_range=time_range,
            wav_bytes=wav_bytes,
            audio_data=audio_data,
            quality_report=quality,
            embedding=emb,
            approved_by_user=False,
            is_borderline_accepted=bool(quality.is_borderline and override_borderline),
            is_inconsistent=is_inconsistent,
            consistency_similarity=consistency_sim,
        )
        return True, "Sample successfully inspected.", pending, quality

    def stage_sample(
        self,
        session: EnrollmentSession,
        pending: PendingSample,
        user_approved: bool = False,
    ) -> Tuple[bool, str]:
        """
        Stages an inspected sample into the active enrollment session.
        Security Invariant: Every sample, including CLEAR_PASS, strictly requires explicit user approval before staging.
        """
        if not user_approved:
            return False, "Sample staging rejected: Explicit operator approval is required before staging."
        if len(session.samples) >= MAX_ENROLLMENT_SAMPLES:
            return False, f"Maximum of {MAX_ENROLLMENT_SAMPLES} samples already reached for this session."

        sample_idx = len(session.samples) + 1
        pending.sample_index = sample_idx
        pending.sample_name = f"sample_{sample_idx}"
        pending.approved_by_user = True
        session.samples.append(pending)
        return True, f"Sample {pending.sample_name} approved and staged."

    def import_and_verify_sample(
        self,
        session: EnrollmentSession,
        source_media_path: str,
        time_range: Optional[Tuple[float, float]] = None,
        user_approved: bool = False,
        override_borderline: bool = False,
        confirm_inconsistency: bool = False,
    ) -> Tuple[bool, str, Optional[QualityCheckResult]]:
        """
        Inspects sample in memory and stages ONLY if explicit user_approved is True.
        If user_approved is False, rejects staging and returns quality inspection.
        """
        ok, msg, pending, quality = self.inspect_sample(
            session=session,
            source_media_path=source_media_path,
            time_range=time_range,
            override_borderline=override_borderline,
            confirm_inconsistency=confirm_inconsistency,
        )
        if not ok or pending is None:
            return False, msg, quality

        if not user_approved:
            verdict_str = quality.verdict if quality else "UNKNOWN"
            return (
                False,
                f"Sample verified ({verdict_str}). Explicit operator approval required before staging.",
                quality,
            )

        stage_ok, stage_msg = self.stage_sample(session, pending, user_approved=True)
        return stage_ok, stage_msg, quality

    def set_sample_approval(self, session: EnrollmentSession, sample_index: int, approved: bool) -> bool:
        """Sets user confirmation approval flag for a specific pending sample."""
        for s in session.samples:
            if s.sample_index == sample_index:
                s.approved_by_user = approved
                return True
        return False

    def remove_sample(self, session: EnrollmentSession, sample_index: int) -> bool:
        """Removes an imported sample from the in-memory staging session."""
        for i, s in enumerate(session.samples):
            if s.sample_index == sample_index:
                session.samples.pop(i)
                for idx, rem in enumerate(session.samples, start=1):
                    rem.sample_index = idx
                    rem.sample_name = f"sample_{idx}"
                return True
        return False

    def commit_enrollment(self, session: EnrollmentSession) -> Tuple[bool, str]:
        """
        Commits approved voice samples and identity into Vault v2:
        - Requires 3 to 5 approved samples.
        - Calculates normalized combined centroid embedding.
        - Encrypts reference WAV samples in Biometric Zone (Zone 2).
        - Encrypts biometric record with model name & version in Zone 2.
        - Encrypts identity record with consent in Identity Zone (Zone 1).
        - Appends to Keyed-MAC Audit Log.
        - Wipes plaintext audio buffers from memory.
        """
        approved_samples = [s for s in session.samples if s.approved_by_user]
        count = len(approved_samples)

        if count < MIN_ENROLLMENT_SAMPLES:
            return False, f"Enrollment requires at least {MIN_ENROLLMENT_SAMPLES} approved samples (currently approved: {count})."

        if count > MAX_ENROLLMENT_SAMPLES:
            return False, f"Maximum {MAX_ENROLLMENT_SAMPLES} samples allowed (currently approved: {count})."

        if not session.consent_noted:
            return False, "Security Invariant: Cannot commit profile without verified consent."

        if not self.vault.is_unlocked():
            return False, "Voice Vault must be unlocked to complete enrollment."

        spk_id = session.spk_id

        try:
            # 1. Compute combined normalized centroid embedding
            all_embs = [s.embedding for s in approved_samples]
            sum_emb = np.sum(all_embs, axis=0)
            norm = float(np.linalg.norm(sum_emb))
            combined_emb = (sum_emb / norm).astype(np.float32) if norm > 1e-9 else sum_emb.astype(np.float32)
            combined_bytes = combined_emb.tobytes()

            sample_names = [s.sample_name for s in approved_samples]

            # 2. Write Encrypted Reference WAVs to Biometric Zone (Zone 2)
            for s in approved_samples:
                self.storage.save_sample_audio(spk_id, s.sample_name, s.wav_bytes)

            # 3. Write Biometric Record to Biometric Zone (Zone 2) with Model Version
            model_name = getattr(self.embedding_engine, "model_name", "pyannote/wespeaker-voxceleb-resnet34-LM")
            model_ver = getattr(self.embedding_engine, "model_version", "1.0.0")
            model_rev = getattr(self.embedding_engine, "model_revision", "837717ddb9ff5507820346191109dc79c958d614")

            self.storage.create_biometric_record(
                spk_id=spk_id,
                embedding_bytes=combined_bytes,
                sample_names=sample_names,
                model=model_name,
                dimension=len(combined_emb),
                model_version=model_ver,
                model_revision=model_rev,
            )

            # 4. Write Identity Record to Identity Zone (Zone 1)
            self.storage.create_identity_record(
                full_name=session.full_name,
                spk_id=spk_id,
                roles=session.roles,
                notes=session.notes or "",
            )

            # Update identity record with explicit consent timestamp
            self.storage.update_identity_record(
                spk_id,
                {
                    "consent": {
                        "consent_noted": True,
                        "consent_date_utc": session.consent_date_utc,
                        "policy_version": "v2.0",
                    }
                },
            )

            # 5. Append Keyed-MAC Audit Log entries
            master_key = self.vault.get_master_key_bytes()
            if master_key:
                audit_log = self.vault.get_audit_log()
                audit_key = audit_log.derive_audit_key(master_key)

                # Record borderline sample overrides if any
                borderline_samples = [s for s in approved_samples if s.is_borderline_accepted]
                if borderline_samples:
                    audit_log.append_entry(
                        audit_key=audit_key,
                        action="BORDERLINE_SAMPLE_ACCEPTED",
                        target_id=spk_id,
                        details={
                            "borderline_count": len(borderline_samples),
                            "samples": [
                                {
                                    "name": s.sample_name,
                                    "reasons": s.quality_report.borderline_reasons,
                                    "snr_db": s.quality_report.snr_db,
                                    "speech_sec": s.quality_report.speech_duration_sec,
                                }
                                for s in borderline_samples
                            ],
                        },
                    )

                audit_log.append_entry(
                    audit_key=audit_key,
                    action="PROFILE_ENROLLED",
                    target_id=spk_id,
                    details={
                        "sample_count": count,
                        "total_speech_sec": round(session.total_speech_duration, 2),
                        "model": model_name,
                        "model_version": model_ver,
                    },
                )

            # 6. Secure in-memory buffer cleanup
            for s in session.samples:
                s.wav_bytes = b""
                s.audio_data = np.zeros(0, dtype=np.float32)

            logger.info("Successfully committed voice profile %s into Vault v2.", spk_id)
            return True, f"Voice profile enrolled successfully ({spk_id})."
        except Exception as e:
            logger.error("Failed to commit enrollment for %s: %s", spk_id, e)
            return False, f"Enrollment commitment failed: {e}"

    def re_embed_all_profiles(
        self,
        new_embedding_engine: Optional[LocalEmbeddingEngine] = None,
    ) -> Tuple[int, str]:
        """
        Re-computes embedding vectors for all enrolled profiles in the vault
        using their encrypted reference WAV samples.
        Zero unencrypted audio is written to disk.
        """
        if not self.vault.is_unlocked():
            return 0, "Voice Vault must be unlocked to re-embed profiles."

        engine = new_embedding_engine or self.embedding_engine
        bio_ids = self.storage.list_biometric_ids(include_quarantined=False)
        re_embedded_count = 0

        for spk_id in bio_ids:
            try:
                bio_meta, _ = self.storage.read_biometric_record(spk_id)
                sample_names = bio_meta.get("sample_names", [])
                if not sample_names:
                    continue

                new_embs = []
                for s_name in sample_names:
                    wav_bytes = self.storage.read_sample_audio(spk_id, s_name)
                    audio_data, sr = sf.read(io.BytesIO(wav_bytes))
                    if audio_data.ndim > 1:
                        audio_data = np.mean(audio_data, axis=1)
                    audio_data = audio_data.astype(np.float32)
                    emb = engine.compute_embedding(audio_data, sample_rate=sr)
                    new_embs.append(emb)

                if new_embs:
                    sum_emb = np.sum(new_embs, axis=0)
                    norm = float(np.linalg.norm(sum_emb))
                    combined_emb = (sum_emb / norm).astype(np.float32) if norm > 1e-9 else sum_emb.astype(np.float32)

                    model_name = getattr(engine, "model_name", "pyannote/wespeaker-voxceleb-resnet34-LM")
                    model_ver = getattr(engine, "model_version", "1.0.0")
                    model_rev = getattr(engine, "model_revision", "837717ddb9ff5507820346191109dc79c958d614")

                    self.storage.update_biometric_record(
                        spk_id,
                        updates={
                            "model": model_name,
                            "model_version": model_ver,
                            "model_revision": model_rev,
                            "dimension": len(combined_emb),
                        },
                        new_embedding_bytes=combined_emb.tobytes(),
                    )
                    re_embedded_count += 1
            except Exception as e:
                logger.error("Failed to re-embed profile %s: %s", spk_id, e)

        master_key = self.vault.get_master_key_bytes()
        if master_key and re_embedded_count > 0:
            audit_log = self.vault.get_audit_log()
            audit_key = audit_log.derive_audit_key(master_key)
            audit_log.append_entry(
                audit_key=audit_key,
                action="PROFILES_RE_EMBEDDED",
                target_id="ALL_PROFILES",
                details={
                    "count": re_embedded_count,
                    "model": getattr(engine, "model_name", "pyannote/wespeaker-voxceleb-resnet34-LM"),
                    "model_version": getattr(engine, "model_version", "1.0.0"),
                    "model_revision": getattr(engine, "model_revision", "837717ddb9ff5507820346191109dc79c958d614"),
                },
            )

        return re_embedded_count, f"Successfully re-embedded {re_embedded_count} profiles."
