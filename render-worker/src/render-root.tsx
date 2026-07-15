import { Composition } from "remotion";

import { totalFrames } from "./render-helpers";
import { LaunchifyRender } from "./render-video";
import { RenderPayload } from "./types";

export const RenderRoot = () => {
  return (
    <Composition
      id="LaunchifyRender"
      component={LaunchifyRender}
      durationInFrames={300}
      fps={30}
      width={1280}
      height={720}
      calculateMetadata={({ props }) => ({
        durationInFrames: totalFrames(props as RenderPayload),
        fps: (props as RenderPayload).dimensions.fps,
        height: (props as RenderPayload).dimensions.height,
        width: (props as RenderPayload).dimensions.width,
      })}
      defaultProps={{} as RenderPayload}
    />
  );
};
