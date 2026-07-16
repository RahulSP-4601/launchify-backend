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

  return (
    <AbsoluteFill style={shellStyle()}>
      <AudioTrack introFrames={introFrames} payload={payload} />
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
  const transition = transitionStyle(scene, frame, payload.dimensions.fps);
  return (
    <AbsoluteFill style={videoShellStyle(transition.opacity, transition.translateY)}>
      <VideoLayer payload={payload} scene={scene} zoom={zoom} transitionScale={transition.focusScale} />
      <GradientMask />
      <SceneHeader payload={payload} scene={scene} />
      {caption ? <CaptionPill payload={payload} caption={caption} /> : null}
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
  zoom: { origin: string; scale: number; translateX: number; translateY: number };
  transitionScale: number;
}> = ({ payload, scene, zoom, transitionScale }) => {
  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
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

const AudioTrack: React.FC<{ introFrames: number; payload: RenderPayload }> = ({ introFrames, payload }) => {
  if (!payload.voiceoverAudioPath || payload.voiceover.mode === "original") {
    return null;
  }
  return <Sequence from={introFrames}><Audio src={payload.voiceoverAudioPath} volume={voiceoverVolume(payload)} /></Sequence>;
};

const GradientMask = () => <AbsoluteFill style={gradientMaskStyle()} />;

const SceneHeader: React.FC<{ payload: RenderPayload; scene: RenderScene }> = ({ payload, scene }) => {
  const accent = payload.templateConfig.theme === "bold" ? "#f97316" : "#7dd3fc";
  return (
    <div style={sceneHeaderStyle()}>
      <p style={{ ...titleStyles.eyebrow, color: accent }}>Scene {scene.scene_number}</p>
      <h3 style={{ ...titleStyles.body, color: "#f8fafc", fontSize: 34 }}>{scene.purpose}</h3>
      <p style={{ ...titleStyles.body, fontSize: 22 }}>{scene.on_screen_text}</p>
    </div>
  );
};

const CaptionPill: React.FC<{
  payload: RenderPayload;
  caption: RenderScene["captions"][number];
}> = ({ payload, caption }) => {
  return (
    <div style={captionStyle(payload.templateConfig.theme, caption.variant)}>
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
    background: "radial-gradient(circle at top left, rgba(56, 189, 248, 0.16), transparent 28%), linear-gradient(135deg, #020617 0%, #0f172a 38%, #172554 100%)",
  };
}

function cardShell(opacity: number): React.CSSProperties {
  return {
    alignItems: "flex-start",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    opacity,
    padding: "112px 120px",
  };
}

function videoShellStyle(opacity: number, translateY: number): React.CSSProperties {
  return {
    backgroundColor: "#020617",
    opacity,
    transform: `translateY(${translateY}px)`,
  };
}

function gradientMaskStyle(): React.CSSProperties {
  return {
    background: "linear-gradient(180deg, rgba(2,6,23,0.22) 0%, rgba(2,6,23,0.04) 28%, rgba(2,6,23,0.62) 100%)",
  };
}

function sceneHeaderStyle(): React.CSSProperties {
  return {
    background: "linear-gradient(135deg, rgba(15,23,42,0.72), rgba(15,23,42,0.28))",
    border: "1px solid rgba(191, 219, 254, 0.14)",
    borderRadius: 28,
    boxShadow: "0 18px 50px rgba(2, 6, 23, 0.24)",
    left: 40,
    maxWidth: 760,
    padding: "20px 24px 18px",
    position: "absolute",
    top: 32,
  };
}

function captionStyle(theme: string, variant: string): React.CSSProperties {
  const backgroundColor = theme === "bold" ? "rgba(127, 29, 29, 0.84)" : "rgba(15, 23, 42, 0.7)";
  const maxWidth = variant === "hero" ? "90%" : "84%";
  return {
    backdropFilter: "blur(10px)",
    backgroundColor,
    border: "1px solid rgba(191, 219, 254, 0.18)",
    borderRadius: 24,
    bottom: 34,
    boxShadow: "0 14px 34px rgba(2, 6, 23, 0.28)",
    left: 34,
    maxWidth,
    padding: "18px 22px",
    position: "absolute",
  };
}

function captionTextStyle(profile: string, variant: string): React.CSSProperties {
  const fontSize = profile === "cinematic" || variant === "hero" ? 34 : profile === "minimal" ? 25 : 29;
  const fontFamily = profile === "cinematic" ? "\"Iowan Old Style\", Georgia, serif" : "\"Avenir Next\", \"Segoe UI\", sans-serif";
  const fontWeight = variant === "hero" ? 700 : 500;
  return {
    color: "#f8fafc",
    fontFamily,
    fontSize,
    fontWeight,
    letterSpacing: "0.01em",
    lineHeight: 1.3,
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
    color: profile === "cinematic" ? "#fcd34d" : "#67e8f9",
    fontWeight: 700,
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
    background: "rgba(34, 211, 238, 0.05)",
    border: "3px solid rgba(125, 211, 252, 0.92)",
    borderRadius: focusBox ? 28 : 9999,
    boxShadow: `0 0 0 ${10 + intensity * 10}px rgba(56, 189, 248, ${0.06 + intensity * 0.1}), 0 10px 24px rgba(2, 6, 23, 0.18)`,
    height,
    opacity: 0.76 + intensity * 0.2,
    width,
  };
}

function highlightLabelStyle(intensity: number): React.CSSProperties {
  return {
    backgroundColor: "rgba(15, 23, 42, 0.88)",
    border: "1px solid rgba(191, 219, 254, 0.28)",
    borderRadius: 9999,
    color: "#f8fafc",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif",
    fontSize: 20,
    fontWeight: 600,
    marginTop: 12,
    opacity: 0.8 + intensity * 0.2,
    padding: "10px 16px",
  };
}

function sourceVideoVolume(payload: RenderPayload): number {
  if (payload.voiceover.mode === "voiceover") {
    return 0;
  }
  if (payload.voiceover.mode === "mixed") {
    return 0.35;
  }
  return 1;
}

function voiceoverVolume(payload: RenderPayload): number {
  return payload.voiceover.mode === "mixed" ? 0.82 : 1;
}
