export type RenderPayload = {
  projectId: string;
  projectName: string;
  productName: string;
  quality: string;
  dimensions: {
    width: number;
    height: number;
    fps: number;
  };
  introDurationSeconds: number;
  outroDurationSeconds: number;
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
  sourceVideoPath?: string;
};

export type RenderScene = {
  scene_number: number;
  title: string;
  purpose: string;
  start: number;
  end: number;
  confidence: number;
  camera_mode: "static" | "focus";
  decision_summary: string;
  visual_summary: string;
  spoken_line: string;
  on_screen_text: string;
  source_excerpt: string;
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
};

export type RenderZoom = {
  start: number;
  end: number;
  scale: number;
  focus_region: string;
  reason: string;
  confidence: number;
  focus_box: FocusBox | null;
};

export type RenderHighlight = {
  start: number;
  end: number;
  label: string;
  style: string;
  anchor_region: string;
  confidence: number;
  focus_box: FocusBox | null;
};
