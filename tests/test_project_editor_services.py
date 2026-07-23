from __future__ import annotations

from datetime import UTC, datetime
from unittest import TestCase
from unittest.mock import patch

from app.models.project_editor import (
    ProjectEditorConflictError,
    EditorCaptionRecord,
    EditorClipRecord,
    EditorCommentRecord,
    EditorSceneRecord,
    EditorTrackRecord,
    ProjectEditorToolState,
    ProjectEditorSequence,
    ProjectEditorState,
)
from app.models.projects import ProjectRecord
from app.services.project_editor_render_timeline import build_render_timeline
from app.services.project_editor_store import ensure_revision_base
from app.services.project_editor_validation import validate_project_editor_state


class ProjectEditorServicesTests(TestCase):
    def test_validate_project_editor_state_accepts_sequence_aligned_tracks(self) -> None:
        project = sample_project()
        state = sample_editor_state()
        validate_project_editor_state(project, state)

    def test_validate_project_editor_state_rejects_unknown_selected_track(self) -> None:
        state = sample_editor_state()
        state.selected_track_id = "track-missing"
        with self.assertRaisesRegex(ValueError, "selected editor track no longer exists"):
            validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_rejects_unknown_selected_clip(self) -> None:
        state = sample_editor_state()
        state.selected_clip_id = "clip-missing"
        with self.assertRaisesRegex(ValueError, "selected editor clip no longer exists"):
            validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_rejects_orphan_caption_after_ripple_case(self) -> None:
        project = sample_project()
        state = sample_editor_state()
        state.sequence.tracks[1].clips[0].timeline_end = 3.4
        with self.assertRaisesRegex(ValueError, "Caption timings must match the sequence caption track."):
            validate_project_editor_state(project, state)

    def test_build_render_timeline_uses_sequence_clip_timings(self) -> None:
        timeline = build_render_timeline(sample_project(), sample_editor_state())
        scenes = timeline["scenes"]
        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]["editor_start"], 0.0)
        self.assertEqual(scenes[0]["editor_end"], 3.0)
        self.assertTrue(scenes[1]["is_inserted"])
        self.assertEqual(timeline["total_duration_seconds"], 6.0)
        self.assertEqual(timeline["tracks"][0]["kind"], "video")

    def test_build_render_timeline_exposes_overlay_tracks(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        id="clip-overlay-1",
                        track_id="track-overlay-1",
                        kind="inserted_card",
                        title="Overlay Callout",
                        scene_id="scene-1-overlay",
                        timeline_start=0.5,
                        timeline_end=2.5,
                        source_start=None,
                        source_end=None,
                        text="Focus here",
                    ),
                ],
                id="track-overlay-1",
                kind="overlay",
                name="Overlay",
            ),
        )
        timeline = build_render_timeline(sample_project(), state)
        overlay_tracks = [track for track in timeline["tracks"] if track["kind"] == "overlay"]
        self.assertEqual(len(overlay_tracks), 1)
        self.assertEqual(overlay_tracks[0]["clips"][0]["title"], "Overlay Callout")

    def test_build_render_timeline_uses_secondary_video_track_segments(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        asset_path="editor/video/imported.mp4",
                        content_type="video/mp4",
                        id="clip-secondary-1",
                        kind="media_video",
                        title="Imported clip",
                        scene_id="scene-secondary-1",
                        timeline_start=1.0,
                        timeline_end=2.0,
                        source_start=None,
                        source_end=None,
                        text="Imported clip",
                        track_id="track-video-2",
                    ),
                ],
                id="track-video-2",
                kind="video",
                name="Video 2",
            ),
        )
        with patch("app.services.project_editor_render_timeline.create_signed_asset_url", side_effect=lambda path: f"signed://{path}"):
            timeline = build_render_timeline(sample_project(), state)
        imported_scene = next(
            scene for scene in timeline["scenes"]
            if scene["clip_kind"] == "media_video"
        )
        self.assertEqual(imported_scene["editor_start"], 1.0)
        self.assertEqual(imported_scene["editor_end"], 2.0)
        self.assertEqual(imported_scene["source"], "imported")

    def test_build_render_timeline_keeps_captions_during_secondary_video_overlay(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        asset_path="editor/video/imported.mp4",
                        content_type="video/mp4",
                        id="clip-secondary-1",
                        kind="media_video",
                        title="Imported clip",
                        scene_id="scene-secondary-1",
                        timeline_start=1.0,
                        timeline_end=2.0,
                        source_start=None,
                        source_end=None,
                        text="Imported clip",
                        track_id="track-video-2",
                    ),
                ],
                id="track-video-2",
                kind="video",
                name="Video 2",
            ),
        )
        with patch("app.services.project_editor_render_timeline.create_signed_asset_url", side_effect=lambda path: f"signed://{path}"):
            timeline = build_render_timeline(sample_project(), state)
        imported_scene = next(scene for scene in timeline["scenes"] if scene["clip_kind"] == "media_video")
        self.assertTrue(imported_scene["captions"])
        self.assertEqual(imported_scene["captions"][0]["text"], "Hello")

    def test_validate_project_editor_state_accepts_voiceover_audio_track(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        id="audio-clip-1",
                        track_id="track-audio-1",
                        kind="voiceover",
                        title="Scene 1 VO",
                        scene_id="scene-1",
                        timeline_start=0.0,
                        timeline_end=3.0,
                        source_start=None,
                        source_end=None,
                        text="Hello",
                    ),
                ],
                id="track-audio-1",
                kind="audio",
                name="Voiceover",
            ),
        )
        validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_accepts_uploaded_audio_track(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        asset_path="editor/audio/song.mp3",
                        content_type="audio/mpeg",
                        id="audio-bed-1",
                        kind="media_audio",
                        title="Song",
                        scene_id=None,
                        timeline_start=0.0,
                        timeline_end=3.0,
                        source_start=None,
                        source_end=None,
                        text="Song",
                        track_id="track-audio-2",
                    ),
                ],
                id="track-audio-2",
                kind="audio",
                name="Music",
            ),
        )
        validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_accepts_imported_video_duration(self) -> None:
        state = sample_editor_state()
        state.scenes[1].source = "imported"
        validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_rejects_non_voiceover_audio_clip(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        id="audio-clip-1",
                        track_id="track-audio-1",
                        kind="caption",
                        title="Bad audio",
                        scene_id="scene-1",
                        timeline_start=0.0,
                        timeline_end=3.0,
                        source_start=None,
                        source_end=None,
                        text="Hello",
                    ),
                ],
                id="track-audio-1",
                kind="audio",
                name="Voiceover",
            ),
        )
        with self.assertRaisesRegex(ValueError, "Audio tracks cannot contain caption clips."):
            validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_accepts_comments_and_tool_state(self) -> None:
        state = sample_editor_state()
        state.comments = [
            EditorCommentRecord(
                id="comment-1",
                scene_id="scene-1",
                body="Check alignment",
                time=1.2,
                created_at="2026-07-23T00:00:00Z",
            )
        ]
        state.tool_state = ProjectEditorToolState(active_effect="zoom", media_tab="project")
        validate_project_editor_state(sample_project(), state)

    def test_validate_project_editor_state_rejects_overlay_without_content(self) -> None:
        state = sample_editor_state()
        state.sequence.tracks.append(
            EditorTrackRecord(
                clips=[
                    EditorClipRecord(
                        id="overlay-empty-1",
                        kind="text_overlay",
                        scene_id="scene-1",
                        timeline_start=0.2,
                        timeline_end=1.0,
                        source_start=None,
                        source_end=None,
                        text="",
                        title="",
                        track_id="track-overlay-1",
                    )
                ],
                id="track-overlay-1",
                kind="overlay",
                name="Overlay",
            )
        )
        with self.assertRaisesRegex(ValueError, "Overlay clips must include title or text content."):
            validate_project_editor_state(sample_project(), state)

    def test_ensure_revision_base_allows_matching_revision_head(self) -> None:
        ensure_revision_base(12, 12)
        ensure_revision_base(None, None)

    def test_ensure_revision_base_rejects_stale_revision_head(self) -> None:
        with self.assertRaises(ProjectEditorConflictError):
            ensure_revision_base(12, 11)


def sample_project() -> ProjectRecord:
    now = datetime.now(UTC)
    return ProjectRecord(
        created_at=now,
        id="project-1",
        project_name="Pronouncly",
        status="ready",
        updated_at=now,
    )


def sample_editor_state() -> ProjectEditorState:
    return ProjectEditorState(
        aspect_ratio="16:9",
        captions=[
            EditorCaptionRecord(id="caption-1", scene_id="scene-1", start=0.0, end=3.0, text="Hello"),
        ],
        scenes=[
            EditorSceneRecord(id="scene-1", scene_number=1, title="Scene 1", spoken_line="Hello", on_screen_text="Hello", start=0.0, end=3.0, source="edit_plan"),
            EditorSceneRecord(id="inserted-scene-2", scene_number=2, title="Inserted", spoken_line="Gap", on_screen_text="Gap", start=3.0, end=6.0, source="inserted"),
        ],
        selected_scene_id="scene-1",
        selected_track_id="track-video-1",
        sequence=ProjectEditorSequence(
            duration_seconds=6.0,
            id="sequence-project-1",
            playhead_seconds=0.0,
            tracks=[
                EditorTrackRecord(
                    clips=[
                        EditorClipRecord(id="clip-scene-1", track_id="track-video-1", kind="source_video", title="Scene 1", scene_id="scene-1", timeline_start=0.0, timeline_end=3.0, source_start=0.0, source_end=3.0, text="Hello"),
                        EditorClipRecord(id="clip-inserted-2", track_id="track-video-1", kind="inserted_card", title="Inserted", scene_id="inserted-scene-2", timeline_start=3.0, timeline_end=6.0, source_start=None, source_end=None, text="Gap"),
                    ],
                    id="track-video-1",
                    kind="video",
                    name="Video",
                ),
                EditorTrackRecord(
                    clips=[
                        EditorClipRecord(id="caption-clip-caption-1", track_id="track-caption-1", kind="caption", title="Hello", scene_id="scene-1", timeline_start=0.0, timeline_end=3.0, source_start=None, source_end=None, text="Hello"),
                    ],
                    id="track-caption-1",
                    kind="caption",
                    name="Captions",
                ),
            ],
            version=4,
        ),
        selected_clip_id="clip-scene-1",
        show_captions=True,
        comments=[],
        tool_state=ProjectEditorToolState(),
    )
