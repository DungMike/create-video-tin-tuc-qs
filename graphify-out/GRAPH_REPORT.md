# Graph Report - create-video-tin-tuc-qs  (2026-10-07)

## Corpus Check
- 175 files · ~236,314 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 34 file(s) not represented in the graph (top: .ttf 9, .csv 7, .log 6)

## Summary
- 3554 nodes · 10506 edges · 128 communities (104 shown, 24 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 435 edges (avg confidence: 0.92)
- Token cost: 107,977 input · 0 output

## Community Hubs (Navigation)
- Story Video Routes Core
- Frontend API Types
- Edit Styles Page UI
- Story Library Manager UI
- Story Video Page UI
- Story Video Settings UI
- TTS Audio Generation
- Bulk Clip Harvest
- Google Drive Audio Import
- Harvest & Prefetch Panels
- Light FX Effects
- Hardware Benchmark Probes
- CUDA Overlay Benchmark
- Project Docs & Design
- Edit Style Assets
- Edit Style Kinds Registry
- Batch Progress Persistence
- Image & Keyword Repo
- Clip Canonicalization
- EditPlan Runtime
- Video Prefetch Sessions
- Story Library Registry
- Library Normalize & Spec
- Layout Kinds Common
- CTA Overlay Management
- Story Overlay Application
- Clip Usage Ledger
- Image Clip Jobs
- Video Source Downloader
- Subtitle Styles & Presets
- Route Background Jobs
- YouTube Download Page
- Pipeline Overlay Commands
- FFmpeg Expression Graph
- Effect Preview Rendering
- Library Bake Runner
- Benchmark Reports & Backfill
- Image Clip Panel & Library Hook
- Chapter & Quote Kinds
- Pexels Key Pool
- CRT TV Effect Processor
- Edit Style Param Spec
- Config & Overlay Packs
- ASS Subtitle Build Tests
- TV Noise Overlays
- YouTube Downloader
- Edit Style Store
- Font Registry & Scan
- Sparkle Presets
- Clip Usage Mode & DB Writes
- Clip Selection & Audio Prep
- Image Provider Search
- Render Bench Monitor
- Clip Identity
- File Storage Manager
- Batch Queue Runner
- Frontend Lint Tooling
- FFmpeg Helper & NVENC
- Image Clip Routes
- Intro Library
- Karaoke Subtitle Events
- MongoDB Connection
- Keyword Sweep Tests
- Story Create & Decor Preview
- Story Cancel & Audio Utils
- Decor Image Index
- Waveform Overlays
- TS App Config
- Two-Layer Kind
- Overlay Placement Editor
- Logging & Render Priority
- Decor Settings
- Prefetch Download Loop
- Image Clip Commit
- Web App & Samples
- Image Clip Effects Rotation
- Library Resolution
- TS Node Config
- Library Index Storage
- Subtitle Segment Rebase
- shadcn Components Config
- Frontend Dependencies
- Bake Clip Appending
- Library Delete Tests
- Frontend Dev Dependencies
- Media Repo
- Decor Image Import
- Decor Image Search
- Root Package Scripts
- Style Rotation Dealing
- Decor Fit Geometry
- ASS Colour Conversion
- Keyword Repo
- Decor Green-Frame Detect
- Subtitle Text Measuring
- Subtitle Resegmentation
- Edit Style Preview
- Decor Border Styling
- Chapter Parsing
- Magazine Kind
- Decor Image Preprocess
- Album Kind
- DocStrip Kind
- Shared Clip Bag
- Batch Queue Status
- Dossier Kind
- Newsroom Kind
- SRT Parsing
- Background DB Writer
- TS Root Config
- Frontend Scripts
- Clip Usage Summary
- Clip Stills Extraction
- Render Resource Priority
- Vite Config
- Local Audio Folder Scan
- Decor File Removal
- Subtitle ASS Preparation
- Harvest Cancel Signal
- ASS Filter Path
- Grapheme Clusters
- Ordered Pick Fixture
- Unmeasurable Font Fixture
- Tailwind Config
- Image Clip Thread Wait

## God Nodes (most connected - your core abstractions)
1. `Config` - 276 edges
2. `requestJson()` - 130 edges
3. `_error()` - 115 edges
4. `StoryVideoPage()` - 77 edges
5. `FFmpegHelper` - 74 edges
6. `StoryVideoSettingsPage()` - 71 edges
7. `StoryVideoPipelineRunner` - 68 edges
8. `Button` - 57 edges
9. `cn()` - 53 edges
10. `EditPlan` - 43 edges

## Surprising Connections (you probably didn't know these)
- `load_story_progress()` --uses--> `Config`  [INFERRED]
  tests/benchmarks/batch_monitor.py → src/config.py
- `_volumes()` --uses--> `Config`  [INFERRED]
  tests/benchmarks/bench.py → src/config.py
- `main()` --uses--> `Config`  [INFERRED]
  tests/benchmarks/gen_base.py → src/config.py
- `main()` --uses--> `Config`  [INFERRED]
  tests/benchmarks/hw_logger.py → src/config.py
- `main()` --uses--> `Config`  [INFERRED]
  tests/benchmarks/resume_all.py → src/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Clip library preparation flow (harvest/prefetch -> normalize -> bake)** — readme_bulk_harvest, readme_prefetch, readme_library_normalize, readme_library_bake, readme_clip_library [EXTRACTED 1.00]
- **ASS subtitle subsystem design** — docs_subtitle_design_ass_burn_in, docs_subtitle_design_effect_presets, docs_subtitle_design_font_registry, docs_subtitle_design_max_chars_resegment, docs_subtitle_design_subtitle_preview, docs_subtitle_design_relative_ass_path [EXTRACTED 1.00]
- **FFmpeg filter-graph workarounds on Windows** — plan_edit_styles_balanced_tree_expr, plan_edit_styles_filter_script_file, plan_edit_styles_ass_area_cost_rule, docs_subtitle_design_relative_ass_path [INFERRED 0.75]

## Communities (128 total, 24 thin omitted)

### Community 0 - "Story Video Routes Core"
Cohesion: 0.04
Nodes (93): delete_keyword(), bulk_delete_library_clips(), cancel_image_clip_job_route(), cancel_story_batch(), cancel_story_batch_item(), cancel_story_harvest(), cancel_story_library_bake_job(), cancel_story_library_normalize_job() (+85 more)

### Community 1 - "Frontend API Types"
Cohesion: 0.03
Nodes (109): ClipUsageModeField(), ClipUsageModeFieldProps, formatCount(), StoryDecorFrameEditorProps, getClipUsageSummary(), ApiErrorPayload, BakeStoryLibraryRequest, BakeStoryLibraryResponse (+101 more)

### Community 2 - "Edit Styles Page UI"
Cohesion: 0.05
Nodes (93): CHAPTER_FILE_SAMPLE, ChapterFileHelp(), ColorInput(), HEX_COLOR_RE, addRectPath(), bleed(), clampFrame(), DECOR_BORDER_DEFAULT (+85 more)

### Community 3 - "Story Library Manager UI"
Cohesion: 0.07
Nodes (67): introLabel(), StoryIntroSelect(), StoryIntroSelectProps, StoryLibraryBakeDialog(), StoryLibraryBakeDialogProps, TERMINAL, BulkDeleteTarget, KEYWORD_STATUS_LABEL (+59 more)

### Community 4 - "Story Video Page UI"
Cohesion: 0.06
Nodes (75): Separator, readStoredMode(), useClipUsageMode(), cancelStoryBatch(), cancelStoryBatchItem(), cancelStoryVideo(), createStoryBatch(), createStoryVideo() (+67 more)

### Community 5 - "Story Video Settings UI"
Cohesion: 0.05
Nodes (64): SectionNav(), SectionNavItem, createSparkleOverlay(), deleteCtaOverlay(), deleteDecorImage(), deleteEffectPreviewSource(), deleteTVNoiseOverlay(), deleteWaveformOverlay() (+56 more)

### Community 6 - "TTS Audio Generation"
Cohesion: 0.10
Nodes (51): storage_absolute_path(), _api_url(), _build_audio_item_from_path(), _build_chunk_manifest(), _chunk_output_path(), _chunk_record_has_valid_audio(), _chunk_record_output_path(), _chunk_summary_from_manifest() (+43 more)

### Community 7 - "Bulk Clip Harvest"
Cohesion: 0.08
Nodes (42): delete_story_harvest_items(), delete_story_harvest_job(), _append_commit_status(), _cancel_path(), _clear_staging(), commit_harvest_job(), _commit_log_path(), _commit_pause_path() (+34 more)

### Community 8 - "Google Drive Audio Import"
Cohesion: 0.07
Nodes (27): _run_import(), cleanup_expired_drive_audio_imports(), create_drive_audio_import_session(), _download_drive_file(), DriveAudioImportError, fail_drive_audio_import(), _imports_root(), load_drive_audio_import() (+19 more)

### Community 9 - "Harvest & Prefetch Panels"
Cohesion: 0.08
Nodes (51): EmptyCard(), LoadingCard(), PaginationBar(), formatBytes(), LIVE_COMMIT_STATUSES, parseKeywords(), parseTags(), PROVIDERS (+43 more)

### Community 10 - "Light FX Effects"
Cohesion: 0.06
Nodes (19): _even_up(), event_times(), event_windows(), leak_png(), build(), LightLeak, LightRays, LightSweep (+11 more)

### Community 11 - "Hardware Benchmark Probes"
Cohesion: 0.06
Nodes (18): find_csv(), hw_from_csv(), columns(), DiskProbe, drive_letter(), letter_to_physical_map(), _map_via_cim(), _map_via_get_partition() (+10 more)

### Community 12 - "CUDA Overlay Benchmark"
Cohesion: 0.11
Nodes (40): available_filters(), build_ffmpeg_command(), build_filter_complex(), build_precompose_filter_complex(), build_precompose_pack_command(), compare_ssim(), default_overlays(), default_source_video() (+32 more)

### Community 13 - "Project Docs & Design"
Cohesion: 0.06
Nodes (42): mongo:7 service (story-video-mongo), ASS subtitle burn-in via libass, drawtext filter (per-frame expressions, out of scope), Subtitle effect presets (Clean, Fade soft, Karaoke pop, Emphasis bold), Font registry (scan_fonts, family-name resolution), Max-chars-per-frame re-segmentation (strategy A), Subtitle preview render (hash-cached), Auto Video Review Studio SPA entry (+34 more)

### Community 14 - "Edit Style Assets"
Cohesion: 0.09
Nodes (31): _cache_dir(), cached_png(), _cached_png(), card_frame(), build(), cover(), film_frame(), build() (+23 more)

### Community 15 - "Edit Style Kinds Registry"
Cohesion: 0.05
Nodes (7): Ops, is_kind(), Kind, make(), _registry(), _geometry(), TvZoom

### Community 16 - "Batch Progress Persistence"
Cohesion: 0.09
Nodes (28): _batch_cancel_path(), _batch_config_path(), _batch_dir(), _batch_progress_path(), _batches_root(), decor_ids_still_needed(), _enqueue_runner(), _final_batch_status() (+20 more)

### Community 17 - "Image & Keyword Repo"
Cohesion: 0.10
Nodes (28): compute_max_page(), cursor_covers_all(), cursor_id(), _cursor_update(), _db(), default_per_page(), get_cursor(), get_cursors() (+20 more)

### Community 18 - "Clip Canonicalization"
Cohesion: 0.09
Nodes (26): _canonical_matrix(), canonical_output_args(), _canonical_pix_fmt(), _canonical_range(), canonical_scale_filter(), canonical_tag_filter(), canonical_video_filter(), is_canonical() (+18 more)

### Community 20 - "Video Prefetch Sessions"
Cohesion: 0.08
Nodes (25): _numbered_env_keys(), normalize_photo(), _overlay_max_concurrent(), _cancel_path(), create_session(), discard_items(), _apply(), discard_session() (+17 more)

### Community 21 - "Story Library Registry"
Cohesion: 0.10
Nodes (24): bake_story_library(), count_library_clips(), create_library(), _default_library_record(), delete_library(), ensure_libraries_registry(), get_default_library_id(), library_sort_key() (+16 more)

### Community 22 - "Library Normalize & Spec"
Cohesion: 0.11
Nodes (19): describe_spec(), _cache_lock(), _cache_path(), expected_spec(), filter_valid_clips(), _load_cache(), _matches_expected(), matches_expected_spec() (+11 more)

### Community 23 - "Layout Kinds Common"
Cohesion: 0.09
Nodes (14): ass_seconds(), concat_input(), _default_margin(), drop_spans(), fn(), _ffconcat_entry(), first_frame_windows(), map_default_events() (+6 more)

### Community 24 - "CTA Overlay Management"
Cohesion: 0.11
Nodes (27): get_cta_overlays(), apply_placement_updates(), backfill_processed_sizes(), clamp_position(), _coord(), _even_down(), placement_expr(), probe_overlay_size() (+19 more)

### Community 25 - "Story Overlay Application"
Cohesion: 0.11
Nodes (11): is_story_cancel_requested(), _run_overlay_ffmpeg(), _progress(), _progress(), _worker(), _progress(), _worker(), _progress() (+3 more)

### Community 26 - "Clip Usage Ledger"
Cohesion: 0.10
Nodes (19): ClipSelection, ClipUsageLedger, normalize_clip_usage_mode(), _outbox_path(), _count(), _key(), _pool(), _set_counts() (+11 more)

### Community 27 - "Image Clip Jobs"
Cohesion: 0.12
Nodes (31): _cancel_path(), _Cancelled, _commit_cancel_path(), delete_items(), delete_job(), _download_batch(), _ensure_not_busy(), _fetch_photo() (+23 more)

### Community 28 - "Video Source Downloader"
Cohesion: 0.10
Nodes (30): _clips_dir(), run_commit(), add_clips_to_library(), _detect_source(), download_from_links(), download_from_provider_items(), _ensure_dirs(), _extract_pexels_id() (+22 more)

### Community 29 - "Subtitle Styles & Presets"
Cohesion: 0.09
Nodes (25): _abandon_batch_dir(), create_story_batch(), get_subtitle_presets(), get_subtitle_styles(), _resolve_overlay_rotation(), restore_subtitle_styles_route(), _subtitle_config_for_style(), _subtitle_config_from_payload() (+17 more)

### Community 30 - "Route Background Jobs"
Cohesion: 0.07
Nodes (28): commit_story_harvest(), _run_commit(), commit_story_prefetch(), _run_commit(), create_drive_audio_import(), create_sparkle_overlay(), delete_story_prefetch_session(), download_library_videos() (+20 more)

### Community 31 - "YouTube Download Page"
Cohesion: 0.09
Nodes (31): App(), AppShell(), HeroCard(), PageSection(), StatusAlert(), Alert, AlertDescription, AlertTitle (+23 more)

### Community 32 - "Pipeline Overlay Commands"
Cohesion: 0.08
Nodes (3): decor_input_args(), StoryVideoPipelineRunner, _utc_now()

### Community 33 - "FFmpeg Expression Graph"
Cohesion: 0.10
Nodes (10): ease(), sum_expr(), T(), win_factor(), windows_expr(), _even(), QuoteMoments, _even() (+2 more)

### Community 34 - "Effect Preview Rendering"
Cohesion: 0.10
Nodes (23): cleanup_job_files(), cleanup_temp_files(), build_preview_command(), build_source_set(), collect_active_layers(), delete_source(), EffectPreviewError, _index_path() (+15 more)

### Community 35 - "Library Bake Runner"
Cohesion: 0.12
Nodes (16): _target_resolution(), pause_story_library_bake_job(), _bake_cancel_path(), _bake_dir(), _bake_pause_path(), _bake_progress_path(), _clear_marker(), is_bake_cancel_requested() (+8 more)

### Community 36 - "Benchmark Reports & Backfill"
Cohesion: 0.11
Nodes (20): _harvest_keyword_pairs(), _library_ids(), _load_json(), main(), _prefetch_keyword_pairs(), _source_meta_updates(), load_voices(), save_voice_record() (+12 more)

### Community 37 - "Image Clip Panel & Library Hook"
Cohesion: 0.11
Nodes (33): EffectThumbnail(), effectTransform(), formatBytes(), PROVIDERS, RUNNING_STATUSES, splitList(), STATUS_LABEL, STOPPED_BY (+25 more)

### Community 38 - "Chapter & Quote Kinds"
Cohesion: 0.12
Nodes (9): ChapterCards, esc(), fit_font_size(), pick_quotes(), ass_alpha(), ass_colour(), ass_event(), ass_inline() (+1 more)

### Community 39 - "Pexels Key Pool"
Cohesion: 0.09
Nodes (7): _configured_keys(), _cooldown_seconds(), _KeyState, _parse_int(), PexelsKeyLease, PexelsKeyPool, PexelsQuotaExhausted

### Community 40 - "CRT TV Effect Processor"
Cohesion: 0.12
Nodes (24): build_custom_tv_effect_filter(), build_tv_effect_chain(), build_tv_effect_filter(), generate_tv_effect_style_preview(), _build_cmd(), get_custom_tv_effect_params(), get_selected_tv_effect_style_id(), get_tv_effect_filter() (+16 more)

### Community 41 - "Edit Style Param Spec"
Cohesion: 0.13
Nodes (26): boolean(), _chapter_fields(), _clamp_num(), clean_image_ref(), _clean_point(), _clean_rect(), color(), _corner_fields() (+18 more)

### Community 42 - "Config & Overlay Packs"
Cohesion: 0.15
Nodes (20): Config, overlay_position_expr(), build_pack_signature(), build_precompose_command(), build_precompose_filter(), _duration_from_record(), _file_fingerprint(), get_or_create_story_overlay_pack() (+12 more)

### Community 43 - "ASS Subtitle Build Tests"
Cohesion: 0.19
Nodes (31): build_ass(), _dialogue_lines(), _layer_of(), _style_line(), test_build_ass_background_disabled_turns_off_a_preset_box(), test_build_ass_background_override_draws_a_rectangle_under_the_text(), test_build_ass_colour_overrides_land_in_the_style_line(), test_build_ass_dialogue_has_exactly_10_fields_and_escapes() (+23 more)

### Community 44 - "TV Noise Overlays"
Cohesion: 0.18
Nodes (28): _worker(), _download_tv_noise_youtube(), _worker(), _absolute(), attach_tv_noise_source_file(), create_generated_overlay(), create_tv_noise_overlay(), create_tv_noise_placeholder() (+20 more)

### Community 45 - "YouTube Downloader"
Cohesion: 0.13
Nodes (25): cancel_youtube_download_job(), create_youtube_download_job(), download_youtube_file(), _error(), get_youtube_download_job(), _is_http_url(), cancel_job(), _clean_error() (+17 more)

### Community 46 - "Edit Style Store"
Cohesion: 0.14
Nodes (22): image_key(), add_image(), create_edit_style(), delete_edit_style(), _dir(), get_edit_style(), image_abspath(), _image_field() (+14 more)

### Community 47 - "Font Registry & Scan"
Cohesion: 0.12
Nodes (22): _cmap_format12_maps(), _cmap_format4_maps(), _cmap_maps_codepoint(), _coerce_style_float(), _coerce_style_int(), ensure_bundled_fonts(), _face_style(), _fallback_font_entries() (+14 more)

### Community 48 - "Sparkle Presets"
Cohesion: 0.12
Nodes (15): build_sparkle_source_command(), _dot_field_chain(), _even(), generate_sparkle_source(), get_sparkle_preset(), list_sparkle_presets(), sanitize_sparkle_params(), _source_dir() (+7 more)

### Community 49 - "Clip Usage Mode & DB Writes"
Cohesion: 0.10
Nodes (17): get_background_writer(), submit(), report_keyword_sweep(), _report_keyword_sweep(), _report_ingest(), _fake_pool(), _forbid_mongo(), boom() (+9 more)

### Community 50 - "Clip Selection & Audio Prep"
Cohesion: 0.08
Nodes (5): get_clip_usage_ledger(), _clip_source(), _load_story_library_index(), successor(), _usable_clip_pool()

### Community 51 - "Image Provider Search"
Cohesion: 0.10
Nodes (17): _clamp_per_page(), _clean_filters(), coverage_key(), _finish(), _get(), _normalize_pexels_photo(), _normalize_pixabay_photo(), _parse_int() (+9 more)

### Community 52 - "Render Bench Monitor"
Cohesion: 0.12
Nodes (13): _build_http_session(), iso(), main(), _mb(), Monitor, now(), poll(), run_scenario() (+5 more)

### Community 53 - "Clip Identity"
Cohesion: 0.15
Nodes (21): clip_identity(), clip_key_for(), provider_id_from_prefix(), _session_prefix(), source_identity(), source_key_for(), split_source_name(), _src_tag() (+13 more)

### Community 54 - "File Storage Manager"
Cohesion: 0.13
Nodes (22): _asset_id_for_clip(), cleanup_review_video_assets(), clear_directory(), collect_library_tags(), _default_library_index(), ensure_library_storage(), get_job_manifest_path(), get_library_index_path() (+14 more)

### Community 56 - "Frontend Lint Tooling"
Cohesion: 0.09
Nodes (25): name, private, type, version, auto-video-review-studio, clsx, eslint, @eslint/js (+17 more)

### Community 57 - "FFmpeg Helper & NVENC"
Cohesion: 0.13
Nodes (8): CRTEffectProcessor, FFmpegHelper, _normalize_intro(), _run_ffmpeg_with_fallback(), render_subtitle_preview(), gpu_sample(), main(), test_ffmpeg_helper_kills_active_process_when_cancel_requested()

### Community 58 - "Image Clip Routes"
Cohesion: 0.12
Nodes (18): unavailable_message(), cancel_image_clip_commit_route(), commit_image_clip_job_route(), delete_image_clip_items_route(), delete_image_clip_job_route(), get_image_clip_items_route(), get_image_clip_job_route(), _image_clip_job_or_404() (+10 more)

### Community 59 - "Intro Library"
Cohesion: 0.15
Nodes (18): create_intro_route(), delete_intro_route(), list_intros_route(), rename_intro_route(), add_intro(), delete_intro(), get_intro(), _has_audio_stream() (+10 more)

### Community 60 - "Karaoke Subtitle Events"
Cohesion: 0.12
Nodes (17): _allocate_centiseconds(), _background_events(), _cue_events(), _dialogue(), _escape_ass_text(), _format_ass_time(), _karaoke_units(), _karaoke_zoom_events() (+9 more)

### Community 61 - "MongoDB Connection"
Cohesion: 0.13
Nodes (12): _client_for_config(), ensure_indexes(), get_db(), is_available(), is_configured(), _make_client(), MongoUnavailable, reset() (+4 more)

### Community 62 - "Keyword Sweep Tests"
Cohesion: 0.13
Nodes (16): record_keyword_sweep(), client(), harvest_calls(), fake_search(), raw_env(), _run_harvest(), test_harvest_default_searches_everything(), test_harvest_records_limited_when_max_per_keyword_stops_it() (+8 more)

### Community 63 - "Story Create & Decor Preview"
Cohesion: 0.09
Nodes (17): _clean_ids(), _clip_usage_mode_or_503(), create_story_video(), run_holding_decor(), _find_sample_clip(), generate_crt_demo(), generate_subtitle_preview(), generate_tv_noise_demo() (+9 more)

### Community 64 - "Story Cancel & Audio Utils"
Cohesion: 0.11
Nodes (9): get_audio_duration(), validate_audio(), _cancel_path(), _output_dir(), request_story_cancel(), _story_dir(), StoryVideoCancelled, main() (+1 more)

### Community 65 - "Decor Image Index"
Cohesion: 0.16
Nodes (15): get_decor_images(), claim_decor_images(), decor_group_of(), decor_is_used(), delete_decor_image_record(), get_enabled_decor_images(), _index_path(), list_decor_groups() (+7 more)

### Community 66 - "Waveform Overlays"
Cohesion: 0.19
Nodes (18): get_waveform_overlays(), corner_expr(), _position_expr(), _absolute(), build_waveform_rotation(), create_waveform_overlay(), delete_waveform_overlay_record(), get_default_waveform_overlay() (+10 more)

### Community 67 - "TS App Config"
Cohesion: 0.09
Nodes (21): compilerOptions, allowImportingTsExtensions, baseUrl, erasableSyntaxOnly, ignoreDeprecations, jsx, lib, module (+13 more)

### Community 69 - "Overlay Placement Editor"
Cohesion: 0.20
Nodes (18): clampMove(), clampResize(), Drag, EditStyleFrameEditor(), even(), FrameItem, boxStyle(), CORNER_BUTTONS (+10 more)

### Community 70 - "Logging & Render Priority"
Cohesion: 0.14
Nodes (8): _boost_priority_if_batch_active(), SafeConsoleHandler, setup_logger(), _find_matching(), is_batch_render_active(), process_name_matches(), suspend_target_names(), main()

### Community 71 - "Decor Settings"
Cohesion: 0.16
Nodes (12): apply_decor_blur_default(), apply_decor_settings(), _baked_look_is_stale(), blur_radius_of(), border_of(), _clamp_blur(), _clamp_border(), _clamp_border_color() (+4 more)

### Community 72 - "Prefetch Download Loop"
Cohesion: 0.12
Nodes (13): imported_provider_video_ids(), is_cancel_requested(), _mark_item(), _apply(), _media_path(), _emit(), run_prefetch(), download_one() (+5 more)

### Community 73 - "Image Clip Commit"
Cohesion: 0.14
Nodes (15): find_known_photo_keys(), get_photo_statuses(), _apply_updates(), _clip_tags(), commit_image_clip_job(), cancel_cb(), cleanup_item(), finish() (+7 more)

### Community 74 - "Web App & Samples"
Cohesion: 0.17
Nodes (12): next_default_audio_name(), _safe_name(), _frontend_dist_dir(), media(), spa(), voices(), cut_audio(), fmt_ts() (+4 more)

### Community 75 - "Image Clip Effects Rotation"
Cohesion: 0.11
Nodes (8): start_position(), clean_effects(), duration_range(), EffectRotation, pick_duration(), _clean_keywords(), start_image_clip_job(), _start_thread()

### Community 76 - "Library Resolution"
Cohesion: 0.14
Nodes (10): library_motion_blocked(), any_fully_baked_library(), any_styled_library(), get_library(), is_fully_baked_library(), is_styled_library(), libraries_clip_duration(), library_clip_duration() (+2 more)

### Community 77 - "TS Node Config"
Cohesion: 0.11
Nodes (17): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 78 - "Library Index Storage"
Cohesion: 0.19
Nodes (10): remove_file_with_retries(), _asset_absolute_path(), _clean_orphan_clip_files(), delete_story_library_assets(), _index_lock(), _index_path(), _library_root(), load_story_library_index() (+2 more)

### Community 79 - "Subtitle Segment Rebase"
Cohesion: 0.18
Nodes (12): _ass_event_spans(), _fmt_ass_ts(), _parse_ass_ts(), _rebase_ass_file(), _snap_segment_boundary(), test_ass_event_spans_merges_overlapping_events(), test_ass_event_spans_missing_file_is_empty(), test_snap_boundary_gives_up_when_no_edge_is_within_the_window() (+4 more)

### Community 80 - "shadcn Components Config"
Cohesion: 0.12
Nodes (16): aliases, components, hooks, lib, ui, utils, iconLibrary, rsc (+8 more)

### Community 81 - "Frontend Dependencies"
Cohesion: 0.12
Nodes (17): dependencies, auto-video-review-studio, class-variance-authority, clsx, lucide-react, @radix-ui/react-alert-dialog, @radix-ui/react-checkbox, @radix-ui/react-dialog (+9 more)

### Community 82 - "Bake Clip Appending"
Cohesion: 0.17
Nodes (6): bake_and_append_clips(), _hwaccel_flags(), motion_filter(), _style_filter_from_record(), _library_clip_paths(), story_library_root()

### Community 83 - "Library Delete Tests"
Cohesion: 0.28
Nodes (12): _asset(), client(), _default_root(), _read_assets(), story_library(), test_bulk_delete_all_clears_index_and_orphan_files(), test_bulk_delete_all_rejects_active_import_session(), test_bulk_delete_ids_only_removes_requested_clips() (+4 more)

### Community 84 - "Frontend Dev Dependencies"
Cohesion: 0.13
Nodes (15): devDependencies, eslint, @eslint/js, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals, tailwindcss, @tailwindcss/vite (+7 more)

### Community 85 - "Media Repo"
Cohesion: 0.19
Nodes (7): build_ingest_updates(), commit_clip_usage(), get_clip_use_counts(), _now(), record_ingest(), _source_meta(), bulk_upsert()

### Community 86 - "Decor Image Import"
Cohesion: 0.18
Nodes (7): _even_down(), find_decor_by_source(), import_decor_image(), imported_source_keys(), _place(), _retired_source_keys(), _source_key()

### Community 87 - "Decor Image Search"
Cohesion: 0.24
Nodes (9): download_provider_image(), _is_allowed_download_url(), is_fullhd_16_9(), _normalize_pexels_photo(), search_pexels_images(), search_pixabay_images(), search_provider_images(), get_pexels_key_pool() (+1 more)

### Community 88 - "Root Package Scripts"
Cohesion: 0.14
Nodes (13): devDependencies, concurrently, name, private, scripts, build, dev, dev:backend (+5 more)

### Community 89 - "Style Rotation Dealing"
Cohesion: 0.16
Nodes (6): deal_rotation(), build_plan(), build_layout_rotation(), deal_modifier_rotation(), resolve_modifiers(), _usable()

### Community 90 - "Decor Fit Geometry"
Cohesion: 0.19
Nodes (7): build_manual_mask_png(), _clamp_frame(), corner_radius_of(), decor_filter_parts(), decor_fit_geometry(), _even(), target_size()

### Community 91 - "ASS Colour Conversion"
Cohesion: 0.14
Nodes (8): get_preset_style(), _hex_to_ass_colour(), _opacity_to_alpha_tag(), _resolve_render_style(), _split_ass_colour(), test_get_preset_style_unknown_falls_back_to_clean(), test_hex_to_ass_colour_rejects_anything_but_six_hex_digits(), test_hex_to_ass_colour_reverses_byte_order()

### Community 92 - "Keyword Repo"
Cohesion: 0.23
Nodes (8): find_used_keyword_pairs(), get_used_keywords(), is_used(), _iso(), list_keywords(), _now(), serialize_keyword(), test_find_used_pairs_is_disabled_without_mongo()

### Community 93 - "Decor Green-Frame Detect"
Cohesion: 0.15
Nodes (7): detect_decor_image_frame(), _contiguous_run(), decor_source_abs_path(), detect_green_frame(), get_decor_image(), processed_abs_path(), resolve_decor_image()

### Community 94 - "Subtitle Text Measuring"
Cohesion: 0.19
Nodes (8): _ass_size_scale(), _can_measure(), _layout_lines(), prefix(), _load_measure_font(), _measure_width(), test_measure_font_is_scaled_to_the_ass_size_convention(), test_thai_positioned_layout_matches_a_single_line_measurement()

### Community 95 - "Subtitle Resegmentation"
Cohesion: 0.17
Nodes (13): _enforce_min_duration(), _has_cjk(), _merge_short_blocks(), _pack_units(), resegment(), _split_sentences(), test_resegment_latin_breaks_only_at_spaces(), test_resegment_merge_respects_max_lines_after_repack() (+5 more)

### Community 96 - "Edit Style Preview"
Cohesion: 0.21
Nodes (6): _decor_free_layouts(), _first_decor_id(), _previews_dir(), render_preview(), _sample_srt(), type_flag()

### Community 97 - "Decor Border Styling"
Cohesion: 0.21
Nodes (5): styled_decor(), build(), _blur_keeping_hole_out(), _border_signature(), _draw_border()

### Community 98 - "Chapter Parsing"
Cohesion: 0.24
Nodes (6): auto_chapters(), format_title(), parse_chapter_file(), parse_time(), resolve(), snap()

### Community 100 - "Decor Image Preprocess"
Cohesion: 0.23
Nodes (8): _absolute(), create_decor_image(), _key_is_blue(), preprocess_decor_image(), _rebuild_processed(), regenerate_decor_mask(), _register_decor_image(), _relative()

### Community 104 - "Batch Queue Status"
Cohesion: 0.25
Nodes (6): get_batch_progress(), get_batch_queue(), _public_batch_status(), batch_output_dir(), queue_position(), queue_snapshot()

### Community 107 - "SRT Parsing"
Cohesion: 0.22
Nodes (8): _decode_srt_bytes(), parse_srt(), SubtitleParseError, test_parse_srt_garbage_and_empty(), test_parse_srt_handles_bom_and_crlf(), test_parse_srt_missing_file(), test_parse_srt_real_file(), test_resegment_korean_long_cue()

### Community 109 - "TS Root Config"
Cohesion: 0.29
Nodes (6): compilerOptions, baseUrl, ignoreDeprecations, paths, files, references

### Community 110 - "Frontend Scripts"
Cohesion: 0.33
Nodes (6): scripts, build, dev, lint, preview, typecheck

### Community 111 - "Clip Usage Summary"
Cohesion: 0.33
Nodes (3): usage_summary(), clip_usage_summary(), _resolve_library_ids_or_404()

### Community 114 - "Vite Config"
Cohesion: 0.40
Nodes (3): @tailwindcss/vite, vite, @vitejs/plugin-react

### Community 119 - "ASS Filter Path"
Cohesion: 0.67
Nodes (3): ass_filter_path(), test_ass_filter_path_different_drive_fallback(), test_ass_filter_path_same_drive()

## Knowledge Gaps
- **219 isolated node(s):** `$schema`, `style`, `rsc`, `tsx`, `config` (+214 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1090 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **24 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Config & Overlay Packs` to `Story Video Routes Core`, `TTS Audio Generation`, `Bulk Clip Harvest`, `Google Drive Audio Import`, `Hardware Benchmark Probes`, `Edit Style Assets`, `Batch Progress Persistence`, `Clip Canonicalization`, `Video Prefetch Sessions`, `Story Library Registry`, `Library Normalize & Spec`, `CTA Overlay Management`, `Clip Usage Ledger`, `Image Clip Jobs`, `Video Source Downloader`, `Subtitle Styles & Presets`, `Route Background Jobs`, `Pipeline Overlay Commands`, `Effect Preview Rendering`, `Library Bake Runner`, `Benchmark Reports & Backfill`, `Pexels Key Pool`, `CRT TV Effect Processor`, `ASS Subtitle Build Tests`, `TV Noise Overlays`, `YouTube Downloader`, `Edit Style Store`, `Font Registry & Scan`, `Sparkle Presets`, `Clip Usage Mode & DB Writes`, `Image Provider Search`, `Render Bench Monitor`, `File Storage Manager`, `Batch Queue Runner`, `FFmpeg Helper & NVENC`, `Intro Library`, `MongoDB Connection`, `Keyword Sweep Tests`, `Story Create & Decor Preview`, `Story Cancel & Audio Utils`, `Decor Image Index`, `Waveform Overlays`, `Logging & Render Priority`, `Decor Settings`, `Prefetch Download Loop`, `Image Clip Commit`, `Web App & Samples`, `Image Clip Effects Rotation`, `Library Index Storage`, `Bake Clip Appending`, `Library Delete Tests`, `Decor Image Import`, `Decor Image Search`, `Decor Fit Geometry`, `Decor Green-Frame Detect`, `Subtitle Resegmentation`, `Edit Style Preview`, `Decor Image Preprocess`, `Decor File Removal`?**
  _High betweenness centrality (0.119) - this node is a cross-community bridge._
- **Are the 219 inferred relationships involving `Config` (e.g. with `get_db()` and `is_configured()`) actually correct?**
  _`Config` has 219 INFERRED edges - model-reasoned connections that need verification._
- **What connects `$schema`, `style`, `rsc` to the rest of the system?**
  _219 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Story Video Routes Core` be split into smaller, more focused modules?**
  _Cohesion score 0.0421455938697318 - nodes in this community are weakly interconnected._
- **Why does `StoryVideoPipelineRunner` connect `Pipeline Overlay Commands` to `Story Video Routes Core`, `Edit Style Preview`, `Story Cancel & Audio Utils`, `TTS Audio Generation`, `Shared Clip Bag`, `Config & Overlay Packs`, `SRT Parsing`, `Edit Style Kinds Registry`, `Batch Progress Persistence`, `Clip Usage Mode & DB Writes`, `Clip Selection & Audio Prep`, `Subtitle ASS Preparation`, `Batch Queue Runner`, `FFmpeg Helper & NVENC`, `Story Overlay Application`, `Story Create & Decor Preview`?**
  _High betweenness centrality (0.038) - this node is a cross-community bridge._
- **Should `Frontend API Types` be split into smaller, more focused modules?**
  _Cohesion score 0.032335907335907334 - nodes in this community are weakly interconnected._
- **Why does `Kind` connect `Edit Style Kinds Registry` to `FFmpeg Expression Graph`, `Magazine Kind`, `Two-Layer Kind`, `Album Kind`, `DocStrip Kind`, `Chapter & Quote Kinds`, `Dossier Kind`, `Light FX Effects`, `Newsroom Kind`, `Layout Kinds Common`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._