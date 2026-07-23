import { interpolate } from "remotion";

import { FocusBox, RenderHighlight, RenderPayload, RenderScene, RenderZoom, TimelineScene } from "./types";

export const titleStyles = {
  eyebrow: {
    color: "#93c5fd",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 22,
    fontWeight: 600,
    letterSpacing: "0.28em",
    textTransform: "uppercase" as const,
  },
  headline: {
    color: "#eff6ff",
    fontFamily: "\"Iowan Old Style\", Georgia, serif",
    fontSize: 78,
    fontWeight: 700,
    lineHeight: 1.02,
  },
  body: {
    color: "#dbeafe",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 26,
    lineHeight: 1.45,
  },
};

export function totalFrames(payload: RenderPayload) {
  const fps = payload.dimensions.fps;
  const totalSeconds =
    payload.introDurationSeconds +
    timelineDurationSeconds(payload) +
    payload.outroDurationSeconds;
  return Math.max(1, Math.ceil(totalSeconds * fps));
}

export function sceneDurationFrames(scene: RenderScene | TimelineScene, fps: number) {
  const duration = scene.render_duration_seconds ?? timelineSceneDuration(scene);
  return Math.max(1, Math.round(duration * fps));
}

export function timelineDurationSeconds(payload: RenderPayload) {
  return payload.timeline?.total_duration_seconds ?? payload.editPlan.total_duration_seconds;
}

export function timelineSceneDuration(scene: RenderScene | TimelineScene) {
  if ("editor_end" in scene) {
    return scene.editor_end - scene.editor_start;
  }
  return scene.end - scene.start;
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
      translateX: 0,
      translateY: 0,
    };
  }
  const confidenceScale = 1 + (activeZoom.scale - 1) * activeZoom.confidence;
  const intensity = easedMotionIntensity(activeZoom, localSeconds);
  return {
    scale: 1 + (confidenceScale - 1) * intensity,
    origin: focusBoxToOrigin(activeZoom.focus_box) ?? focusRegionToOrigin(activeZoom.focus_region),
    translateX: activeZoom.x_offset * intensity,
    translateY: activeZoom.y_offset * intensity,
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
    pulse: highlightPulse(highlight, localSeconds),
  };
}

export function activeFocusBox(scene: RenderScene, localSeconds: number) {
  const activeHighlight = scene.highlights.find((item) => item.start <= localSeconds && item.end >= localSeconds);
  if (activeHighlight?.focus_box) {
    return activeHighlight.focus_box;
  }
  const activeZoom = scene.zooms.find((zoom) => zoom.start <= localSeconds && zoom.end >= localSeconds);
  return activeZoom?.focus_box ?? null;
}

export function motionOpacity(frame: number, durationInFrames: number) {
  const safeDuration = Math.max(1, durationInFrames);
  if (safeDuration <= 2) {
    return interpolate(frame, [0, safeDuration], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  }
  const fadeFrames = Math.max(1, Math.min(8, Math.floor(safeDuration / 3)));
  const plateauEnd = Math.min(safeDuration - 1, Math.max(fadeFrames + 1, safeDuration - fadeFrames));
  return interpolate(frame, [0, fadeFrames, plateauEnd, safeDuration], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

export function transitionStyle(scene: RenderScene, frame: number, fps: number) {
  const transitionFrames = Math.max(1, Math.round(scene.transition_duration_seconds * fps));
  const entry = interpolate(frame, [0, transitionFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const translateY = scene.transition_style === "slide-up" ? (1 - entry) * 40 : 0;
  const focusScale = scene.transition_style === "focus-push" ? 1 + (1 - entry) * 0.04 : 1;
  return { opacity: entry, translateY, focusScale };
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

function motionIntensity(start: number, end: number, current: number, holdRatio: number) {
  const duration = Math.max(end - start, 0.01);
  const progress = Math.min(Math.max((current - start) / duration, 0), 1);
  const entry = Math.max(0.12, 0.22 - holdRatio * 0.08);
  const exitStart = Math.min(0.88, 0.78 + holdRatio * 0.08);
  if (progress <= entry) {
    return progress / entry;
  }
  if (progress >= exitStart) {
    return (1 - progress) / (1 - exitStart);
  }
  return 1;
}

function highlightPulse(highlight: RenderHighlight, localSeconds: number) {
  const raw = motionIntensity(highlight.start, highlight.end, localSeconds, 0.45);
  return raw < 0.5 ? 2 * raw * raw : 1 - Math.pow(-2 * raw + 2, 2) / 2;
}

function easedMotionIntensity(zoom: RenderZoom, localSeconds: number) {
  const raw = motionIntensity(zoom.start, zoom.end, localSeconds, zoom.hold_ratio);
  const smoothed = raw < 0.5 ? 4 * raw * raw * raw : 1 - Math.pow(-2 * raw + 2, 3) / 2;
  if (zoom.easing === "ease-in") {
    return Math.pow(smoothed, 1.18);
  }
  if (zoom.easing === "ease-out") {
    return 1 - Math.pow(1 - smoothed, 1.18);
  }
  if (zoom.easing === "ease-in-out") {
    return smoothed;
  }
  return raw * (1 - zoom.smoothing) + smoothed * zoom.smoothing;
}
