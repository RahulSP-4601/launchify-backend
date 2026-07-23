export type RenderPayload = {
  projectId: string;
  projectName: string;
  productName: string;
  quality: string;
  voiceoverAudioPath?: string;
  dimensions: {
    width: number;
    height: number;
    fps: number;
  };
  introDurationSeconds: number;
  outroDurationSeconds: number;
  templateConfig: {
    theme: "clean" | "spotlight" | "bold";
    caption_profile: "product" | "minimal" | "cinematic";
    motion_profile: "balanced" | "dynamic" | "calm";
  };
  voiceover: {
    provider: string;
    model: string;
    mode: "original" | "voiceover" | "mixed";
    status: "disabled" | "script_only" | "ready";
    script: string;
    cues: Array<{
      scene_number: number;
      start: number;
      end: number;
      text: string;
      duration_seconds: number;
    }>;
    clips: Array<{
      scene_number: number;
      start: number;
      end: number;
      text: string;
      duration_seconds: number;
      audio_storage_path: string;
    }>;
    audio_storage_path: string;
    duration_seconds: number;
  };
  editPlan: {
    overview: string;
    total_duration_seconds: number;
    scenes: RenderScene[];
    render_spec: {
      title_card: string;
      title_options: string[];
      cta: string;
      total_duration_seconds: number;
    };
  };
  timeline?: {
    total_duration_seconds: number;
    scenes: TimelineScene[];
    tracks: TimelineTrack[];
  };
  sourceVideoPath?: string;
};

export type TimelineTrack = {
  id: string;
  kind: "video" | "audio" | "caption" | "overlay";
  name: string;
  locked: boolean;
  muted: boolean;
  clips: TimelineClip[];
};

export type TimelineClip = {
  id: string;
  track_id: string;
  kind: "source_video" | "inserted_card" | "caption" | "voiceover";
  title: string;
  scene_id: string | null;
  timeline_start: number;
  timeline_end: number;
  source_start: number | null;
  source_end: number | null;
  text: string;
  locked: boolean;
  muted: boolean;
};

export type TimelineScene = {
  scene_number: number;
  title: string;
  purpose: string;
  editor_start: number;
  editor_end: number;
  source_start: number;
  source_end: number;
  render_duration_seconds: number | null;
  camera_mode: "static" | "focus";
  scene_role: "action" | "result" | "explanation";
  spoken_line: string;
  on_screen_text: string;
  source_excerpt: string;
  source: "edit_plan" | "launch_script" | "transcript" | "fallback" | "inserted";
  is_inserted: boolean;
  transition_style: "cut" | "fade" | "slide-up" | "focus-push";
  transition_duration_seconds: number;
  captions: RenderCaption[];
  zooms: RenderZoom[];
  highlights: RenderHighlight[];
};

export type RenderScene = {
  scene_number: number;
  title: string;
  purpose: string;
  start: number;
  end: number;
  render_duration_seconds: number | null;
  confidence: number;
  camera_mode: "static" | "focus";
  scene_role: "action" | "result" | "explanation";
  decision_summary: string;
  visual_summary: string;
  spoken_line: string;
  on_screen_text: string;
  source_excerpt: string;
  action_timestamp: number | null;
  result_anchor_timestamp?: number | null;
  readable_hold_seconds?: number;
  establish_end_timestamp?: number | null;
  focus_start_timestamp?: number | null;
  focus_end_timestamp?: number | null;
  settle_end_timestamp?: number | null;
  layout_mode?: string;
  show_captions?: boolean;
  transition_style: "cut" | "fade" | "slide-up" | "focus-push";
  transition_duration_seconds: number;
  captions: RenderCaption[];
  zooms: RenderZoom[];
  highlights: RenderHighlight[];
};

export type FocusBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type RenderCaption = {
  start: number;
  end: number;
  text: string;
  emphasis_words: string[];
  variant: string;
};

export type RenderZoom = {
  start: number;
  end: number;
  scale: number;
  focus_region: string;
  reason: string;
  confidence: number;
  focus_box: FocusBox | null;
  easing: string;
  x_offset: number;
  y_offset: number;
  smoothing: number;
  hold_ratio: number;
};

export type RenderHighlight = {
  start: number;
  end: number;
  label: string;
  style: string;
  anchor_region: string;
  confidence: number;
  focus_box: FocusBox | null;
  placement_preference: string;
  ui_label: string;
};
