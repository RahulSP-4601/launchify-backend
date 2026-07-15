from __future__ import annotations

from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanZoom, ManualOverrideRecord


def apply_manual_overrides(
    edit_plan: EditPlanRecord,
    manual_overrides: ManualOverrideRecord | None,
) -> EditPlanRecord:
    if manual_overrides is None or not manual_overrides.scenes:
        return edit_plan
    overrides_by_scene = {override.scene_number: override for override in manual_overrides.scenes}
    updated_scenes = []
    for scene in edit_plan.scenes:
        override = overrides_by_scene.get(scene.scene_number)
        if override is None:
            updated_scenes.append(scene)
            continue
        updated_scenes.append(
            scene.model_copy(
                update={
                    "title": override.title or scene.title,
                    "spoken_line": override.spoken_line or scene.spoken_line,
                    "on_screen_text": override.on_screen_text or scene.on_screen_text,
                    "captions": updated_captions(scene.captions, override.caption_override),
                    "zooms": updated_zooms(scene.zooms, override.force_zoom),
                    "highlights": updated_highlights(scene.highlights, override.force_highlight),
                }
            )
        )
    return edit_plan.model_copy(update={"scenes": updated_scenes})


def updated_captions(captions: list[EditPlanCaption], caption_override: str) -> list[EditPlanCaption]:
    if not caption_override or not captions:
        return captions
    first_caption = captions[0].model_copy(update={"text": caption_override})
    return [first_caption, *captions[1:]]


def updated_zooms(zooms: list[EditPlanZoom], force_zoom: bool | None) -> list[EditPlanZoom]:
    if force_zoom is None:
        return zooms
    return zooms if force_zoom else []


def updated_highlights(
    highlights: list[EditPlanHighlight],
    force_highlight: bool | None,
) -> list[EditPlanHighlight]:
    if force_highlight is None:
        return highlights
    return highlights if force_highlight else []
