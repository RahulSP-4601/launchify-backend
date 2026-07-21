from __future__ import annotations

from app.models.projects import LaunchScriptRecord, LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label
from app.services.scene_intent_resolver import resolve_scene_intent
from app.services.scene_type_classifier import classify_scene_type, visible_scene_labels

GENERIC_SURFACE_TOKENS = frozenset(
    {
        "account",
        "continue",
        "course",
        "courses",
        "create",
        "google",
        "japanese",
        "level",
        "login",
        "open",
        "pick",
        "select",
        "sign",
        "start",
    }
)


def recover_canonical_scenes(
    launch_script: LaunchScriptRecord,
    events: list[SessionEventRecord] | None,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> list[LaunchScriptScene]:
    if not events:
        return launch_script.scenes
    events_by_scene = preferred_events_by_scene(events)
    analyses_by_scene = {analysis.scene_number: analysis for analysis in visual_analyses or []}
    kept: list[LaunchScriptScene] = []
    for index, scene in enumerate(launch_script.scenes):
        if not should_drop_scene(scene, index, launch_script.scenes, events_by_scene, analyses_by_scene):
            kept.append(scene)
    merged = merge_screen_scenes(kept, events_by_scene, analyses_by_scene)
    return merged or kept or launch_script.scenes


def preferred_events_by_scene(events: list[SessionEventRecord]) -> dict[int, SessionEventRecord]:
    preferred: dict[int, SessionEventRecord] = {}
    for event in events:
        scene_number = int(event.metadata.get("scene_number", "0") or 0)
        if scene_number <= 0:
            continue
        current = preferred.get(scene_number)
        if current is None or event_rank(event) >= event_rank(current):
            preferred[scene_number] = event
    return preferred


def event_rank(event: SessionEventRecord) -> tuple[float, float, float]:
    score = float(event.metadata.get("score", "0") or 0.0)
    return (
        1.0 if event.type == "click" else 0.6 if event.type == "focus" else 0.3,
        score,
        1.0 if (event.target.label or event.target.text).strip() else 0.0,
    )


def should_drop_scene(
    scene: LaunchScriptScene,
    index: int,
    scenes: list[LaunchScriptScene],
    events_by_scene: dict[int, SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> bool:
    if scene.scene_number in events_by_scene or not action_like_scene(scene):
        return False
    neighbors = nearby_supported_scenes(index, scenes, events_by_scene, analyses_by_scene)
    if not neighbors:
        return False
    best_neighbor = max(neighbors, key=lambda item: surface_similarity(scene, item, analyses_by_scene))
    return surface_similarity(scene, best_neighbor, analyses_by_scene) >= 0.32


def nearby_supported_scenes(
    index: int,
    scenes: list[LaunchScriptScene],
    events_by_scene: dict[int, SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[LaunchScriptScene]:
    current = scenes[index]
    family = scene_family(current, analyses_by_scene.get(current.scene_number))
    selected: list[LaunchScriptScene] = []
    for offset in (-2, -1, 1, 2):
        neighbor_index = index + offset
        if neighbor_index < 0 or neighbor_index >= len(scenes):
            continue
        neighbor = scenes[neighbor_index]
        if neighbor.scene_number not in events_by_scene:
            continue
        if scene_family(neighbor, analyses_by_scene.get(neighbor.scene_number)) != family:
            continue
        selected.append(neighbor)
    return selected


def action_like_scene(scene: LaunchScriptScene) -> bool:
    return resolve_scene_intent(scene.source_excerpt, scene.spoken_line).intent not in {"result", "generic"}


def scene_family(scene: LaunchScriptScene, analysis: VisualSceneAnalysisRecord | None) -> str:
    scene_type = classify_scene_type(scene, analysis)
    if scene_type in {"auth_entry", "auth_provider", "account_picker"}:
        return "auth"
    if scene_type in {"course_catalog", "result_state"}:
        return "course"
    return scene_type


def surface_similarity(
    left: LaunchScriptScene,
    right: LaunchScriptScene,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> float:
    left_analysis = analyses_by_scene.get(left.scene_number)
    right_analysis = analyses_by_scene.get(right.scene_number)
    if scene_family(left, left_analysis) != scene_family(right, right_analysis):
        return 0.0
    left_tokens = scene_surface_tokens(left, left_analysis)
    right_tokens = scene_surface_tokens(right, right_analysis)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return overlap / union if union else 0.0


def scene_surface_tokens(scene: LaunchScriptScene, analysis: VisualSceneAnalysisRecord | None) -> set[str]:
    intent = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
    tokens = set(intent.focus_tokens) | label_tokens(scene.on_screen_text) | label_tokens(scene.source_excerpt)
    for label in visible_scene_labels(analysis)[:8]:
        tokens.update(label_tokens(label))
    return {token for token in tokens if token in GENERIC_SURFACE_TOKENS or len(token) >= 5}


def label_tokens(text: str) -> set[str]:
    return {token for token in normalize_label(text).split() if token}


def merge_screen_scenes(
    scenes: list[LaunchScriptScene],
    events_by_scene: dict[int, SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[LaunchScriptScene]:
    merged: list[LaunchScriptScene] = []
    index = 0
    while index < len(scenes):
        current = scenes[index]
        if index + 1 >= len(scenes):
            merged.append(current)
            break
        following = scenes[index + 1]
        combined = merged_screen_scene(current, following, events_by_scene, analyses_by_scene)
        if combined is None:
            merged.append(current)
            index += 1
            continue
        merged.append(combined)
        index += 2
    return merged


def merged_screen_scene(
    current: LaunchScriptScene,
    following: LaunchScriptScene,
    events_by_scene: dict[int, SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> LaunchScriptScene | None:
    if auth_screen_pair(current, following, events_by_scene, analyses_by_scene):
        return merged_scene(current, following, merged_auth_label(current, following, analyses_by_scene))
    if state_action_pair(current, following, events_by_scene):
        return merged_scene(current, following, merged_course_label(current, analyses_by_scene))
    if transition_course_pair(current, following, events_by_scene, analyses_by_scene):
        return merged_scene(current, following, merged_course_label(current, analyses_by_scene))
    family = scene_family(current, analyses_by_scene.get(current.scene_number))
    if family != scene_family(following, analyses_by_scene.get(following.scene_number)):
        return None
    return None


def auth_screen_pair(
    current: LaunchScriptScene,
    following: LaunchScriptScene,
    events_by_scene: dict[int, SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> bool:
    current_event = events_by_scene.get(current.scene_number)
    next_event = events_by_scene.get(following.scene_number)
    if current_event is not None or next_event is None:
        return False
    if next_event.type != "click":
        return False
    combined = " ".join(visible_scene_labels(analyses_by_scene.get(following.scene_number)))
    return "google" in combined and ("sign up with google" in combined or "log in with google" in combined)


def state_action_pair(
    current: LaunchScriptScene,
    following: LaunchScriptScene,
    events_by_scene: dict[int, SessionEventRecord],
) -> bool:
    current_event = events_by_scene.get(current.scene_number)
    next_event = events_by_scene.get(following.scene_number)
    if current_event is None or next_event is None:
        return False
    if current_event.type != "focus" or next_event.type != "click":
        return False
    return current_event.metadata.get("action_class") == "result_state"


def transition_course_pair(
    current: LaunchScriptScene,
    following: LaunchScriptScene,
    events_by_scene: dict[int, SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> bool:
    current_event = events_by_scene.get(current.scene_number)
    next_event = events_by_scene.get(following.scene_number)
    if current_event is not None or next_event is None:
        return False
    if next_event.type != "click":
        return False
    if not course_click_label(next_event):
        return False
    return has_course_surface(analyses_by_scene.get(current.scene_number)) or has_course_surface(analyses_by_scene.get(following.scene_number))


def merged_scene(
    current: LaunchScriptScene,
    following: LaunchScriptScene,
    label: str,
) -> LaunchScriptScene:
    screen_label = label or current.on_screen_text or following.on_screen_text
    purpose = current.purpose if current.on_screen_text.strip() == screen_label and current.purpose.startswith("Show the viewer") else screen_purpose(screen_label)
    spoken_line = join_scene_text(current.spoken_line, following.spoken_line)
    source_excerpt = join_scene_text(current.source_excerpt, following.source_excerpt)
    return LaunchScriptScene(
        scene_number=current.scene_number,
        purpose=purpose,
        spoken_line=spoken_line,
        on_screen_text=screen_label,
        source_excerpt=source_excerpt,
        estimated_duration_seconds=round(current.estimated_duration_seconds + following.estimated_duration_seconds, 2),
    )


def merged_auth_label(
    current: LaunchScriptScene,
    following: LaunchScriptScene,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    labels = visible_scene_labels(analyses_by_scene.get(following.scene_number))
    return next((label.title() for label in labels if "continue with google" in label), "") or "Continue with Google"


def merged_course_label(
    current: LaunchScriptScene,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    labels = visible_scene_labels(analyses_by_scene.get(current.scene_number))
    return next((label.title() for label in labels if "select a course" in label), "") or "Select a course"


def join_scene_text(left: str, right: str) -> str:
    parts = [part.strip() for part in (left, right) if part.strip()]
    unique = list(dict.fromkeys(parts))
    return ". ".join(unique)


def screen_purpose(label: str) -> str:
    clean = label.strip().rstrip(".")
    return f"Show the viewer the {clean} screen." if clean else "Show the viewer the next screen."


def course_click_label(event: SessionEventRecord) -> bool:
    label = normalize_label(event.target.label or event.target.text)
    return label in {"open course", "japanese"} or ("course" in label and "open" in label)


def has_course_surface(analysis: VisualSceneAnalysisRecord | None) -> bool:
    labels = visible_scene_labels(analysis)
    normalized = {normalize_label(label) for label in labels}
    language_count = sum(1 for label in normalized if label in {"japanese", "english", "german", "spanish", "french"})
    return "select a course" in normalized or "open course" in normalized or language_count >= 2
