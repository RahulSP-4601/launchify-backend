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
  activeFocusBox,
  motionOpacity,
  sceneDurationFrames,
  spotlightStyle,
  timelineSceneDuration,
  transitionStyle,
  totalFrames,
  zoomTransform,
} from "./render-helpers";
import { FocusMatte } from "./scene-overlays";
import { IntroCard, OutroCard } from "./scene-text";
import { RenderPayload, RenderScene, TimelineClip, TimelineScene } from "./types";

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
  const scenes = payload.timeline?.scenes ?? legacyTimeline(payload.editPlan.scenes);
  return (
    <>
      {scenes.map((scene) => {
        const durationInFrames = sceneDurationFrames(scene, payload.dimensions.fps);
        const sequence = (
          <Sequence key={`${scene.scene_number}-${scene.title}`} from={sceneOffset} durationInFrames={durationInFrames}>
            <SceneComposition fastPreview={fastPreview} payload={payload} scene={scene} />
          </Sequence>
        );
        sceneOffset += durationInFrames;
        return sequence;
      })}
    </>
  );
};

const SceneComposition: React.FC<{ fastPreview: boolean; payload: RenderPayload; scene: TimelineScene }> = ({
  fastPreview,
  payload,
  scene,
}) => {
  const frame = useCurrentFrame();
  const viewport = viewportMetrics(payload.dimensions);
  const sourceDuration = Math.max(scene.source_end - scene.source_start, 0);
  const sourceSeconds = Math.min(frame / payload.dimensions.fps, sourceDuration);
  const localSeconds = Math.min(frame / payload.dimensions.fps, timelineSceneDuration(scene));
  const focusBox = activeFocusBox(scene, localSeconds);
  const zoom = zoomTransform(scene.zooms, localSeconds);
  const transition = transitionStyle(scene, frame, payload.dimensions.fps);
  const overlayClips = activeOverlayClips(payload, scene, localSeconds);
  const highlight = spotlightStyle(scene.highlights, localSeconds);

  return (
    <AbsoluteFill style={sceneCanvasStyle(transition.opacity, transition.translateY)}>
      <SceneGlow fastPreview={fastPreview} />
      <ViewportFrame fastPreview={fastPreview}>
        {scene.is_inserted
          ? <InsertedSceneLayer scene={scene} />
          : <VideoLayer payload={payload} scene={scene} viewport={viewport} zoom={zoom} transitionScale={transition.focusScale} />}
        {overlayClips.map((clip) => <OverlayClipLayer key={clip.id} clip={clip} highlightVisible={Boolean(highlight)} />)}
        <FocusMatte fastPreview={fastPreview} focusBox={focusBox} sceneRole={scene.scene_role} viewport={viewport} />
        <GradientMask fastPreview={fastPreview} />
        <BrowserChrome payload={payload} viewport={viewport} />
      </ViewportFrame>
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
  scene: TimelineScene;
  viewport: {
    chromeOffset: number;
  };
  zoom: { origin: string; scale: number; translateX: number; translateY: number };
  transitionScale: number;
}> = ({ payload, scene, viewport, zoom, transitionScale }) => {
  const videoSource = scene.asset_path ?? payload.sourceVideoPath ?? "";
  const mediaWindow = videoWindow(scene, payload.dimensions.fps);
  return (
    <AbsoluteFill style={videoSurfaceStyle(viewport.chromeOffset)}>
      <OffthreadVideo
        {...mediaWindow}
        src={videoSource}
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

const InsertedSceneLayer: React.FC<{ scene: TimelineScene }> = ({ scene }) => (
  <AbsoluteFill
    style={{
      alignItems: "center",
      background: "radial-gradient(circle at top, rgba(114, 78, 224, 0.2), transparent 36%), linear-gradient(180deg, #091018, #09090d)",
      display: "flex",
      justifyContent: "center",
      padding: 48,
    }}
  >
    <div
      style={{
        background: "linear-gradient(180deg, rgba(31,31,39,0.96), rgba(15,15,20,0.98))",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 28,
        boxShadow: "0 28px 56px rgba(0,0,0,0.34)",
        color: "#f5f5ff",
        maxWidth: 760,
        padding: "40px 44px",
        width: "100%",
      }}
    >
      <div style={{ color: "#9e95ff", fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif", fontSize: 16, letterSpacing: "0.28em", textTransform: "uppercase" }}>Inserted Screen</div>
      <div style={{ fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif", fontSize: 52, fontWeight: 700, lineHeight: 1.02, marginTop: 18 }}>{scene.title}</div>
      <div style={{ color: "#c7c7d8", fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif", fontSize: 24, lineHeight: 1.5, marginTop: 24 }}>{scene.on_screen_text || scene.spoken_line}</div>
    </div>
  </AbsoluteFill>
);

const OverlayClipLayer: React.FC<{ clip: TimelineClip; highlightVisible: boolean }> = ({ clip, highlightVisible }) => (
  <div
    style={{
      alignItems: "flex-end",
      display: "flex",
      height: "100%",
      justifyContent: "flex-start",
      left: 0,
      padding: "0 56px 44px",
      position: "absolute",
      top: 0,
      width: "100%",
      zIndex: highlightVisible ? 2 : 3,
    }}
  >
    <div
      style={{
        backdropFilter: "blur(18px)",
        background: "linear-gradient(180deg, rgba(18,21,28,0.92), rgba(10,11,16,0.96))",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 22,
        boxShadow: "0 20px 56px rgba(0,0,0,0.32)",
        color: "#f8fafc",
        maxWidth: 520,
        padding: "24px 28px",
      }}
    >
      <div style={{ color: "#c084fc", fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif", fontSize: 14, letterSpacing: "0.24em", textTransform: "uppercase" }}>Overlay Callout</div>
      <div style={{ fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif", fontSize: 34, fontWeight: 700, lineHeight: 1.05, marginTop: 12 }}>{clip.title}</div>
      <div style={{ color: "#d4d4dc", fontFamily: "\"Avenir Next\", \"Segoe UI\", sans-serif", fontSize: 20, lineHeight: 1.45, marginTop: 14 }}>{clip.text || clip.title}</div>
    </div>
  </div>
);

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
  const clipTracks = timelineAudioClips(payload);
  if (clipTracks?.length) {
    return (
      <>
        {clipTracks.map((clip) => (
          <Sequence
            key={`${clip.scene_number}-${clip.start}-${clip.audio_storage_path}`}
            from={introFrames + Math.round(clip.start * payload.dimensions.fps)}
          >
            <Audio loop={clip.loop ?? false} src={clip.audio_storage_path} volume={clipVolumeEnvelope(clip, payload.dimensions.fps, voiceoverVolume(payload))} />
          </Sequence>
        ))}
      </>
    );
  }
  if (payload.voiceover.mode === "original" || payload.voiceover.status !== "ready") {
    return null;
  }
  const voiceoverClips = payload.voiceover.clips.filter((clip) => Boolean(clip.audio_storage_path));
  if (voiceoverClips.length > 0) {
    return (
      <>
        {voiceoverClips.map((clip) => (
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

function clipVolumeEnvelope(
  clip: {
    duration_seconds?: number;
    fade_in_seconds?: number;
    fade_out_seconds?: number;
    volume_percent?: number;
  },
  fps: number,
  fallbackVolume: number,
) {
  const baseVolume = Math.max((clip.volume_percent ?? 100) / 100, 0) * fallbackVolume;
  const clipDurationFrames = Math.max(1, Math.round((clip.duration_seconds ?? 0) * fps));
  const fadeInFrames = Math.max(0, Math.round((clip.fade_in_seconds ?? 0) * fps));
  const fadeOutFrames = Math.max(0, Math.round((clip.fade_out_seconds ?? 0) * fps));
  return (frame: number) => {
    const fadeIn = fadeInFrames > 0 ? Math.min(frame / fadeInFrames, 1) : 1;
    const remainingFrames = clipDurationFrames - frame;
    const fadeOut = fadeOutFrames > 0 ? Math.min(Math.max(remainingFrames, 0) / fadeOutFrames, 1) : 1;
    return baseVolume * Math.min(fadeIn, fadeOut);
  };
}

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

function timelineAudioClips(payload: RenderPayload) {
  const audioTrackClips = (payload.timeline?.tracks ?? [])
    .filter((track) => track.kind === "audio" && !track.muted)
    .flatMap((track) => track.clips)
    .filter((clip) => (clip.kind === "voiceover" || clip.kind === "media_audio") && !clip.muted);
  if (!audioTrackClips.length) {
    return null;
  }
  return audioTrackClips
    .map((clip) => timelineAudioClip(payload, clip))
    .filter(Boolean);
}

function timelineAudioClip(payload: RenderPayload, clip: TimelineClip) {
  if (clip.kind === "media_audio" && clip.asset_path) {
    return {
      audio_storage_path: clip.asset_path,
      duration_seconds: clip.timeline_end - clip.timeline_start,
      end: clip.timeline_end,
      fade_in_seconds: clip.fade_in_seconds ?? 0,
      fade_out_seconds: clip.fade_out_seconds ?? 0,
      loop: clip.loop ?? false,
      scene_number: sceneNumberForClip(clip.scene_id) ?? 0,
      start: clip.timeline_start,
      text: clip.text,
      volume_percent: clip.volume_percent ?? 100,
    };
  }
  if (clip.kind !== "voiceover") {
    return null;
  }
  const sceneNumber = sceneNumberForClip(clip.scene_id);
  const voiceover = payload.voiceover.clips.find((item) => item.scene_number === sceneNumber && Boolean(item.audio_storage_path));
  if (!voiceover) {
    return null;
  }
  return {
    ...voiceover,
    end: clip.timeline_end,
    fade_in_seconds: clip.fade_in_seconds ?? 0,
    fade_out_seconds: clip.fade_out_seconds ?? 0,
    loop: clip.loop ?? false,
    start: clip.timeline_start,
    volume_percent: clip.volume_percent ?? 100,
  };
}

function legacyTimeline(scenes: RenderScene[]): TimelineScene[] {
  return scenes.map((scene) => ({
    camera_mode: scene.camera_mode,
    captions: scene.captions.map((caption) => ({ ...caption, end: caption.end - scene.start, start: caption.start - scene.start })),
    editor_end: scene.end,
    editor_start: scene.start,
    highlights: scene.highlights.map((highlight) => ({ ...highlight, end: highlight.end - scene.start, start: highlight.start - scene.start })),
    is_inserted: false,
    on_screen_text: scene.on_screen_text,
    purpose: scene.purpose,
    render_duration_seconds: scene.render_duration_seconds,
    scene_number: scene.scene_number,
    scene_role: scene.scene_role,
    source: "edit_plan",
    source_end: scene.end,
    source_excerpt: scene.source_excerpt,
    source_start: scene.start,
    spoken_line: scene.spoken_line,
    title: scene.title,
    transition_duration_seconds: scene.transition_duration_seconds,
    transition_style: scene.transition_style,
    zooms: scene.zooms.map((zoom) => ({ ...zoom, end: zoom.end - scene.start, start: zoom.start - scene.start })),
  }));
}

function activeOverlayClips(payload: RenderPayload, scene: TimelineScene, localSeconds: number) {
  const editorTime = scene.editor_start + localSeconds;
  return (payload.timeline?.tracks ?? [])
    .filter((track) => track.kind === "overlay" && !track.muted)
    .flatMap((track) => track.clips)
    .filter((clip) => {
      if (clip.muted || (clip.kind !== "inserted_card" && clip.kind !== "effect_overlay")) {
        return false;
      }
      return clip.timeline_start <= editorTime && clip.timeline_end >= editorTime;
    });
}

function videoWindow(scene: TimelineScene, fps: number) {
  if (scene.clip_kind === "media_video" && scene.asset_path) {
    return {};
  }
  return {
    endAt: Math.round(scene.source_end * fps),
    startFrom: Math.round(scene.source_start * fps),
  };
}

function sceneNumberForClip(sceneId: string | null) {
  if (!sceneId || !sceneId.startsWith("scene-")) {
    return null;
  }
  const rawValue = sceneId.slice("scene-".length).split("-", 1)[0];
  const number = Number.parseInt(rawValue, 10);
  return Number.isNaN(number) ? null : number;
}

function shellStyle(): React.CSSProperties {
  return {
    background:
      "radial-gradient(circle at 10% 20%, rgba(103, 232, 249, 0.18), transparent 24%), radial-gradient(circle at 88% 16%, rgba(244, 114, 182, 0.18), transparent 28%), linear-gradient(135deg, #f8fafc 0%, #e2e8f0 38%, #dbeafe 100%)",
  };
}

function viewportMetrics(dimensions: { width: number; height: number }) {
  const frameHeight = dimensions.height * 0.84;
  const chromeOffset = Math.round(frameHeight * (42 / 720));
  return {
    left: 0.035,
    top: 0.08,
    width: 0.93,
    height: 0.84,
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
    background: "rgba(255,255,255,0.62)",
    border: "1px solid rgba(255,255,255,0.94)",
    borderRadius: 34,
    boxShadow: fastPreview
      ? "0 18px 34px rgba(15, 23, 42, 0.12)"
      : "0 24px 48px rgba(15, 23, 42, 0.14)",
    height: "84%",
    left: "3.5%",
    overflow: "hidden",
    position: "absolute",
    top: "8%",
    width: "93%",
  };
}

function viewportInnerStyle(): React.CSSProperties {
  return {
    background: "#071322",
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
      ? "linear-gradient(180deg, rgba(2,6,23,0.03) 0%, rgba(2,6,23,0.01) 24%, rgba(2,6,23,0.12) 100%)"
      : "linear-gradient(180deg, rgba(2,6,23,0.04) 0%, rgba(2,6,23,0.01) 24%, rgba(2,6,23,0.14) 100%)",
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
