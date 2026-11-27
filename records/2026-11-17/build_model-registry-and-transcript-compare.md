# Work Record: Model Registry, Re-transcribe & Transcript Comparison (Part D)

- **Date:** 2026-11-17
- **What was asked:**
  - Implement Part D of the transcription engine and UI specification:
    1. **Model Registry:**
       - Local Whisper models that fit 4 GB GPU VRAM (`whisper-tiny`, `whisper-base`, `whisper-small`, `whisper-medium`).
       - Cloud engines (`nvidia/parakeet-ctc-1.1b` active, `openai/whisper-large-v3` active, `nvidia/nemotron-speech` disabled template).
       - Rate-limit duration warnings and cost estimation.
    2. **Replace vs Compare:**
       - "Re-transcribe selected" modal dialog providing explicit Replace (overwrite with prior archiving) and Compare (multi-run history preservation) modes.
    3. **Stage 3 Display for Compare:**
       - Expandable child rows under clip items showing model name, duration, execution time, speedup ratio, Word Error Rate (WER) / diff vs baseline, and a "Make Primary" action.
    4. **Drawer / Detail View:**
       - Bottom split comparison drawer when a multi-run clip is selected, displaying side-by-side transcripts with diff metrics, speedup comparison, latency delta, and a promote-to-primary button.
    5. **Usage & Rate Limits:**
       - Each run logs model, provider, tokens/seconds, speedup, and cost estimate to the usage ledger. Warns if cloud clip length exceeds recommended thresholds.
    6. **Export:**
       - "Export comparison" button generating an authentic Microsoft Excel report (`.xlsx`) via pure-Python zipfile/XML (zero external dependencies) and a companion UTF-8 with BOM `.csv`.
- **Why:**
  - Enables conference organizers and content teams to evaluate alternative transcription models for challenging clips, compare accuracy against baseline models, benchmark cloud speedup vs. local privacy, and promote preferred versions without manual file copying or losing version history.
- **What was built (user-visible behaviour):**
  - **`ReTranscribeDialog`:** Accessible via "Re-transcribe…" in the review bar or right-click context menu. Offers radio selection between Compare and Replace modes, categorized model selection with VRAM footprints, live cost estimates, and threshold warnings (e.g., >10 min clips on Parakeet).
  - **Expandable Table Hierarchy:** Clips with comparison runs display an indicator `▼ <clip_name> (N runs)` with indented child rows `↳ #1`, `↳ #2` showing model, speedup, WER vs. baseline, and a clickable `[Make Primary]` action.
  - **Comparison Drawer:** Displays side-by-side texts for Run 1 vs Run 2 with metrics banner (`WER: X.X%`, `Char Diff: X.X%`, `Latency Δ: ±Xs`, speedup comparison) and a "Make Comparison Run Primary" button.
  - **Excel & CSV Export:** "Export comparison…" generates a multi-sheet `.xlsx` file (Sheet 1: Run Metrics, Sheet 2: Side-by-Side Comparison) and companion UTF-8 BOM CSV saved to `scribe/reports/`.
  - **Usage Ledger Logging:** All re-transcription runs append sanitized attempt records to `scribe/state/usage/ledger.jsonl`.
- **How to use it:**
  1. Select one or more clips in the Stage 3 table.
  2. Click "Re-transcribe…" or right-click → "Re-transcribe with Model Choice…".
  3. Select **Compare (Recommended)** or **Replace**, choose a model (e.g. Parakeet or Whisper Small), and click "Start Re-transcribe".
  4. View the child rows and side-by-side comparison in the bottom drawer. Click "Make Comparison Run Primary" to promote it.
  5. Click "Export comparison…" to generate the Excel report in `scribe/reports/`.
- **Settings and defaults:**
  - Default execution mode: `compare`.
  - Default local model: `whisper-medium` (~3.5 GB VRAM).
  - Disabled preview template: `nvidia/nemotron-speech` ("Coming Soon on build.nvidia.com").
- **Files changed:**
  - `scribe_core/model_registry.py` (New: catalog, VRAM specs, rate limits, cost estimator)
  - `scribe_core/run_comparison.py` (New: WER calculation, run persistence, pure-python XLSX & CSV generator)
  - `gui/theme.py` (Added `open()` and `close()` to `CollapsibleSection`)
  - `gui/stage3_tab.py` (Added `ReTranscribeDialog`, child run rows, comparison drawer, export handler)
  - `gui/qt/stage3_view.py` (Added Comparison tab to activity drawer, promote primary, and export actions)
  - `tests/test_model_registry_and_compare.py` (New: 12 unit and integration tests)
  - `README.md` (Updated with Model Registry, Compare drawer, and export capabilities)
  - `CHANGELOG.md`
  - `records/registers/FEATURES.md`
  - `records/2026-11-17/INDEX.md`
- **Limitations and known issues:**
  - Nemotron Speech Transcribe 3.5 is cataloged as a disabled preview template until build.nvidia.com enables the public endpoint.
- **How it was verified:**
  - Ran `tests/test_model_registry_and_compare.py`: 12/12 tests passed (testing VRAM limits, rate limit warnings, cost estimation, Levenshtein WER, Replace vs Compare modes, primary promotion, pure-python XLSX packaging, and GUI drawer integration).
  - Secret scan executed via `scripts/secret_scan.py`: 0 secrets found.
- **How to roll it back:**
  - Revert `gui/stage3_tab.py`, `gui/qt/stage3_view.py`, and remove `scribe_core/model_registry.py` and `scribe_core/run_comparison.py`.

