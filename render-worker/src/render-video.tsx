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
  totalFrames,
  zoomTransform,
} from "./render-helpers";
import { FocusMatte, HighlightBadge } from "./scene-overlays";
import { CaptionPill, IntroCard, OutroCard, SceneMeta } from "./scene-text";
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
        <HeroCard fastPreview={fastPreview}>
          <IntroCard payload={payload} fastPreview={fastPreview} />
        </HeroCard>
      </Sequence>
      <SceneTrack fastPreview={fastPreview} introFrames={introFrames} payload={payload} />
      <Sequence from={totalDuration - outroFrames} durationInFrames={outroFrames}>
        <HeroCard fastPreview={fastPreview}>
          <OutroCard payload={payload} fastPreview={fastPreview} />
        </HeroCard>
      </Sequence>
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
  const viewport = viewportMetrics(payload.dimensions);
  const sourceDuration = Math.max(scene.end - scene.start, 0);
  const sourceSeconds = Math.min(frame / payload.dimensions.fps, sourceDuration);
  const localSeconds = scene.start + sourceSeconds;
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
          viewport={viewport}
          zoom={zoom}
          transitionScale={transition.focusScale}
        />
        <FocusMatte fastPreview={fastPreview} focusBox={focusBox} sceneRole={scene.scene_role} viewport={viewport} />
        <GradientMask fastPreview={fastPreview} />
        <BrowserChrome payload={payload} viewport={viewport} />
      </ViewportFrame>
      <SceneMeta payload={payload} scene={scene} fastPreview={fastPreview} />
      {caption ? <CaptionPill payload={payload} caption={caption} /> : null}
      {spotlight ? (
        <HighlightBadge
          anchor={spotlight.anchor}
          fastPreview={fastPreview}
          focusBox={spotlight.focusBox}
          intensity={spotlight.intensity}
          label={spotlight.label}
          pulse={spotlight.pulse}
          style={spotlight.style}
          viewport={viewport}
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
  if (payload.voiceover.mode === "original" || payload.voiceover.status !== "ready") {
    return null;
  }
  const clipTracks = payload.voiceover.clips.filter((clip) => Boolean(clip.audio_storage_path));
  if (clipTracks.length > 0) {
    return (
      <>
        {clipTracks.map((clip) => (
          <Sequence
            key={`${clip.scene_number}-${clip.start}-${clip.audio_storage_path}`}
            from={introFrames + Math.round(clip.start * payload.dimensions.fps)}
          >
            <Audio src={clip.audio_storage_path} volume={voiceoverVolume(payload)} />
          </Sequence>
        ))}
      </>
    );
  }
  if (!payload.voiceoverAudioPath) {
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

const HeroCard: React.FC<{ children: React.ReactNode; fastPreview: boolean }> = ({ children, fastPreview }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return <AbsoluteFill style={heroShell(motionOpacity(frame, durationInFrames), fastPreview)}>{children}</AbsoluteFill>;
};

const SceneGlow: React.FC<{ fastPreview: boolean }> = ({ fastPreview }) => (
  <AbsoluteFill style={sceneGlowStyle(fastPreview)} />
);

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

function sourceVideoVolume(payload: RenderPayload): number {
  const hasReadyVoiceover =
    payload.voiceover.status === "ready" &&
    (Boolean(payload.voiceoverAudioPath) || payload.voiceover.clips.some((clip) => Boolean(clip.audio_storage_path)));
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
