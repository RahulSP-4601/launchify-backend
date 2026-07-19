import React from "react";

import { titleStyles } from "./render-helpers";
import { RenderPayload, RenderScene } from "./types";

export const IntroCard: React.FC<{ payload: RenderPayload; fastPreview: boolean }> = ({ payload, fastPreview }) => {
  return (
    <>
      <div style={heroBadgeStyle()}>AI Product Video</div>
      <h1 style={heroTitleStyle(fastPreview)}>{payload.editPlan.render_spec.title_card}</h1>
      <p style={heroBodyStyle(fastPreview)}>
        {payload.productName} walkthrough, cleaned up with guided focus, captions, and launch-ready framing.
      </p>
    </>
  );
};

export const OutroCard: React.FC<{ payload: RenderPayload; fastPreview: boolean }> = ({ payload, fastPreview }) => {
  return (
    <>
      <div style={heroBadgeStyle()}>Ready To Share</div>
      <h2 style={heroTitleStyle(fastPreview)}>{payload.editPlan.render_spec.cta}</h2>
      <p style={heroBodyStyle(fastPreview)}>
        {payload.productName} is ready to publish as a polished demo, walkthrough, or launch asset.
      </p>
    </>
  );
};

export const SceneMeta: React.FC<{ fastPreview: boolean; payload: RenderPayload; scene: RenderScene }> = ({
  fastPreview,
  payload,
  scene,
}) => {
  const accent = payload.templateConfig.theme === "bold" ? "#fb7185" : sceneAccent(scene.scene_role);
  return (
    <div style={sceneMetaStyle(fastPreview)}>
      <div style={{ ...sceneNumberChipStyle(), color: accent, borderColor: `${accent}55` }}>Scene {scene.scene_number}</div>
      <h3 style={sceneTitleStyle()}>{scene.purpose}</h3>
      <p style={sceneSubtitleStyle()}>{scene.on_screen_text || scene.visual_summary}</p>
    </div>
  );
};

export const CaptionPill: React.FC<{
  payload: RenderPayload;
  caption: RenderScene["captions"][number];
}> = ({ payload, caption }) => {
  return (
    <div style={captionStyle(payload.quality === "preview")}>
      <CaptionText
        emphasisWords={caption.emphasis_words}
        profile={payload.templateConfig.caption_profile}
        text={caption.text}
        variant={caption.variant}
      />
    </div>
  );
};

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

function sceneAccent(sceneRole: RenderScene["scene_role"]) {
  if (sceneRole === "result") {
    return "#f59e0b";
  }
  if (sceneRole === "explanation") {
    return "#94a3b8";
  }
  return "#67e8f9";
}

function captionStyle(fastPreview: boolean): React.CSSProperties {
  return {
    background: "rgba(9, 14, 28, 0.74)",
    border: "1px solid rgba(148,163,184,0.26)",
    borderRadius: 20,
    bottom: 26,
    boxShadow: fastPreview ? "0 10px 26px rgba(15, 23, 42, 0.16)" : "0 16px 40px rgba(15, 23, 42, 0.2)",
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
