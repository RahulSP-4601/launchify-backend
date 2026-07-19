import React from "react";

import { FocusBox } from "./types";

type ViewportMetrics = {
  left: number;
  top: number;
  width: number;
  height: number;
  canvasWidth: number;
  canvasHeight: number;
  chromeOffset: number;
};

type HighlightVariant = "spotlight" | "soft-glow" | "ambient-lift" | "ambient";

export const FocusMatte: React.FC<{
  fastPreview: boolean;
  focusBox: FocusBox | null;
  sceneRole: "action" | "result" | "explanation";
  viewport: ViewportMetrics;
}> = ({ fastPreview, focusBox, sceneRole, viewport }) => {
  if (!focusBox) {
    return null;
  }
  const rects = focusMatteRects(focusBox, viewport, fastPreview, sceneRole);
  return (
    <>
      {rects.map((style, index) => (
        <div key={index} style={style} />
      ))}
      <div style={focusHaloStyle(focusBox, viewport, fastPreview, sceneRole)} />
    </>
  );
};

export const HighlightBadge: React.FC<{
  fastPreview: boolean;
  focusBox: FocusBox | null;
  intensity: number;
  label: string;
  pulse: number;
  style: string;
  anchor: { left: string; top: string };
  viewport: ViewportMetrics;
}> = ({ fastPreview, focusBox, intensity, label, pulse, style, anchor, viewport }) => {
  const variant = normalizeVariant(style);
  return (
    <div style={highlightPosition(anchor, viewport, variant)}>
      <div style={highlightHaloStyle(fastPreview, focusBox, intensity, pulse, variant, viewport)} />
      <div style={highlightChipStyle(fastPreview, intensity, pulse, variant)}>{label}</div>
    </div>
  );
};

function normalizeVariant(style: string): HighlightVariant {
  if (style === "soft-glow" || style === "ambient-lift" || style === "ambient") {
    return style;
  }
  return "spotlight";
}

function focusMetrics(focusBox: FocusBox, viewport: ViewportMetrics) {
  const width = viewport.width * viewport.canvasWidth;
  const height = viewport.height * viewport.canvasHeight;
  const usableHeight = height - viewport.chromeOffset;
  const left = Math.max(0, focusBox.x * width - 14);
  const top = Math.max(0, viewport.chromeOffset + focusBox.y * usableHeight - 14);
  const boxWidth = Math.min(width - left, focusBox.width * width + 28);
  const boxHeight = Math.min(height - top, focusBox.height * usableHeight + 28);
  return { left, top, boxWidth, boxHeight, width, height };
}

function focusMatteRects(
  focusBox: FocusBox,
  viewport: ViewportMetrics,
  fastPreview: boolean,
  sceneRole: "action" | "result" | "explanation",
): React.CSSProperties[] {
  const { left, top, boxWidth, boxHeight, width, height } = focusMetrics(focusBox, viewport);
  const opacity = matteOpacity(fastPreview, sceneRole);
  const overlay = `rgba(2, 6, 23, ${opacity})`;
  return [
    { background: overlay, height: top, left: 0, position: "absolute", top: 0, width, zIndex: 2 },
    { background: overlay, height: height - (top + boxHeight), left: 0, position: "absolute", top: top + boxHeight, width, zIndex: 2 },
    { background: overlay, height: boxHeight, left: 0, position: "absolute", top, width: left, zIndex: 2 },
    { background: overlay, height: boxHeight, left: left + boxWidth, position: "absolute", top, width: width - (left + boxWidth), zIndex: 2 },
  ];
}

function matteOpacity(fastPreview: boolean, sceneRole: "action" | "result" | "explanation") {
  if (sceneRole === "explanation") {
    return fastPreview ? 0.16 : 0.2;
  }
  if (sceneRole === "result") {
    return fastPreview ? 0.2 : 0.26;
  }
  return fastPreview ? 0.24 : 0.32;
}

function focusHaloStyle(
  focusBox: FocusBox,
  viewport: ViewportMetrics,
  fastPreview: boolean,
  sceneRole: "action" | "result" | "explanation",
): React.CSSProperties {
  const { left, top, boxWidth, boxHeight } = focusMetrics(focusBox, viewport);
  const accent = sceneRole === "result" ? "245, 158, 11" : "34, 211, 238";
  const shadow = fastPreview ? 18 : 26;
  return {
    background: `radial-gradient(circle at center, rgba(${accent}, 0.12), rgba(${accent}, 0.02) 58%, transparent 74%)`,
    border: `1px solid rgba(${accent}, ${sceneRole === "action" ? 0.14 : 0.08})`,
    borderRadius: 30,
    boxShadow: `0 0 ${shadow}px rgba(${accent}, ${sceneRole === "action" ? 0.22 : 0.12})`,
    height: boxHeight,
    left,
    pointerEvents: "none",
    position: "absolute",
    top,
    width: boxWidth,
    zIndex: 3,
  };
}

function highlightPosition(
  anchor: { left: string; top: string },
  viewport: ViewportMetrics,
  variant: HighlightVariant,
): React.CSSProperties {
  const left = Number.parseFloat(anchor.left) / 100;
  const top = Number.parseFloat(anchor.top) / 100;
  const chromeOffsetPercent = (viewport.chromeOffset / viewport.canvasHeight) * viewport.height;
  return {
    left: `${((viewport.left + left * viewport.width) * 100).toFixed(2)}%`,
    position: "absolute",
    top: `${((viewport.top + chromeOffsetPercent + top * (viewport.height - chromeOffsetPercent)) * 100).toFixed(2)}%`,
    transform: variant === "ambient-lift" ? "translate(-6%, -18%)" : "translate(-8%, -12%)",
    zIndex: 7,
  };
}

function highlightHaloStyle(
  fastPreview: boolean,
  focusBox: FocusBox | null,
  intensity: number,
  pulse: number,
  variant: HighlightVariant,
  viewport: ViewportMetrics,
): React.CSSProperties {
  const { width, height } = haloSize(focusBox, viewport, variant);
  const blur = fastPreview ? 18 : 24;
  const scale = 0.94 + pulse * 0.12;
  const palette = highlightPalette(variant);
  return {
    background: palette.fill,
    border: palette.border,
    borderRadius: focusBox ? 28 : 9999,
    boxShadow: `${palette.outerGlow(blur, intensity)}, ${palette.innerGlow(intensity)}`,
    height,
    opacity: 0.68 + intensity * 0.2,
    transform: `scale(${scale.toFixed(3)})`,
    transformOrigin: "center center",
    width,
  };
}

function haloSize(focusBox: FocusBox | null, viewport: ViewportMetrics, variant: HighlightVariant) {
  const canvasWidth = viewport.width * viewport.canvasWidth;
  const canvasHeight = viewport.height * viewport.canvasHeight;
  const width = focusBox ? Math.max(108, Math.round(focusBox.width * canvasWidth)) : 132;
  const height = focusBox ? Math.max(64, Math.round(focusBox.height * canvasHeight)) : 120;
  if (variant === "soft-glow") {
    return { width: width + 16, height: height + 10 };
  }
  if (variant === "ambient-lift") {
    return { width: width + 26, height: height + 18 };
  }
  return { width, height };
}

function highlightPalette(variant: HighlightVariant) {
  if (variant === "ambient") {
    return {
      fill: "radial-gradient(circle at center, rgba(255,255,255,0.1), rgba(255,255,255,0.02) 66%, transparent 80%)",
      border: "1px solid rgba(255,255,255,0.12)",
      innerGlow: (intensity: number) => `inset 0 0 18px rgba(255,255,255,${0.06 + intensity * 0.04})`,
      outerGlow: (blur: number, intensity: number) => `0 0 ${blur}px rgba(255,255,255,${0.08 + intensity * 0.04})`,
    };
  }
  if (variant === "soft-glow") {
    return {
      fill: "radial-gradient(circle at center, rgba(56,189,248,0.22), rgba(14,165,233,0.08) 62%, transparent 82%)",
      border: "1px solid rgba(125,211,252,0.16)",
      innerGlow: (intensity: number) => `inset 0 0 22px rgba(103,232,249,${0.09 + intensity * 0.08})`,
      outerGlow: (blur: number, intensity: number) => `0 0 ${blur + 6}px rgba(34,211,238,${0.16 + intensity * 0.08})`,
    };
  }
  if (variant === "ambient-lift") {
    return {
      fill: "linear-gradient(135deg, rgba(250,204,21,0.18), rgba(249,115,22,0.08))",
      border: "1px solid rgba(253,224,71,0.18)",
      innerGlow: (intensity: number) => `inset 0 0 16px rgba(253,224,71,${0.08 + intensity * 0.06})`,
      outerGlow: (blur: number, intensity: number) => `0 0 ${blur}px rgba(251,191,36,${0.14 + intensity * 0.08})`,
    };
  }
  return {
    fill: "radial-gradient(circle at center, rgba(34,211,238,0.16), rgba(14,165,233,0.04) 64%, transparent 82%)",
    border: "2px solid rgba(34,211,238,0.42)",
    innerGlow: (intensity: number) => `inset 0 0 16px rgba(103,232,249,${0.07 + intensity * 0.08})`,
    outerGlow: (blur: number, intensity: number) => `0 0 ${blur}px rgba(34,211,238,${0.14 + intensity * 0.1})`,
  };
}

function highlightChipStyle(
  fastPreview: boolean,
  intensity: number,
  pulse: number,
  variant: HighlightVariant,
): React.CSSProperties {
  const palette = chipPalette(variant);
  return {
    backdropFilter: "blur(14px)",
    background: palette.background,
    border: palette.border,
    borderRadius: 9999,
    boxShadow: fastPreview
      ? `0 8px 18px rgba(15,23,42,0.14), 0 0 0 1px rgba(255,255,255,0.03)`
      : `0 12px 28px rgba(15,23,42,0.18), 0 0 0 1px rgba(255,255,255,0.04)`,
    color: palette.color,
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 15,
    fontWeight: 700,
    marginTop: variant === "ambient-lift" ? 10 : 8,
    opacity: 0.82 + intensity * 0.18,
    padding: "9px 14px",
    transform: `translateY(${(1 - pulse) * 4}px)`,
    whiteSpace: "nowrap",
  };
}

function chipPalette(variant: HighlightVariant) {
  if (variant === "ambient-lift") {
    return {
      background: "linear-gradient(135deg, rgba(23,37,84,0.88), rgba(15,23,42,0.84))",
      border: "1px solid rgba(250,204,21,0.32)",
      color: "#fef3c7",
    };
  }
  if (variant === "ambient") {
    return {
      background: "rgba(15,23,42,0.74)",
      border: "1px solid rgba(255,255,255,0.18)",
      color: "#f8fafc",
    };
  }
  return {
    background: "linear-gradient(135deg, rgba(8,47,73,0.9), rgba(15,23,42,0.86))",
    border: "1px solid rgba(125,211,252,0.34)",
    color: "#e0f2fe",
  };
}
