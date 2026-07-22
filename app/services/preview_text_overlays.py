from __future__ import annotations

import re
from pathlib import Path

from app.models.projects import EditPlanScene


def caption_draw_filters(
    scene: EditPlanScene,
    clip_start: float,
    clip_end: float,
    quality: str,
    working_dir: Path,
) -> list[str]:
    if not scene.show_captions:
        return []
    filters: list[str] = []
    for index, caption in enumerate(scene.captions, start=1):
        start = max(caption.start, clip_start) - clip_start
        end = min(caption.end, clip_end) - clip_start
        if end - start <= 0.05:
            continue
        font_size = 34 if quality == "final" else 24
        caption_file = write_caption_text_file(working_dir, scene.scene_number, index, caption.text)
        filters.append(
            "drawtext="
            f"textfile='{escape_drawtext_path(caption_file)}':"
            "expansion=none:"
            f"fontsize={font_size}:fontcolor=white:"
            "line_spacing=8:box=1:boxcolor=black@0.34:boxborderw=20:borderw=1:bordercolor=white@0.08:"
            "x=(w-text_w)/2:y=h-(h*0.15):"
            f"enable='between(t,{round(start, 2)},{round(end, 2)})'"
        )
    return filters


def write_caption_text_file(working_dir: Path, scene_number: int, caption_index: int, text: str) -> Path:
    caption_file = working_dir / f"scene-{scene_number}-caption-{caption_index}.txt"
    caption_file.write_text(normalized_caption_text(text), encoding="utf-8")
    return caption_file


def normalized_caption_text(text: str) -> str:
    preserved = "\n".join(line for line in (re.sub(r"\s+", " ", part).strip() for part in text.replace("\r", "").split("\n")) if line)
    return preserved or " "


def escape_drawtext_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", r"\'")
