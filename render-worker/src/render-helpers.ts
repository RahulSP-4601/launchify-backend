import { interpolate } from "remotion";

import { FocusBox, RenderHighlight, RenderPayload, RenderScene, RenderZoom } from "./types";

export const titleStyles = {
  eyebrow: {
    color: "#7dd3fc",
    fontFamily: "Arial, sans-serif",
    fontSize: 24,
    letterSpacing: "0.24em",
    textTransform: "uppercase" as const,
  },
  headline: {
    color: "#f8fafc",
    fontFamily: "Georgia, serif",
    fontSize: 72,
    fontWeight: 700,
    lineHeight: 1.05,
  },
  body: {
    color: "#cbd5e1",
    fontFamily: "Arial, sans-serif",
    fontSize: 28,
    lineHeight: 1.5,
  },
};

export function totalFrames(payload: RenderPayload) {
  const fps = payload.dimensions.fps;
  const totalSeconds =
    payload.introDurationSeconds +
    payload.editPlan.total_duration_seconds +
    payload.outroDurationSeconds;
  return Math.max(1, Math.ceil(totalSeconds * fps));
}

export function sceneDurationFrames(scene: RenderScene, fps: number) {
  return Math.max(1, Math.round((scene.end - scene.start) * fps));
}

export function activeCaption(scene: RenderScene, localSeconds: number) {
  return scene.captions.find((caption) => caption.start <= localSeconds && caption.end >= localSeconds) ?? null;
}

export function zoomTransform(zooms: RenderZoom[], localSeconds: number) {
  const activeZoom = zooms.find((zoom) => zoom.start <= localSeconds && zoom.end >= localSeconds);
  if (!activeZoom) {
    return {
      scale: 1,
      origin: "50% 50%",
    };
  }
  const confidenceScale = 1 + (activeZoom.scale - 1) * activeZoom.confidence;
  const intensity = motionIntensity(activeZoom.start, activeZoom.end, localSeconds);
  return {
    scale: 1 + (confidenceScale - 1) * intensity,
    origin: focusBoxToOrigin(activeZoom.focus_box) ?? focusRegionToOrigin(activeZoom.focus_region),
  };
}

export function spotlightStyle(highlights: RenderHighlight[], localSeconds: number) {
  const highlight = highlights.find((item) => item.start <= localSeconds && item.end >= localSeconds);
  if (!highlight) {
    return null;
  }
  return {
    label: highlight.label,
    style: highlight.style,
    anchor: focusBoxToAnchor(highlight.focus_box) ?? highlightAnchor(highlight.anchor_region),
    intensity: highlight.confidence,
    focusBox: highlight.focus_box,
  };
}

export function motionOpacity(frame: number, durationInFrames: number) {
  return interpolate(frame, [0, 8, durationInFrames - 8, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

function focusRegionToOrigin(region: string) {
  switch (region) {
    case "top-left":
      return "18% 18%";
    case "top-center":
      return "50% 18%";
    case "top-right":
      return "82% 18%";
    case "bottom-right":
      return "82% 82%";
    default:
      return "50% 50%";
  }
}

function highlightAnchor(region: string) {
  if (region === "top-right") {
    return { top: "16%", left: "68%" };
  }
  if (region === "top-left") {
    return { top: "20%", left: "12%" };
  }
  if (region === "top-center") {
    return { top: "15%", left: "42%" };
  }
  if (region === "bottom-right") {
    return { top: "70%", left: "66%" };
  }
  return { top: "42%", left: "42%" };
}

function focusBoxToOrigin(box: FocusBox | null) {
  if (!box) {
    return null;
  }
  const centerX = `${((box.x + box.width / 2) * 100).toFixed(2)}%`;
  const centerY = `${((box.y + box.height / 2) * 100).toFixed(2)}%`;
  return `${centerX} ${centerY}`;
}

function focusBoxToAnchor(box: FocusBox | null) {
  if (!box) {
    return null;
  }
  return {
    left: `${(box.x * 100).toFixed(2)}%`,
    top: `${(box.y * 100).toFixed(2)}%`,
  };
}

function motionIntensity(start: number, end: number, current: number) {
  const duration = Math.max(end - start, 0.01);
  const progress = Math.min(Math.max((current - start) / duration, 0), 1);
  if (progress <= 0.18) {
    return progress / 0.18;
  }
  if (progress >= 0.82) {
    return (1 - progress) / 0.18;
  }
  return 1;
}
