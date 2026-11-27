"""
scribe_core/identification_engine.py

Phase V6 Voice Biometrics & Closed-Set Speaker Identification Engine.

Key Architectural Principles:
1. Closed-Set Matching: Matches strictly against the session roster (host first for panels;
   single speaker for solo).
2. Margin-Based Acceptance: Matches accepted only above match_threshold AND with a margin
   over the second-best candidate. Otherwise [Unidentified].
3. Cross-Clip Centroid Linking: Links clusters across the clips of a session by voice only
   (centroid cosine comparison). Never by order, gap, or timestamp.
4. Short-Turn & Audience Handling: Short turns (< 2.5s) are low-confidence. In a panel session,
   unmatched short-turn voices are labeled [Audience].
5. VRAM Mutual Exclusion: Whisper ASR and Pyannote Diarization/Embedding never share GPU memory.
   The ASR step receives no voice data or speaker identities.
6. Quarantine Exclusion: Quarantined voice profiles are strictly excluded and never matched.
7. Privacy Invariant: All outputs use sequential aliases only (S01, S02, ...), zero personal
   names or spk_ IDs on unencrypted media disk.
8. Re-identification without Re-transcription: Re-evaluates cached centroids against updated
   rosters without touching ASR text caches.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import soundfile as sf
import torch

from scribe_core.embedding_engine import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    LocalEmbeddingEngine,
    _VRAM_EXECUTION_LOCK,
    EMBEDDING_DIMENSION,
)
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.quarantine_manager import QuarantineManager
from scribe_core.multi_speaker import format_hhmmss_mmm

logger = logging.getLogger("scribe_core.identification")


@dataclass
class IdentificationConfig:
    """
    Configuration thresholds for speaker identification and clustering.
    Defaults are provisional until calibrated against real recorded audio.
    """
    match_threshold: float = 0.65            # Minimum cosine similarity to accept a match
    second_best_margin: float = 0.08         # Required margin: top_sim - second_sim >= margin
    short_turn_threshold_sec: float = 2.5     # Turns under 2.5s are considered short/low-confidence
    cross_clip_link_threshold: float = 0.70  # Centroid similarity to link clusters across clips
    min_snippet_duration_sec: float = 3.0    # Min duration for representative snippet
    max_snippet_duration_sec: float = 8.0    # Max duration for representative snippet


@dataclass
class AlignedSegment:
    """A segment of transcribed words aligned to a diarized speaker turn."""
    start: float
    end: float
    speaker_alias: str
    role: str
    text: str
    words: List[Dict[str, Any]] = field(default_factory=list)
    is_overlapping: bool = False
    overlapping_aliases: Optional[List[Tuple[str, str]]] = None
    confidence: float = 0.0
    cluster_id: str = "cluster_unknown"

    def format_line(self, is_multi_speaker: bool = False) -> str:
        """Format segment as standard transcript line."""
        ts_str = f"[{format_hhmmss_mmm(self.start)} - {format_hhmmss_mmm(self.end)}]"
        cleaned = self.text.strip()

        if self.is_overlapping:
            if self.overlapping_aliases:
                parts = [f"{a} ({r})" if r else a for a, r in self.overlapping_aliases]
                return f"[Overlapping: {', '.join(parts)}] {ts_str} {cleaned}"
            return f"[Overlapping] {ts_str} {cleaned}"

        if not is_multi_speaker:
            lbl = self.speaker_alias if self.speaker_alias else "Speaker"
            return f"[{lbl}] {ts_str} {cleaned}"

        if self.speaker_alias in ("[Unidentified]", "Unidentified"):
            return f"[Unidentified] {ts_str} {cleaned}"

        if self.speaker_alias in ("[Audience]", "Audience"):
            return f"[Audience] {ts_str} {cleaned}"

        role_str = f" ({self.role})" if self.role else ""
        return f"[{self.speaker_alias}{role_str}] {ts_str} {cleaned}"


@dataclass
class SpeakerCluster:
    """An acoustic speaker cluster aggregated across turns and clips."""
    cluster_id: str
    clip_names: List[str] = field(default_factory=list)
    turn_intervals: List[Tuple[float, float]] = field(default_factory=list)
    centroid: Optional[np.ndarray] = None
    sample_count: int = 0
    matched_alias: str = "[Unidentified]"
    matched_role: str = ""
    matched_spk_id: Optional[str] = None
    confidence: float = 0.0
    second_best_confidence: float = 0.0
    is_audience: bool = False
    is_unidentified: bool = True
    representative_snippet: Optional[bytes] = None
    snippet_time_range: Optional[Tuple[float, float]] = None


class IdentificationEngine:
    """
    Orchestrates diarization alignment, cross-clip centroid linking, closed-set
    roster matching, and in-memory snippet extraction with VRAM safety.
    """

    def __init__(
        self,
        vault_storage: Optional[VaultV2StorageEngine] = None,
        quarantine_mgr: Optional[QuarantineManager] = None,
        embedding_engine: Optional[LocalEmbeddingEngine] = None,
        config: Optional[IdentificationConfig] = None,
    ):
        self.vault_storage = vault_storage
        self.quarantine_mgr = quarantine_mgr
        self.embedding_engine = embedding_engine or LocalEmbeddingEngine(prefer_gpu=False)
        self.config = config or IdentificationConfig()

    def align_words_to_turns(
        self,
        words: List[Dict[str, Any]],
        turns: List[Dict[str, Any]],
    ) -> List[AlignedSegment]:
        """
        Aligns transcribed words to raw diarized turns.
        The ASR step that produced the words received zero voice or identity data.
        """
        if not words:
            return []

        if not turns:
            # Fallback if diarization found no turns: single unidentified segment
            text = " ".join(w.get("word", "").strip() for w in words)
            start = float(words[0].get("start", 0.0))
            end = float(words[-1].get("end", 0.0))
            return [
                AlignedSegment(
                    start=start,
                    end=end,
                    speaker_alias="[Unidentified]",
                    role="",
                    text=text,
                    words=words,
                    confidence=0.0,
                    cluster_id="cluster_0",
                )
            ]

        # 1. Assign turn(s) to each word based on timestamp midpoint
        assigned_words = []
        for w in words:
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", w_start + 0.1))
            w_mid = (w_start + w_end) / 2.0

            overlapping_turns = [
                t for t in turns
                if float(t.get("start", 0.0)) <= w_mid <= float(t.get("end", 0.0))
            ]

            is_overlap = len(overlapping_turns) > 1
            primary_turn = overlapping_turns[0] if overlapping_turns else turns[0]
            cluster_id = primary_turn.get("speaker", "SPEAKER_00")

            assigned_words.append({
                "start": w_start,
                "end": w_end,
                "word": w.get("word", "").strip(),
                "cluster_id": cluster_id,
                "is_overlapping": is_overlap,
                "overlapping_clusters": [t.get("speaker", "") for t in overlapping_turns] if is_overlap else None,
            })

        # 2. Group adjacent words by (cluster_id, is_overlapping)
        segments: List[AlignedSegment] = []
        current_cluster = None
        current_overlap = False
        cur_words = []
        cur_start = 0.0
        cur_end = 0.0
        cur_overlapping_clusters = None

        for item in assigned_words:
            c_id = item["cluster_id"]
            ov = item["is_overlapping"]

            if current_cluster is None or (c_id != current_cluster or ov != current_overlap):
                if cur_words:
                    segments.append(
                        AlignedSegment(
                            start=cur_start,
                            end=cur_end,
                            speaker_alias="[Unidentified]",
                            role="",
                            text=" ".join(w["word"] for w in cur_words),
                            words=cur_words,
                            is_overlapping=current_overlap,
                            overlapping_aliases=[(c, "") for c in cur_overlapping_clusters] if cur_overlapping_clusters else None,
                            cluster_id=current_cluster,
                        )
                    )
                current_cluster = c_id
                current_overlap = ov
                cur_words = [item]
                cur_start = item["start"]
                cur_end = item["end"]
                cur_overlapping_clusters = item["overlapping_clusters"]
            else:
                cur_words.append(item)
                cur_end = item["end"]

        if cur_words:
            segments.append(
                AlignedSegment(
                    start=cur_start,
                    end=cur_end,
                    speaker_alias="[Unidentified]",
                    role="",
                    text=" ".join(w["word"] for w in cur_words),
                    words=cur_words,
                    is_overlapping=current_overlap,
                    overlapping_aliases=[(c, "") for c in cur_overlapping_clusters] if cur_overlapping_clusters else None,
                    cluster_id=current_cluster,
                )
            )

        return segments

    def filter_short_turns(
        self,
        segments: List[AlignedSegment],
        is_panel: bool = False,
    ) -> List[AlignedSegment]:
        """
        Processes short turns (< short_turn_threshold_sec, e.g. 2.5s):
        - Merges contiguous short turns with adjacent turns from the same speaker.
        - Unmerged short turns become low-confidence:
          In a panel, unmatched short turns are candidates for [Audience].
          Otherwise marked [Unidentified].
        """
        if not segments:
            return []

        merged: List[AlignedSegment] = []
        for seg in segments:
            duration = seg.end - seg.start
            if merged and merged[-1].cluster_id == seg.cluster_id and not seg.is_overlapping and not merged[-1].is_overlapping:
                # Merge contiguous speech from same cluster if gap is small (< 1.0s)
                if (seg.start - merged[-1].end) < 1.0:
                    merged[-1].end = seg.end
                    merged[-1].text = (merged[-1].text + " " + seg.text).strip()
                    merged[-1].words.extend(seg.words)
                    continue

            # Flag short turn
            if duration < self.config.short_turn_threshold_sec:
                if is_panel:
                    # In panel, short interjections from non-roster speakers default to Audience
                    seg.role = "Audience"
                    seg.speaker_alias = "[Audience]"
                    seg.confidence = 0.50
                else:
                    seg.speaker_alias = "[Unidentified]"
                    seg.confidence = 0.0

            merged.append(seg)

        return merged

    def find_representative_snippet_range(
        self,
        turn_intervals: List[Tuple[float, float]],
    ) -> Optional[Tuple[float, float]]:
        """Finds start and end times (seconds) for representative speech snippet (3.0s to 8.0s)."""
        if not turn_intervals:
            return None
        sorted_turns = sorted(turn_intervals, key=lambda t: t[1] - t[0], reverse=True)
        for s_time, e_time in sorted_turns:
            dur = e_time - s_time
            if dur >= self.config.min_snippet_duration_sec:
                target_dur = min(dur, self.config.max_snippet_duration_sec)
                return (round(float(s_time), 3), round(float(s_time + target_dur), 3))
        longest = sorted_turns[0]
        return (round(float(longest[0]), 3), round(float(longest[1]), 3))

    def compute_cluster_centroids(
        self,
        audio_data: np.ndarray,
        segments: List[AlignedSegment],
        sample_rate: int = 16000,
        clip_name: str = "",
    ) -> Dict[str, SpeakerCluster]:
        """
        Computes normalized acoustic centroid embeddings for each speaker cluster in a clip.
        Operates strictly in memory under VRAM lock.
        """
        clusters: Dict[str, List[np.ndarray]] = {}
        intervals: Dict[str, List[Tuple[float, float]]] = {}

        for seg in segments:
            if seg.is_overlapping:
                continue
            dur = seg.end - seg.start
            if dur < 1.0:
                continue

            start_idx = int(seg.start * sample_rate)
            end_idx = int(seg.end * sample_rate)
            slice_data = audio_data[start_idx:end_idx]

            if len(slice_data) < sample_rate:
                continue

            # Compute embedding vector
            emb = self.embedding_engine.compute_embedding(
                audio_data=slice_data,
                sample_rate=sample_rate,
                release_after=True,
            )
            clusters.setdefault(seg.cluster_id, []).append(emb)
            intervals.setdefault(seg.cluster_id, []).append((seg.start, seg.end))

        result: Dict[str, SpeakerCluster] = {}
        for c_id, embs in clusters.items():
            arr = np.vstack(embs)
            centroid = np.mean(arr, axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 1e-8:
                centroid = centroid / norm

            # Extract representative snippet (3-8 seconds)
            snippet = self.extract_representative_snippet(
                audio_data=audio_data,
                turn_intervals=intervals.get(c_id, []),
                sample_rate=sample_rate,
            )
            snip_range = self.find_representative_snippet_range(intervals.get(c_id, []))

            result[c_id] = SpeakerCluster(
                cluster_id=c_id,
                clip_names=[clip_name] if clip_name else [],
                turn_intervals=intervals.get(c_id, []),
                centroid=centroid,
                sample_count=len(embs),
                representative_snippet=snippet,
                snippet_time_range=snip_range,
            )

        return result

    def link_clusters_across_clips(
        self,
        clip_clusters: List[Dict[str, SpeakerCluster]],
    ) -> List[SpeakerCluster]:
        """
        Links speaker clusters across all clips of a session:
        - Compares centroids by acoustic cosine similarity ONLY.
        - NEVER links by chronological order, time gaps, or timestamps.
        - Merges linked clusters if similarity >= cross_clip_link_threshold.
        """
        session_clusters: List[SpeakerCluster] = []

        for clip_dict in clip_clusters:
            for c_id, cluster in clip_dict.items():
                if cluster.centroid is None:
                    continue

                best_match = None
                best_sim = -1.0

                for existing in session_clusters:
                    if existing.centroid is None:
                        continue
                    sim = float(np.dot(existing.centroid, cluster.centroid))
                    if sim > best_sim:
                        best_sim = sim
                        best_match = existing

                if best_match and best_sim >= self.config.cross_clip_link_threshold:
                    # Link cluster into existing session cluster
                    # Update combined centroid
                    total_samples = best_match.sample_count + cluster.sample_count
                    w1 = best_match.sample_count / total_samples
                    w2 = cluster.sample_count / total_samples
                    new_centroid = w1 * best_match.centroid + w2 * cluster.centroid
                    norm = float(np.linalg.norm(new_centroid))
                    if norm > 1e-8:
                        new_centroid = new_centroid / norm
                    best_match.centroid = new_centroid
                    best_match.sample_count = total_samples
                    best_match.clip_names.extend(cluster.clip_names)
                    best_match.turn_intervals.extend(cluster.turn_intervals)
                    if not best_match.representative_snippet and cluster.representative_snippet:
                        best_match.representative_snippet = cluster.representative_snippet
                    if not best_match.snippet_time_range and cluster.snippet_time_range:
                        best_match.snippet_time_range = cluster.snippet_time_range
                else:
                    # New distinct session cluster
                    session_clusters.append(cluster)

        return session_clusters

    def match_roster(
        self,
        clusters: List[SpeakerCluster],
        session_roster: List[Dict[str, Any]],
        is_solo: bool = False,
    ) -> None:
        """
        Matches clusters strictly against the session roster (closed set).
        - Solo sessions expect one speaker.
        - Panel sessions prioritize Host first.
        - Accepts match only if similarity >= match_threshold AND margin over second-best >= second_best_margin.
        - Quarantined profiles are strictly excluded and never matched.
        - All assignments use aliases (S01, S02, ...) only.
        """
        if not clusters or not session_roster or not self.vault_storage:
            return

        # 1. Filter out quarantined profiles from the candidate set
        eligible_candidates = []
        for m in session_roster:
            spk_id = m.get("spk_id")
            if not spk_id:
                # If spk_id is missing, look it up by alias or name if available
                continue
            if self.quarantine_mgr and self.quarantine_mgr.is_quarantined(spk_id):
                logger.warning("Profile %s is quarantined; strictly excluded from matching.", spk_id)
                continue

            # Load enrolled biometric embedding from Vault v2
            try:
                bio_meta, emb_bytes = self.vault_storage.read_biometric_record(spk_id)
            except Exception as e:
                logger.warning("Could not load biometric record for %s: %s", spk_id, e)
                continue

            if not emb_bytes:
                continue

            enrolled_emb = np.frombuffer(emb_bytes, dtype=np.float32)
            norm = float(np.linalg.norm(enrolled_emb))
            if norm > 1e-8:
                enrolled_emb = enrolled_emb / norm

            eligible_candidates.append({
                "alias": m.get("alias", "S01"),
                "role": m.get("role", "Speaker"),
                "spk_id": spk_id,
                "embedding": enrolled_emb,
            })

        if not eligible_candidates:
            for c in clusters:
                c.matched_alias = "[Unidentified]"
                c.is_unidentified = True
            return

        # 2. Solo Session Matching: exactly 1 expected speaker
        if is_solo:
            expected = eligible_candidates[0]
            for c in clusters:
                if c.centroid is None:
                    continue
                sim = float(np.dot(c.centroid, expected["embedding"]))
                if sim >= self.config.match_threshold:
                    c.matched_alias = expected["alias"]
                    c.matched_role = expected["role"]
                    c.matched_spk_id = expected["spk_id"]
                    c.confidence = sim
                    c.is_unidentified = False
                else:
                    c.matched_alias = "[Unidentified]"
                    c.is_unidentified = True
                    c.confidence = sim
            return

        # 3. Panel Session Matching: Closed set, prioritize Host first
        # Sort candidates so Host is evaluated first
        sorted_candidates = sorted(
            eligible_candidates,
            key=lambda m: 0 if m.get("role") == "Host" else 1
        )

        for c in clusters:
            if c.centroid is None:
                continue

            scores = []
            for cand in sorted_candidates:
                sim = float(np.dot(c.centroid, cand["embedding"]))
                scores.append((sim, cand))

            scores.sort(key=lambda x: x[0], reverse=True)
            top_sim, top_cand = scores[0]
            second_sim = scores[1][0] if len(scores) > 1 else 0.0

            margin = top_sim - second_sim

            if top_sim >= self.config.match_threshold and margin >= self.config.second_best_margin:
                c.matched_alias = top_cand["alias"]
                c.matched_role = top_cand["role"]
                c.matched_spk_id = top_cand["spk_id"]
                c.confidence = top_sim
                c.second_best_confidence = second_sim
                c.is_unidentified = False
            else:
                # Ambiguous or sub-threshold
                c.confidence = top_sim
                c.second_best_confidence = second_sim
                if c.is_audience:
                    c.matched_alias = "[Audience]"
                    c.matched_role = "Audience"
                else:
                    c.matched_alias = "[Unidentified]"
                    c.matched_role = ""
                c.is_unidentified = True

    def apply_attributions_to_segments(
        self,
        segments: List[AlignedSegment],
        clusters: Dict[str, SpeakerCluster],
        is_multi_speaker: bool = False,
    ) -> None:
        """Applies cluster identification results to word segments."""
        for seg in segments:
            cluster = clusters.get(seg.cluster_id)
            if not cluster:
                continue

            seg.speaker_alias = cluster.matched_alias
            seg.role = cluster.matched_role
            seg.confidence = cluster.confidence

            # If segment is overlapping, format overlapping list
            if seg.is_overlapping and seg.overlapping_aliases:
                resolved_overlap = []
                for raw_c, _ in seg.overlapping_aliases:
                    cl = clusters.get(raw_c)
                    if cl and not cl.is_unidentified:
                        resolved_overlap.append((cl.matched_alias, cl.matched_role))
                    else:
                        resolved_overlap.append(("[Unidentified]", ""))
                seg.overlapping_aliases = resolved_overlap

    def extract_representative_snippet(
        self,
        audio_data: np.ndarray,
        turn_intervals: List[Tuple[float, float]],
        sample_rate: int = 16000,
    ) -> Optional[bytes]:
        """
        Extracts a clean representative speech snippet (3.0s to 8.0s) strictly in memory.
        Returns mono 16kHz WAV bytes. Zero unencrypted audio touches disk.
        """
        if not turn_intervals or len(audio_data) == 0:
            return None

        # Sort intervals by duration descending to find the cleanest sustained speech
        sorted_turns = sorted(turn_intervals, key=lambda t: t[1] - t[0], reverse=True)

        for start_s, end_s in sorted_turns:
            dur = end_s - start_s
            if dur >= self.config.min_snippet_duration_sec:
                target_dur = min(dur, self.config.max_snippet_duration_sec)
                start_idx = int(start_s * sample_rate)
                end_idx = int((start_s + target_dur) * sample_rate)
                slice_pcm = audio_data[start_idx:end_idx]

                buf = io.BytesIO()
                sf.write(buf, slice_pcm, sample_rate, format="WAV", subtype="PCM_16")
                return buf.getvalue()

        # Fallback: concatenate shortest turns up to min_snippet_duration_sec
        concat_slices = []
        accum_dur = 0.0
        for start_s, end_s in turn_intervals:
            s_idx = int(start_s * sample_rate)
            e_idx = int(end_s * sample_rate)
            concat_slices.append(audio_data[s_idx:e_idx])
            accum_dur += (end_s - start_s)
            if accum_dur >= self.config.min_snippet_duration_sec:
                break

        if concat_slices:
            combined = np.concatenate(concat_slices)
            buf = io.BytesIO()
            sf.write(buf, combined, sample_rate, format="WAV", subtype="PCM_16")
            return buf.getvalue()

        return None

    def reidentify_session(
        self,
        cached_segments: List[AlignedSegment],
        session_clusters: Dict[str, SpeakerCluster],
        updated_roster: List[Dict[str, Any]],
        is_solo: bool = False,
    ) -> List[AlignedSegment]:
        """
        Re-evaluates speaker attributions using cached acoustic centroids
        against an updated roster (e.g. after enrolling an unknown speaker).
        Requires ZERO re-transcription.
        """
        cluster_list = list(session_clusters.values())
        self.match_roster(cluster_list, updated_roster, is_solo=is_solo)
        self.apply_attributions_to_segments(cached_segments, session_clusters, is_multi_speaker=not is_solo)
        return cached_segments


# ---------------------------------------------------------------------------
# Persistent Cluster Storage (scribe\state\clusters\<clip>.json)
# ---------------------------------------------------------------------------


def save_clip_clusters_to_disk(
    state_dir: str,
    clip_name: str,
    segments: List[Any],
    clusters: Dict[str, SpeakerCluster],
) -> str:
    """
    Saves diarization segments, acoustic cluster centroids, and snippet time ranges
    to <state_dir>\\clusters\\<clip_stem>.json.

    Invariants:
    1. Zero personal names or identity records are ever stored.
    2. Zero audio data or audio arrays are stored (time ranges only).
    3. Strictly stores anonymous cluster IDs, turn intervals, snippet time ranges, and centroid vectors.
    """
    stem, _ = os.path.splitext(clip_name)
    clusters_dir = os.path.join(state_dir, "clusters")
    os.makedirs(clusters_dir, exist_ok=True)
    out_path = os.path.join(clusters_dir, f"{stem}.json")

    # Serialize anonymous segments (NO personal names)
    clean_segments = []
    for s in segments:
        if isinstance(s, AlignedSegment):
            clean_segments.append({
                "start": round(float(s.start), 3),
                "end": round(float(s.end), 3),
                "cluster_id": s.cluster_id,
                "is_overlapping": bool(s.is_overlapping),
            })
        elif isinstance(s, dict):
            clean_segments.append({
                "start": round(float(s.get("start", 0.0)), 3),
                "end": round(float(s.get("end", 0.0)), 3),
                "cluster_id": str(s.get("speaker", s.get("cluster_id", "cluster_0"))),
                "is_overlapping": bool(s.get("is_overlapping", False)),
            })

    # Serialize clusters
    clusters_data = {}
    for c_id, c in clusters.items():
        centroid_list = c.centroid.tolist() if c.centroid is not None else []
        turn_intervals_list = [[round(float(t[0]), 3), round(float(t[1]), 3)] for t in c.turn_intervals]
        snip_range = [round(float(c.snippet_time_range[0]), 3), round(float(c.snippet_time_range[1]), 3)] if c.snippet_time_range else None

        clusters_data[c_id] = {
            "cluster_id": c.cluster_id,
            "centroid": centroid_list,
            "sample_count": int(c.sample_count),
            "turn_intervals": turn_intervals_list,
            "snippet_time_range": snip_range,
        }

    payload = {
        "clip_name": clip_name,
        "version": "1.0",
        "embedding_model": DEFAULT_EMBEDDING_MODEL_NAME,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "segments": clean_segments,
        "clusters": clusters_data,
    }

    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, out_path)
    logger.info("Persisted %d cluster centroids for %s to %s", len(clusters_data), clip_name, out_path)
    return out_path


def load_clip_clusters_from_disk(clusters_dir: str, clip_name: str) -> Optional[Dict[str, Any]]:
    """Loads persisted cluster data for a clip from <clusters_dir>\\<clip>.json."""
    if not os.path.isdir(clusters_dir):
        return None
    stem, _ = os.path.splitext(clip_name)
    cand_paths = [
        os.path.join(clusters_dir, f"{stem}.json"),
        os.path.join(clusters_dir, f"{clip_name}.json"),
    ]
    for p in cand_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not read cluster JSON %s: %s", p, e)
    return None


def load_all_event_clusters(event_root: str) -> Dict[str, SpeakerCluster]:
    """
    Loads and links all persisted speaker clusters from <event_root>\\scribe\\state\\clusters\\.
    Returns dict mapping cluster_id to SpeakerCluster with reconstructed centroids and snippet time ranges.
    """
    clusters_dir = os.path.join(event_root, "scribe", "state", "clusters")
    if not os.path.isdir(clusters_dir):
        return {}

    clip_clusters_list: List[Dict[str, SpeakerCluster]] = []
    for fname in sorted(os.listdir(clusters_dir)):
        if not fname.endswith(".json"):
            continue
        p = os.path.join(clusters_dir, fname)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            clip_name = data.get("clip_name", fname)
            clusters_dict = data.get("clusters", {})
            current_clip_clusters: Dict[str, SpeakerCluster] = {}
            for c_id, c_info in clusters_dict.items():
                centroid_list = c_info.get("centroid", [])
                centroid = np.array(centroid_list, dtype=np.float32) if centroid_list else None
                turn_intervals = [tuple(t) for t in c_info.get("turn_intervals", [])]
                snip_range = tuple(c_info.get("snippet_time_range")) if c_info.get("snippet_time_range") else None
                current_clip_clusters[c_id] = SpeakerCluster(
                    cluster_id=c_id,
                    clip_names=[clip_name],
                    turn_intervals=turn_intervals,
                    centroid=centroid,
                    sample_count=c_info.get("sample_count", len(turn_intervals)),
                    matched_alias="[Unidentified]",
                    is_unidentified=True,
                    snippet_time_range=snip_range,
                )
            if current_clip_clusters:
                clip_clusters_list.append(current_clip_clusters)
        except Exception as e:
            logger.warning("Error loading cluster file %s: %s", p, e)

    if not clip_clusters_list:
        return {}

    # Link clusters across clips using IdentificationEngine
    engine = IdentificationEngine()
    linked_clusters = engine.link_clusters_across_clips(clip_clusters_list)
    return {c.cluster_id: c for c in linked_clusters}


def extract_and_save_diarized_clusters(
    wav_path: str,
    turns: List[Dict[str, Any]],
    diarized_segments: List[Dict[str, Any]],
    state_dir: str,
    clip_name: str,
    embedding_engine: Optional[LocalEmbeddingEngine] = None,
) -> Optional[str]:
    """
    Given a diarized audio file and turns, calculates acoustic centroids and snippet
    time ranges, saving to <state_dir>\\clusters\\<clip>.json.
    Operates strictly in memory; zero audio is saved to disk.
    """
    if not os.path.isfile(wav_path) or not turns:
        return None

    try:
        data, sr = sf.read(wav_path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)

        engine = embedding_engine or LocalEmbeddingEngine(prefer_gpu=False)

        # Group turns by cluster label
        cluster_slices: Dict[str, List[np.ndarray]] = {}
        cluster_intervals: Dict[str, List[Tuple[float, float]]] = {}

        for t in turns:
            spk = t.get("speaker", "SPEAKER_00")
            start_s = float(t.get("start", 0.0))
            end_s = float(t.get("end", 0.0))
            dur = end_s - start_s
            cluster_intervals.setdefault(spk, []).append((start_s, end_s))

            if dur >= 0.8:
                start_idx = int(start_s * sr)
                end_idx = int(end_s * sr)
                if end_idx <= len(data):
                    slice_pcm = data[start_idx:end_idx]
                    if len(slice_pcm) >= int(0.5 * sr):
                        emb = engine.compute_embedding(slice_pcm, sample_rate=sr, release_after=True)
                        cluster_slices.setdefault(spk, []).append(emb)

        ident_engine = IdentificationEngine(embedding_engine=engine)
        clusters_to_save: Dict[str, SpeakerCluster] = {}

        for c_id, embs in cluster_slices.items():
            arr = np.vstack(embs)
            centroid = np.mean(arr, axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 1e-8:
                centroid = centroid / norm

            intervals = cluster_intervals.get(c_id, [])
            snip_range = ident_engine.find_representative_snippet_range(intervals)

            clusters_to_save[c_id] = SpeakerCluster(
                cluster_id=c_id,
                clip_names=[clip_name],
                turn_intervals=intervals,
                centroid=centroid,
                sample_count=len(embs),
                snippet_time_range=snip_range,
                matched_alias="[Unidentified]",
                is_unidentified=True,
            )

        # In case some cluster had no slices >= 0.8s, still register with intervals
        for c_id, intervals in cluster_intervals.items():
            if c_id not in clusters_to_save:
                snip_range = ident_engine.find_representative_snippet_range(intervals)
                clusters_to_save[c_id] = SpeakerCluster(
                    cluster_id=c_id,
                    clip_names=[clip_name],
                    turn_intervals=intervals,
                    centroid=None,
                    sample_count=0,
                    snippet_time_range=snip_range,
                    matched_alias="[Unidentified]",
                    is_unidentified=True,
                )

        return save_clip_clusters_to_disk(
            state_dir=state_dir,
            clip_name=clip_name,
            segments=diarized_segments,
            clusters=clusters_to_save,
        )
    except Exception as e:
        logger.warning("Could not extract and save clusters for %s: %s", clip_name, e)
        return None

