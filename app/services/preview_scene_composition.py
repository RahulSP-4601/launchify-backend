from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from textwrap import wrap

from app.models.projects import EditPlanScene

COMPOSITION_BG = "0x051015"
PANEL_BG = "0x0C171D"
PANEL_BORDER = "0x31444F"
ACCENT = "0xFFD27A"
TEXT_PRIMARY = "white"
TEXT_SECONDARY = "0xB8C6CF"


@dataclass(frozen=True)
class PreviewSceneComposition:
    layout_mode: str
    headline: str
    supporting_copy: str
    step_badges: tuple[str, ...]
    show_captions: bool


@dataclass(frozen=True)
class LayoutMetrics:
    screen_w_ratio: float
    screen_h_ratio: float
    screen_x_ratio: float
    screen_y_ratio: float
    headline_x_ratio: float
    headline_y_ratio: float
    body_y_ratio: float
    headline_width: int
    body_width: int
    headline_size: int
    body_size: int
    badge_top: bool


def build_scene_composition(scene: EditPlanScene, stage: str) -> PreviewSceneComposition:
    headline = composed_headline(scene)
    support = composed_supporting_copy(scene)
    badges = composed_step_badges(scene)
    if "account" in scene.on_screen_text.lower() or "account" in scene.source_excerpt.lower():
        return PreviewSceneComposition("screen-only", headline, support, badges, should_show_captions("screen-only", stage))
    if scene.scene_role == "result":
        return PreviewSceneComposition("dashboard-wide", headline, support, badges, should_show_captions("dashboard-wide", stage))
    if stage == "establish":
        return PreviewSceneComposition("feature-center", headline, support, badges, should_show_captions("feature-center", stage))
    if scene.action_class in {"auth_action", "card_selection"}:
        return PreviewSceneComposition("split-right", headline, support, badges, should_show_captions("split-right", stage))
    return PreviewSceneComposition("feature-center", headline, support, badges, should_show_captions("feature-center", stage))


def should_show_captions(layout_mode: str, stage: str) -> bool:
    if layout_mode == "screen-only":
        return True
    if layout_mode == "feature-center":
        return stage != "establish"
    return False


def composed_headline(scene: EditPlanScene) -> str:
    if scene.action_class == "auth_action":
        return "Start with one clean login, then step into the first learning path."
    if scene.action_class == "card_selection":
        return f"Choose {label_phrase(scene)} to open the guided course path."
    if scene.scene_role == "result":
        return f"See {label_phrase(scene)} come into view as the next state."
    return sentence_case(scene.spoken_line or scene.purpose or scene.title)


def composed_supporting_copy(scene: EditPlanScene) -> str:
    base = scene.purpose or scene.visual_summary or scene.source_excerpt or scene.on_screen_text
    compact = sentence_case(base)
    if scene.action_class == "auth_action":
        return "Keep the journey focused on login first, then transition into the dashboard and course setup."
    if scene.action_class == "card_selection":
        return "Use the selected course card as the handoff from the dashboard into the actual learning flow."
    return compact


def composed_step_badges(scene: EditPlanScene) -> tuple[str, ...]:
    if scene.action_class == "auth_action":
        return ("Step 01 Login", "Step 02 Dashboard", "Step 03 Open Course")
    if scene.action_class == "card_selection":
        return ("Dashboard", "Course Select", "Open Path")
    if scene.scene_role == "result":
        return ("State Ready", "Review Context", "Continue Flow")
    return ("Focused Step", "Action Context", "Polished View")


def label_phrase(scene: EditPlanScene) -> str:
    source = scene.on_screen_text or scene.title or scene.purpose or "this step"
    words = [word.strip(".,") for word in source.split() if word.strip(".,")]
    return " ".join(words[:6]) or "this step"


def sentence_case(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    if cleaned.endswith((".", "!", "?")):
        return cleaned
    return f"{cleaned}."


def composition_filters(
    composition: PreviewSceneComposition,
    scene_number: int,
    quality: str,
    working_dir: Path,
    target_width: int,
    target_height: int,
) -> list[str]:
    metrics = layout_metrics(composition.layout_mode, quality)
    if composition.layout_mode == "split-right":
        return split_layout_filters(composition, metrics, scene_number, working_dir, target_width, target_height)
    if composition.layout_mode == "screen-only":
        return screen_only_filters(composition, metrics, target_width, target_height)
    if composition.layout_mode == "dashboard-wide":
        return dashboard_layout_filters(composition, metrics, scene_number, working_dir, target_width, target_height)
    return centered_layout_filters(composition, metrics, scene_number, working_dir, target_width, target_height)


def split_layout_filters(
    composition: PreviewSceneComposition,
    metrics: LayoutMetrics,
    scene_number: int,
    working_dir: Path,
    target_width: int,
    target_height: int,
) -> list[str]:
    screen_w, screen_h, screen_x, screen_y = screen_bounds(metrics, target_width, target_height)
    filters = [
        f"scale={screen_w}:{screen_h}:force_original_aspect_ratio=decrease",
        centered_panel_pad_filter(screen_x, screen_y, screen_w, screen_h, target_width, target_height),
        panel_filter(screen_x, screen_y, screen_w, screen_h),
    ]
    filters.extend(text_filters(composition, metrics, scene_number, working_dir, target_width, target_height))
    filters.extend(badge_filters(composition.step_badges, metrics, target_width, target_height))
    return filters


def centered_layout_filters(
    composition: PreviewSceneComposition,
    metrics: LayoutMetrics,
    scene_number: int,
    working_dir: Path,
    target_width: int,
    target_height: int,
) -> list[str]:
    screen_w, screen_h, screen_x, screen_y = screen_bounds(metrics, target_width, target_height)
    filters = [
        f"scale={screen_w}:{screen_h}:force_original_aspect_ratio=decrease",
        centered_panel_pad_filter(screen_x, screen_y, screen_w, screen_h, target_width, target_height),
        panel_filter(screen_x, screen_y, screen_w, screen_h),
    ]
    filters.extend(text_filters(composition, metrics, scene_number, working_dir, target_width, target_height))
    filters.extend(badge_filters(composition.step_badges, metrics, target_width, target_height))
    return filters


def screen_only_filters(
    composition: PreviewSceneComposition,
    metrics: LayoutMetrics,
    target_width: int,
    target_height: int,
) -> list[str]:
    screen_w, screen_h, screen_x, screen_y = screen_bounds(metrics, target_width, target_height)
    filters = [
        f"scale={screen_w}:{screen_h}:force_original_aspect_ratio=decrease",
        centered_panel_pad_filter(screen_x, screen_y, screen_w, screen_h, target_width, target_height),
        panel_filter(screen_x, screen_y, screen_w, screen_h),
    ]
    filters.extend(badge_filters(composition.step_badges, metrics, target_width, target_height))
    return filters


def dashboard_layout_filters(
    composition: PreviewSceneComposition,
    metrics: LayoutMetrics,
    scene_number: int,
    working_dir: Path,
    target_width: int,
    target_height: int,
) -> list[str]:
    screen_w, screen_h, screen_x, screen_y = screen_bounds(metrics, target_width, target_height)
    filters = [
        f"scale={screen_w}:{screen_h}:force_original_aspect_ratio=decrease",
        centered_panel_pad_filter(screen_x, screen_y, screen_w, screen_h, target_width, target_height),
        panel_filter(screen_x, screen_y, screen_w, screen_h),
    ]
    filters.extend(text_filters(composition, metrics, scene_number, working_dir, target_width, target_height))
    return filters


def screen_bounds(metrics: LayoutMetrics, target_width: int, target_height: int) -> tuple[int, int, int, int]:
    screen_w = int(target_width * metrics.screen_w_ratio)
    screen_h = int(target_height * metrics.screen_h_ratio)
    screen_x = int(target_width * metrics.screen_x_ratio) if metrics.screen_x_ratio > 0 else int((target_width - screen_w) / 2)
    screen_y = int(target_height * metrics.screen_y_ratio)
    return screen_w, screen_h, screen_x, screen_y


def centered_panel_pad_filter(
    screen_x: int,
    screen_y: int,
    screen_w: int,
    screen_h: int,
    target_width: int,
    target_height: int,
) -> str:
    x_expr = f"{screen_x}+({screen_w}-iw)/2"
    y_expr = f"{screen_y}+({screen_h}-ih)/2"
    return f"pad={target_width}:{target_height}:{x_expr}:{y_expr}:{COMPOSITION_BG}"


def panel_filter(screen_x: int, screen_y: int, screen_w: int, screen_h: int) -> str:
    outer_x = max(screen_x - 18, 0)
    outer_y = max(screen_y - 18, 0)
    outer_w = min(screen_w + 36, 4096)
    outer_h = min(screen_h + 36, 4096)
    return (
        "drawbox="
        f"x={outer_x}:y={outer_y}:w={outer_w}:h={outer_h}:"
        f"color={PANEL_BG}@0.9:t=fill,"
        "drawbox="
        f"x={outer_x}:y={outer_y}:w={outer_w}:h={outer_h}:"
        f"color={PANEL_BORDER}@0.38:t=2"
    )


def text_filters(
    composition: PreviewSceneComposition,
    metrics: LayoutMetrics,
    scene_number: int,
    working_dir: Path,
    target_width: int,
    target_height: int,
) -> list[str]:
    headline_x = int(target_width * metrics.headline_x_ratio)
    headline_y = int(target_height * metrics.headline_y_ratio)
    body_x = headline_x
    body_y = int(target_height * metrics.body_y_ratio)
    headline_file = write_text_asset(working_dir, scene_number, "headline", composition.headline, metrics.headline_width)
    body_file = write_text_asset(working_dir, scene_number, "support", composition.supporting_copy, metrics.body_width)
    return [
        drawtext_filter(headline_file, metrics.headline_size, TEXT_PRIMARY, headline_x, headline_y, box=False),
        drawtext_filter(body_file, metrics.body_size, TEXT_SECONDARY, body_x, body_y, box=False, line_spacing=12),
    ]


def badge_filters(
    badges: tuple[str, ...],
    metrics: LayoutMetrics,
    target_width: int,
    target_height: int,
) -> list[str]:
    font_size = 18 if metrics.headline_size > 50 else 14
    start_x = int(target_width * 0.09)
    y = int(target_height * 0.74) if metrics.badge_top else int(target_height * 0.12)
    filters: list[str] = []
    for index, badge in enumerate(badges[:3]):
        x = start_x + index * int(target_width * 0.16)
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={int(target_width * 0.14)}:h={int(target_height * 0.08)}:"
            f"color={PANEL_BG}@0.82:t=fill"
        )
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={int(target_width * 0.14)}:h={int(target_height * 0.08)}:"
            f"color={PANEL_BORDER}@0.22:t=2"
        )
        filters.append(
            "drawtext="
            f"text='{escaped_text(badge)}':fontsize={font_size}:fontcolor={TEXT_PRIMARY}:"
            f"x={x + 16}:y={y + int(target_height * 0.046)}"
        )
    return filters


def drawtext_filter(
    text_file: Path,
    font_size: int,
    font_color: str,
    x: int,
    y: int,
    *,
    box: bool,
    line_spacing: int = 8,
) -> str:
    box_text = "box=1:boxcolor=black@0.28:boxborderw=14:" if box else ""
    return (
        "drawtext="
        f"textfile='{escape_drawtext_path(text_file)}':expansion=none:"
        f"fontsize={font_size}:fontcolor={font_color}:line_spacing={line_spacing}:"
        f"{box_text}x={x}:y={y}"
    )


def write_text_asset(
    working_dir: Path,
    scene_number: int,
    name: str,
    text: str,
    width: int,
) -> Path:
    path = working_dir / f"scene-{scene_number}-{name}.txt"
    path.write_text(wrapped_text(text, width), encoding="utf-8")
    return path


def wrapped_text(text: str, width: int) -> str:
    compact = " ".join(text.split()).strip()
    if not compact:
        return " "
    return "\n".join(wrap(compact, width=width, break_long_words=False, break_on_hyphens=False)) or compact


def escape_drawtext_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", r"\'")


def escaped_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def layout_metrics(layout_mode: str, quality: str) -> LayoutMetrics:
    premium = quality == "final"
    if layout_mode == "split-right":
        return LayoutMetrics(0.42, 0.52, 0.54, 0.21, 0.09, 0.18, 0.54, 20, 58, 62 if premium else 44, 28 if premium else 20, True)
    if layout_mode == "screen-only":
        return LayoutMetrics(0.86, 0.78, 0.0, 0.08, 0.0, 0.0, 0.0, 0, 0, 0, 0, False)
    if layout_mode == "dashboard-wide":
        return LayoutMetrics(0.88, 0.56, 0.0, 0.24, 0.09, 0.08, 0.16, 30, 72, 56 if premium else 40, 26 if premium else 19, False)
    return LayoutMetrics(0.78, 0.62, 0.0, 0.18, 0.09, 0.08, 0.16, 28, 76, 58 if premium else 42, 26 if premium else 19, False)
