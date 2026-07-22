from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.preview_manifest import PreviewManifest, PreviewManifestClip


@dataclass(frozen=True)
class RenderedClipSegment:
    path: Path
    clip: PreviewManifestClip
    profile_name: str


@dataclass(frozen=True)
class ProxyPreviewRenderReport:
    manifest: PreviewManifest
    selected_profile: str
    rendered_clips: list[RenderedClipSegment]
