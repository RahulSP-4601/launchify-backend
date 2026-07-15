import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import {
  activeCaption,
  motionOpacity,
  sceneDurationFrames,
  spotlightStyle,
  titleStyles,
  totalFrames,
  zoomTransform,
} from "./render-helpers";
import { RenderPayload, RenderScene } from "./types";

export const LaunchifyRender: React.FC<RenderPayload> = (payload) => {
  const totalDuration = totalFrames(payload);
  const introFrames = Math.round(payload.introDurationSeconds * payload.dimensions.fps);
  const outroFrames = Math.round(payload.outroDurationSeconds * payload.dimensions.fps);

  return (
    <AbsoluteFill style={shellStyle()}>
      <Sequence durationInFrames={introFrames}>
        <IntroCard payload={payload} />
      </Sequence>
      <SceneTrack introFrames={introFrames} payload={payload} />
      <Sequence from={totalDuration - outroFrames} durationInFrames={outroFrames}>
        <OutroCard payload={payload} />
      </Sequence>
    </AbsoluteFill>
  );
};

const IntroCard: React.FC<{ payload: RenderPayload }> = ({ payload }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={cardShell(motionOpacity(frame, durationInFrames))}>
      <p style={titleStyles.eyebrow}>Launchify</p>
      <h1 style={titleStyles.headline}>{payload.editPlan.render_spec.title_card}</h1>
      <p style={titleStyles.body}>{payload.editPlan.overview}</p>
    </AbsoluteFill>
  );
};

const OutroCard: React.FC<{ payload: RenderPayload }> = ({ payload }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={cardShell(motionOpacity(frame, durationInFrames))}>
      <p style={titleStyles.eyebrow}>Call To Action</p>
      <h2 style={titleStyles.headline}>{payload.editPlan.render_spec.cta}</h2>
      <p style={titleStyles.body}>{payload.productName} is ready to publish with polished captions and motion.</p>
    </AbsoluteFill>
  );
};

const SceneTrack: React.FC<{ introFrames: number; payload: RenderPayload }> = ({ introFrames, payload }) => {
  let sceneOffset = introFrames;
  return (
    <>
      {payload.editPlan.scenes.map((scene) => {
        const durationInFrames = sceneDurationFrames(scene, payload.dimensions.fps);
        const sequence = (
          <Sequence key={scene.scene_number} from={sceneOffset} durationInFrames={durationInFrames}>
            <SceneComposition payload={payload} scene={scene} />
          </Sequence>
        );
        sceneOffset += durationInFrames;
        return sequence;
      })}
    </>
  );
};

const SceneComposition: React.FC<{ payload: RenderPayload; scene: RenderScene }> = ({ payload, scene }) => {
  const frame = useCurrentFrame();
  const localSeconds = scene.start + frame / payload.dimensions.fps;
  const caption = activeCaption(scene, localSeconds);
  const zoom = zoomTransform(scene.zooms, localSeconds);
  const spotlight = spotlightStyle(scene.highlights, localSeconds);
  return (
    <AbsoluteFill style={videoShellStyle()}>
      <VideoLayer payload={payload} scene={scene} zoom={zoom} />
      <GradientMask />
      <SceneHeader scene={scene} />
      {caption ? <CaptionPill text={caption.text} /> : null}
      {spotlight ? (
        <HighlightBadge
          label={spotlight.label}
          anchor={spotlight.anchor}
          focusBox={spotlight.focusBox}
          intensity={spotlight.intensity}
          dimensions={payload.dimensions}
        />
      ) : null}
    </AbsoluteFill>
  );
};

const VideoLayer: React.FC<{
  payload: RenderPayload;
  scene: RenderScene;
  zoom: { origin: string; scale: number };
}> = ({ payload, scene, zoom }) => {
  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      <OffthreadVideo
        muted
        src={payload.sourceVideoPath ?? ""}
        startFrom={Math.round(scene.start * payload.dimensions.fps)}
        endAt={Math.round(scene.end * payload.dimensions.fps)}
        style={{
          height: "100%",
          objectFit: "cover",
          transform: `scale(${zoom.scale})`,
          transformOrigin: zoom.origin,
          width: "100%",
        }}
      />
    </AbsoluteFill>
  );
};

const GradientMask = () => <AbsoluteFill style={gradientMaskStyle()} />;

const SceneHeader: React.FC<{ scene: RenderScene }> = ({ scene }) => {
  return (
    <div style={sceneHeaderStyle()}>
      <p style={titleStyles.eyebrow}>Scene {scene.scene_number}</p>
      <h3 style={{ ...titleStyles.body, color: "#f8fafc", fontSize: 34 }}>{scene.purpose}</h3>
      <p style={{ ...titleStyles.body, fontSize: 22 }}>{scene.on_screen_text}</p>
    </div>
  );
};

const CaptionPill: React.FC<{ text: string }> = ({ text }) => {
  return (
    <div style={captionStyle()}>
      <p style={{ color: "#f8fafc", fontFamily: "Arial, sans-serif", fontSize: 30, lineHeight: 1.35 }}>{text}</p>
    </div>
  );
};

const HighlightBadge: React.FC<{
  label: string;
  anchor: { left: string; top: string };
  focusBox: { x: number; y: number; width: number; height: number } | null;
  intensity: number;
  dimensions: { width: number; height: number };
}> = ({ label, anchor, focusBox, intensity, dimensions }) => {
  return (
    <div style={highlightStyle(anchor)}>
      <div style={highlightRingStyle(intensity, focusBox, dimensions)} />
      <div style={highlightLabelStyle(intensity)}>{label}</div>
    </div>
  );
};

function shellStyle(): React.CSSProperties {
  return {
    background: "linear-gradient(135deg, #020617 0%, #0f172a 45%, #172554 100%)",
  };
}

function cardShell(opacity: number): React.CSSProperties {
  return {
    alignItems: "flex-start",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    opacity,
    padding: "120px",
  };
}

function videoShellStyle(): React.CSSProperties {
  return {
    backgroundColor: "#020617",
  };
}

function gradientMaskStyle(): React.CSSProperties {
  return {
    background: "linear-gradient(180deg, rgba(2,6,23,0.18) 0%, rgba(2,6,23,0.04) 35%, rgba(2,6,23,0.55) 100%)",
  };
}

function sceneHeaderStyle(): React.CSSProperties {
  return {
    left: 48,
    maxWidth: 720,
    position: "absolute",
    top: 40,
  };
}

function captionStyle(): React.CSSProperties {
  return {
    backdropFilter: "blur(10px)",
    backgroundColor: "rgba(15, 23, 42, 0.78)",
    border: "1px solid rgba(148, 163, 184, 0.18)",
    borderRadius: 28,
    bottom: 42,
    left: 42,
    maxWidth: "84%",
    padding: "22px 28px",
    position: "absolute",
  };
}

function highlightStyle(anchor: { left: string; top: string }): React.CSSProperties {
  return {
    left: anchor.left,
    position: "absolute",
    top: anchor.top,
  };
}

function highlightRingStyle(
  intensity: number,
  focusBox: { x: number; y: number; width: number; height: number } | null,
  dimensions: { width: number; height: number },
): React.CSSProperties {
  const width = focusBox ? Math.max(90, Math.round(focusBox.width * dimensions.width)) : 120;
  const height = focusBox ? Math.max(60, Math.round(focusBox.height * dimensions.height)) : 120;
  return {
    border: "4px solid rgba(34, 211, 238, 0.95)",
    borderRadius: focusBox ? 28 : 9999,
    boxShadow: `0 0 0 ${12 + intensity * 12}px rgba(34, 211, 238, ${0.08 + intensity * 0.12})`,
    height,
    opacity: 0.72 + intensity * 0.28,
    width,
  };
}

function highlightLabelStyle(intensity: number): React.CSSProperties {
  return {
    backgroundColor: "rgba(15, 23, 42, 0.86)",
    border: "1px solid rgba(125, 211, 252, 0.32)",
    borderRadius: 9999,
    color: "#f8fafc",
    fontFamily: "Arial, sans-serif",
    fontSize: 22,
    marginTop: 16,
    opacity: 0.8 + intensity * 0.2,
    padding: "12px 18px",
  };
}
