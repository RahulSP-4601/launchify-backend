import React from "react";
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import {
  activeCaption,
  activeFocusBox,
  motionOpacity,
  sceneDurationFrames,
  spotlightStyle,
  transitionStyle,
  titleStyles,
  totalFrames,
  zoomTransform,
} from "./render-helpers";
import { RenderPayload, RenderScene } from "./types";

export const LaunchifyRender: React.FC<RenderPayload> = (payload) => {
  const totalDuration = totalFrames(payload);
  const introFrames = Math.round(payload.introDurationSeconds * payload.dimensions.fps);
  const outroFrames = Math.round(payload.outroDurationSeconds * payload.dimensions.fps);
  const fastPreview = isFastPreview(payload);

  return (
    <AbsoluteFill style={shellStyle()}>
      <AudioTrack introFrames={introFrames} payload={payload} />
      <Sequence durationInFrames={introFrames}>
        <IntroCard payload={payload} fastPreview={fastPreview} />
      </Sequence>
      <SceneTrack fastPreview={fastPreview} introFrames={introFrames} payload={payload} />
      <Sequence from={totalDuration - outroFrames} durationInFrames={outroFrames}>
        <OutroCard payload={payload} fastPreview={fastPreview} />
      </Sequence>
    </AbsoluteFill>
  );
};

const IntroCard: React.FC<{ payload: RenderPayload; fastPreview: boolean }> = ({ payload, fastPreview }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={heroShell(motionOpacity(frame, durationInFrames), fastPreview)}>
      <div style={heroBadgeStyle()}>AI Product Video</div>
      <h1 style={heroTitleStyle(fastPreview)}>{payload.editPlan.render_spec.title_card}</h1>
      <p style={heroBodyStyle(fastPreview)}>
        {payload.productName} walkthrough, cleaned up with guided focus, captions, and launch-ready framing.
      </p>
    </AbsoluteFill>
  );
};

const OutroCard: React.FC<{ payload: RenderPayload; fastPreview: boolean }> = ({ payload, fastPreview }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={heroShell(motionOpacity(frame, durationInFrames), fastPreview)}>
      <div style={heroBadgeStyle()}>Ready To Share</div>
      <h2 style={heroTitleStyle(fastPreview)}>{payload.editPlan.render_spec.cta}</h2>
      <p style={heroBodyStyle(fastPreview)}>
        {payload.productName} is ready to publish as a polished demo, walkthrough, or launch asset.
      </p>
    </AbsoluteFill>
  );
};

const SceneTrack: React.FC<{ fastPreview: boolean; introFrames: number; payload: RenderPayload }> = ({
  fastPreview,
  introFrames,
  payload,
}) => {
  let sceneOffset = introFrames;
  return (
    <>
      {payload.editPlan.scenes.map((scene) => {
        const durationInFrames = sceneDurationFrames(scene, payload.dimensions.fps);
        const sequence = (
          <Sequence key={scene.scene_number} from={sceneOffset} durationInFrames={durationInFrames}>
            <SceneComposition fastPreview={fastPreview} payload={payload} scene={scene} />
          </Sequence>
        );
        sceneOffset += durationInFrames;
        return sequence;
      })}
    </>
  );
};

const SceneComposition: React.FC<{ fastPreview: boolean; payload: RenderPayload; scene: RenderScene }> = ({
  fastPreview,
  payload,
  scene,
}) => {
  const frame = useCurrentFrame();
  const localSeconds = scene.start + frame / payload.dimensions.fps;
  const caption = activeCaption(scene, localSeconds);
  const focusBox = activeFocusBox(scene, localSeconds);
  const zoom = zoomTransform(scene.zooms, localSeconds);
  const spotlight = spotlightStyle(scene.highlights, localSeconds);
  const transition = transitionStyle(scene, frame, payload.dimensions.fps);

  return (
    <AbsoluteFill style={sceneCanvasStyle(transition.opacity, transition.translateY)}>
      <SceneGlow fastPreview={fastPreview} />
      <ViewportFrame fastPreview={fastPreview}>
        <VideoLayer
          payload={payload}
          scene={scene}
          viewport={viewportMetrics(payload.dimensions)}
          zoom={zoom}
          transitionScale={transition.focusScale}
        />
        <FocusMatte fastPreview={fastPreview} focusBox={focusBox} viewport={viewportMetrics(payload.dimensions)} />
        <GradientMask fastPreview={fastPreview} />
        <BrowserChrome payload={payload} viewport={viewportMetrics(payload.dimensions)} />
      </ViewportFrame>
      <SceneMeta payload={payload} scene={scene} fastPreview={fastPreview} />
      {caption ? <CaptionPill payload={payload} caption={caption} /> : null}
      {spotlight ? (
        <HighlightBadge
          fastPreview={fastPreview}
          label={spotlight.label}
          anchor={spotlight.anchor}
          focusBox={spotlight.focusBox}
          intensity={spotlight.intensity}
          viewport={viewportMetrics(payload.dimensions)}
        />
      ) : null}
    </AbsoluteFill>
  );
};

const ViewportFrame: React.FC<{ children: React.ReactNode; fastPreview: boolean }> = ({ children, fastPreview }) => (
  <div style={viewportFrameStyle(fastPreview)}>
    <div style={viewportInnerStyle()}>{children}</div>
  </div>
);

const VideoLayer: React.FC<{
  payload: RenderPayload;
  scene: RenderScene;
  viewport: {
    chromeOffset: number;
  };
  zoom: { origin: string; scale: number; translateX: number; translateY: number };
  transitionScale: number;
}> = ({ payload, scene, viewport, zoom, transitionScale }) => {
  return (
    <AbsoluteFill style={videoSurfaceStyle(viewport.chromeOffset)}>
      <OffthreadVideo
        src={payload.sourceVideoPath ?? ""}
        startFrom={Math.round(scene.start * payload.dimensions.fps)}
        endAt={Math.round(scene.end * payload.dimensions.fps)}
        volume={sourceVideoVolume(payload)}
        style={{
          height: "100%",
          objectFit: "cover",
          transform: `translate(${(zoom.translateX * 100).toFixed(2)}%, ${(zoom.translateY * 100).toFixed(2)}%) scale(${(zoom.scale * transitionScale).toFixed(3)})`,
          transformOrigin: zoom.origin,
          width: "100%",
        }}
      />
    </AbsoluteFill>
  );
};

const BrowserChrome: React.FC<{
  payload: RenderPayload;
  viewport: {
    chromeOffset: number;
  };
}> = ({ payload, viewport }) => (
  <div style={browserChromeStyle(viewport.chromeOffset)}>
    <div style={browserDotsStyle()}>
      <span style={{ ...browserDotStyle(), background: "#fb7185" }} />
      <span style={{ ...browserDotStyle(), background: "#fbbf24" }} />
      <span style={{ ...browserDotStyle(), background: "#4ade80" }} />
    </div>
    <div style={browserTitleStyle()}>{payload.productName} walkthrough</div>
  </div>
);

const AudioTrack: React.FC<{ introFrames: number; payload: RenderPayload }> = ({ introFrames, payload }) => {
  if (!payload.voiceoverAudioPath || payload.voiceover.mode === "original") {
    return null;
  }
  return (
    <Sequence from={introFrames}>
      <Audio src={payload.voiceoverAudioPath} volume={voiceoverVolume(payload)} />
    </Sequence>
  );
};

const GradientMask: React.FC<{ fastPreview: boolean }> = ({ fastPreview }) => (
  <AbsoluteFill style={gradientMaskStyle(fastPreview)} />
);

const FocusMatte: React.FC<{
  fastPreview: boolean;
  focusBox: { x: number; y: number; width: number; height: number } | null;
  viewport: {
    width: number;
    height: number;
    canvasWidth: number;
    canvasHeight: number;
    chromeOffset: number;
  };
}> = ({ fastPreview, focusBox, viewport }) => {
  if (!focusBox) {
    return null;
  }
  const rects = focusMatteRects(focusBox, viewport, fastPreview);
  return (
    <>
      {rects.map((style, index) => (
        <div key={index} style={style} />
      ))}
      <div style={focusFrameStyle(focusBox, viewport, fastPreview)} />
    </>
  );
};

const SceneGlow: React.FC<{ fastPreview: boolean }> = ({ fastPreview }) => (
  <AbsoluteFill style={sceneGlowStyle(fastPreview)} />
);

const SceneMeta: React.FC<{ fastPreview: boolean; payload: RenderPayload; scene: RenderScene }> = ({
  fastPreview,
  payload,
  scene,
}) => {
  const accent = payload.templateConfig.theme === "bold" ? "#fb7185" : "#67e8f9";
  return (
    <div style={sceneMetaStyle(fastPreview)}>
      <div style={{ ...sceneNumberChipStyle(), color: accent, borderColor: `${accent}55` }}>Scene {scene.scene_number}</div>
      <h3 style={sceneTitleStyle()}>{scene.purpose}</h3>
      <p style={sceneSubtitleStyle()}>{scene.on_screen_text || scene.visual_summary}</p>
    </div>
  );
};

const CaptionPill: React.FC<{
  payload: RenderPayload;
  caption: RenderScene["captions"][number];
}> = ({ payload, caption }) => {
  return (
    <div style={captionStyle(payload)}>
      <CaptionText
        emphasisWords={caption.emphasis_words}
        profile={payload.templateConfig.caption_profile}
        text={caption.text}
        variant={caption.variant}
      />
    </div>
  );
};

const HighlightBadge: React.FC<{
  fastPreview: boolean;
  label: string;
  anchor: { left: string; top: string };
  focusBox: { x: number; y: number; width: number; height: number } | null;
  intensity: number;
  viewport: {
    left: number;
    top: number;
    width: number;
    height: number;
    chromeOffset: number;
  };
}> = ({ fastPreview, label, anchor, focusBox, intensity, viewport }) => {
  return (
    <div style={highlightStyle(anchor, viewport)}>
      <div style={highlightRingStyle(fastPreview, intensity, focusBox, viewport)} />
      <div style={highlightLabelStyle(fastPreview, intensity)}>{label}</div>
    </div>
  );
};

function isFastPreview(payload: RenderPayload) {
  return payload.quality === "preview";
}

function shellStyle(): React.CSSProperties {
  return {
    background:
      "radial-gradient(circle at 10% 20%, rgba(103, 232, 249, 0.18), transparent 24%), radial-gradient(circle at 88% 16%, rgba(244, 114, 182, 0.18), transparent 28%), linear-gradient(135deg, #f8fafc 0%, #e2e8f0 38%, #dbeafe 100%)",
  };
}

function viewportMetrics(dimensions: { width: number; height: number }) {
  const frameHeight = dimensions.height * 0.82;
  const chromeOffset = Math.round(frameHeight * (42 / 720));
  return {
    left: 0.045,
    top: 0.09,
    width: 0.91,
    height: 0.82,
    chromeOffset,
    canvasWidth: dimensions.width,
    canvasHeight: dimensions.height,
  };
}

function heroShell(opacity: number, fastPreview: boolean): React.CSSProperties {
  return {
    alignItems: "flex-start",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    opacity,
    padding: fastPreview ? "84px 88px" : "112px 124px",
  };
}

function heroBadgeStyle(): React.CSSProperties {
  return {
    background: "rgba(15, 23, 42, 0.08)",
    border: "1px solid rgba(15, 23, 42, 0.1)",
    borderRadius: 9999,
    color: "#0f172a",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 20,
    fontWeight: 600,
    letterSpacing: "0.08em",
    padding: "12px 18px",
    textTransform: "uppercase",
  };
}

function heroTitleStyle(fastPreview: boolean): React.CSSProperties {
  return {
    ...titleStyles.headline,
    color: "#0f172a",
    fontSize: fastPreview ? 54 : 72,
    marginTop: 28,
    maxWidth: 920,
  };
}

function heroBodyStyle(fastPreview: boolean): React.CSSProperties {
  return {
    ...titleStyles.body,
    color: "rgba(15, 23, 42, 0.72)",
    fontSize: fastPreview ? 26 : 30,
    marginTop: 22,
    maxWidth: 820,
  };
}

function sceneCanvasStyle(opacity: number, translateY: number): React.CSSProperties {
  return {
    opacity,
    transform: `translateY(${translateY}px)`,
  };
}

function viewportFrameStyle(fastPreview: boolean): React.CSSProperties {
  return {
    background: "rgba(255,255,255,0.5)",
    border: "1px solid rgba(255,255,255,0.86)",
    borderRadius: 34,
    boxShadow: fastPreview
      ? "0 14px 30px rgba(15, 23, 42, 0.12)"
      : "0 18px 42px rgba(15, 23, 42, 0.14)",
    height: "82%",
    left: "4.5%",
    overflow: "hidden",
    position: "absolute",
    top: "9%",
    width: "91%",
  };
}

function viewportInnerStyle(): React.CSSProperties {
  return {
    background: "#020617",
    borderRadius: 28,
    height: "100%",
    overflow: "hidden",
    position: "relative",
    width: "100%",
  };
}

function videoSurfaceStyle(chromeOffset: number): React.CSSProperties {
  return {
    bottom: 0,
    left: 0,
    overflow: "hidden",
    position: "absolute",
    right: 0,
    top: chromeOffset,
  };
}

function browserChromeStyle(chromeOffset: number): React.CSSProperties {
  return {
    alignItems: "center",
    background: "linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,250,252,0.92))",
    borderBottom: "1px solid rgba(148, 163, 184, 0.22)",
    display: "flex",
    gap: 18,
    height: chromeOffset,
    left: 0,
    padding: "0 16px",
    position: "absolute",
    right: 0,
    top: 0,
    zIndex: 3,
  };
}

function browserDotsStyle(): React.CSSProperties {
  return { display: "flex", gap: 8 };
}

function browserDotStyle(): React.CSSProperties {
  return { borderRadius: 9999, display: "block", height: 10, width: 10 };
}

function browserTitleStyle(): React.CSSProperties {
  return {
    color: "rgba(15, 23, 42, 0.72)",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 15,
    fontWeight: 600,
  };
}

function sceneGlowStyle(fastPreview: boolean): React.CSSProperties {
  return {
    background: fastPreview
      ? "radial-gradient(circle at 20% 15%, rgba(34,211,238,0.08), transparent 20%), radial-gradient(circle at 82% 80%, rgba(59,130,246,0.08), transparent 22%)"
      : "radial-gradient(circle at 18% 14%, rgba(34,211,238,0.12), transparent 22%), radial-gradient(circle at 84% 78%, rgba(59,130,246,0.12), transparent 26%)",
  };
}

function gradientMaskStyle(fastPreview: boolean): React.CSSProperties {
  return {
    background: fastPreview
      ? "linear-gradient(180deg, rgba(2,6,23,0.08) 0%, rgba(2,6,23,0.02) 28%, rgba(2,6,23,0.24) 100%)"
      : "linear-gradient(180deg, rgba(2,6,23,0.1) 0%, rgba(2,6,23,0.03) 28%, rgba(2,6,23,0.28) 100%)",
  };
}

function sceneMetaStyle(fastPreview: boolean): React.CSSProperties {
  return {
    left: 70,
    maxWidth: fastPreview ? 400 : 460,
    position: "absolute",
    top: 74,
    zIndex: 5,
  };
}

function sceneNumberChipStyle(): React.CSSProperties {
  return {
    background: "rgba(255,255,255,0.68)",
    border: "1px solid rgba(103,232,249,0.3)",
    borderRadius: 9999,
    display: "inline-flex",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: "0.14em",
    padding: "10px 14px",
    textTransform: "uppercase",
  };
}

function sceneTitleStyle(): React.CSSProperties {
  return {
    color: "#0f172a",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 30,
    fontWeight: 700,
    letterSpacing: "-0.03em",
    lineHeight: 1.12,
    margin: "14px 0 8px",
    textShadow: "0 2px 10px rgba(255,255,255,0.35)",
  };
}

function sceneSubtitleStyle(): React.CSSProperties {
  return {
    color: "rgba(15, 23, 42, 0.64)",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 17,
    fontWeight: 500,
    lineHeight: 1.28,
    margin: 0,
    maxWidth: 400,
  };
}

function captionStyle(payload: RenderPayload): React.CSSProperties {
  const fastPreview = isFastPreview(payload);
  return {
    background: "rgba(9, 14, 28, 0.74)",
    border: "1px solid rgba(148,163,184,0.26)",
    borderRadius: 20,
    bottom: 26,
    boxShadow: fastPreview
      ? "0 10px 26px rgba(15, 23, 42, 0.16)"
      : "0 16px 40px rgba(15, 23, 42, 0.2)",
    left: "50%",
    maxWidth: "58%",
    padding: "14px 18px",
    position: "absolute",
    transform: "translateX(-50%)",
    zIndex: 6,
  };
}

function captionTextStyle(profile: string, variant: string): React.CSSProperties {
  const fontSize = profile === "cinematic" || variant === "hero" ? 24 : profile === "minimal" ? 18 : 20;
  const fontFamily = profile === "cinematic" ? "\"Iowan Old Style\", Georgia, serif" : "\"Avenir Next\", \"Segoe UI\", sans-serif";
  const fontWeight = variant === "hero" ? 700 : 600;
  return {
    color: "rgba(248,250,252,0.98)",
    fontFamily,
    fontSize,
    fontWeight,
    letterSpacing: "-0.01em",
    lineHeight: 1.22,
    margin: 0,
    textAlign: "center",
  };
}

const CaptionText: React.FC<{
  emphasisWords: string[];
  profile: string;
  text: string;
  variant: string;
}> = ({ emphasisWords, profile, text, variant }) => {
  const words = text.split(/(\s+)/);
  const emphasis = new Set(emphasisWords.map((word) => word.toLowerCase()));
  return (
    <p style={captionTextStyle(profile, variant)}>
      {words.map((word, index) =>
        emphasis.has(word.trim().toLowerCase()) ? (
          <span key={`${word}-${index}`} style={emphasisStyle(profile)}>
            {word}
          </span>
        ) : (
          <span key={`${word}-${index}`}>{word}</span>
        ),
      )}
    </p>
  );
};

function emphasisStyle(profile: string): React.CSSProperties {
  return {
    color: profile === "cinematic" ? "#67e8f9" : "#7dd3fc",
    fontWeight: 700,
  };
}

function focusMatteStyle(
  focusBox: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number; canvasWidth: number; canvasHeight: number; chromeOffset: number },
) {
  const width = viewport.width * viewport.canvasWidth;
  const height = viewport.height * viewport.canvasHeight;
  const usableHeight = height - viewport.chromeOffset;
  const left = Math.max(0, focusBox.x * width - 10);
  const top = Math.max(0, viewport.chromeOffset + focusBox.y * usableHeight - 10);
  const boxWidth = Math.min(width - left, focusBox.width * width + 20);
  const boxHeight = Math.min(height - top, focusBox.height * usableHeight + 20);
  return { left, top, boxWidth, boxHeight, width, height };
}

function focusMatteRects(
  focusBox: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number; canvasWidth: number; canvasHeight: number; chromeOffset: number },
  fastPreview: boolean,
): React.CSSProperties[] {
  const { left, top, boxWidth, boxHeight, width, height } = focusMatteStyle(focusBox, viewport);
  const overlay = `rgba(2, 6, 23, ${fastPreview ? 0.24 : 0.34})`;
  return [
    { background: overlay, height: top, left: 0, position: "absolute", top: 0, width, zIndex: 2 },
    { background: overlay, height: height - (top + boxHeight), left: 0, position: "absolute", top: top + boxHeight, width, zIndex: 2 },
    { background: overlay, height: boxHeight, left: 0, position: "absolute", top, width: left, zIndex: 2 },
    { background: overlay, height: boxHeight, left: left + boxWidth, position: "absolute", top, width: width - (left + boxWidth), zIndex: 2 },
  ];
}

function focusFrameStyle(
  focusBox: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number; canvasWidth: number; canvasHeight: number; chromeOffset: number },
  fastPreview: boolean,
): React.CSSProperties {
  const { left, top, boxWidth, boxHeight } = focusMatteStyle(focusBox, viewport);
  return {
    border: "1px solid rgba(125,211,252,0.18)",
    borderRadius: 24,
    boxShadow: fastPreview ? "0 0 0 1px rgba(34,211,238,0.08)" : "0 0 0 1px rgba(34,211,238,0.12)",
    height: boxHeight,
    left,
    position: "absolute",
    top,
    width: boxWidth,
    zIndex: 3,
  };
}

function highlightStyle(
  anchor: { left: string; top: string },
  viewport: {
    left: number;
    top: number;
    width: number;
    height: number;
    chromeOffset: number;
    canvasHeight: number;
  },
): React.CSSProperties {
  const left = Number.parseFloat(anchor.left) / 100;
  const top = Number.parseFloat(anchor.top) / 100;
  const chromeOffsetPercent = (viewport.chromeOffset / viewport.canvasHeight) * viewport.height;
  return {
    left: `${((viewport.left + left * viewport.width) * 100).toFixed(2)}%`,
    position: "absolute",
    top: `${((viewport.top + chromeOffsetPercent + top * (viewport.height - chromeOffsetPercent)) * 100).toFixed(2)}%`,
    transform: "translate(-10%, -10%)",
    zIndex: 7,
  };
}

function highlightRingStyle(
  fastPreview: boolean,
  intensity: number,
  focusBox: { x: number; y: number; width: number; height: number } | null,
  viewport: { width: number; height: number; canvasWidth: number; canvasHeight: number },
): React.CSSProperties {
  const canvasWidth = viewport.width * viewport.canvasWidth;
  const canvasHeight = viewport.height * viewport.canvasHeight;
  const width = focusBox ? Math.max(110, Math.round(focusBox.width * canvasWidth)) : 130;
  const height = focusBox ? Math.max(72, Math.round(focusBox.height * canvasHeight)) : 130;
  return {
    background: "rgba(14, 165, 233, 0.08)",
    border: "3px solid rgba(34, 211, 238, 0.95)",
    borderRadius: focusBox ? 26 : 9999,
    boxShadow: fastPreview
      ? `0 0 0 ${4 + intensity * 4}px rgba(34, 211, 238, ${0.04 + intensity * 0.04})`
      : `0 0 0 ${6 + intensity * 6}px rgba(34, 211, 238, ${0.05 + intensity * 0.06})`,
    height,
    opacity: 0.8 + intensity * 0.16,
    width,
  };
}

function highlightLabelStyle(fastPreview: boolean, intensity: number): React.CSSProperties {
  return {
    backgroundColor: "rgba(9, 14, 28, 0.88)",
    border: "1px solid rgba(125, 211, 252, 0.42)",
    borderRadius: 9999,
    color: "#f8fafc",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 15,
    fontWeight: 700,
    marginTop: 8,
    opacity: 0.84 + intensity * 0.16,
    padding: "8px 12px",
    boxShadow: fastPreview ? "0 8px 18px rgba(15,23,42,0.08)" : "0 12px 24px rgba(15,23,42,0.1)",
    whiteSpace: "nowrap",
  };
}

function sourceVideoVolume(payload: RenderPayload): number {
  const hasReadyVoiceover = Boolean(payload.voiceoverAudioPath) && payload.voiceover.status === "ready";
  if (payload.voiceover.mode === "voiceover" && hasReadyVoiceover) {
    return 0;
  }
  if (payload.voiceover.mode === "mixed" && hasReadyVoiceover) {
    return 0.25;
  }
  return 1;
}

function voiceoverVolume(payload: RenderPayload): number {
  return payload.voiceover.mode === "mixed" ? 0.92 : 1;
}
