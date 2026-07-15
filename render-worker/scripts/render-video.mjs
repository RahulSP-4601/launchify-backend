import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { bundle } from "@remotion/bundler";
import { getCompositions, renderMedia } from "@remotion/renderer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const mode = process.argv[2] ?? "preview";
const options = parseArgs(process.argv.slice(3));

await renderVideo(mode, options);

async function renderVideo(mode, options) {
  assertOption(options.input, "--input is required");
  assertOption(options.output, "--output is required");
  assertOption(options.source, "--source is required");
  const payload = await readPayload(options.input, options.source);
  const entryPoint = path.join(__dirname, "../src/index.ts");
  const bundled = await bundle({ entryPoint, onProgress: () => undefined });
  const compositions = await getCompositions(bundled, { inputProps: payload });
  const composition = requireComposition(compositions);
  await renderMedia({
    codec: "h264",
    composition,
    chromiumOptions: { disableWebSecurity: true },
    inputProps: payload,
    outputLocation: options.output,
    pixelFormat: "yuv420p",
    serveUrl: bundled,
  });
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 2) {
    parsed[argv[index].replace(/^--/, "")] = argv[index + 1] ?? "";
  }
  return parsed;
}

function assertOption(value, message) {
  if (!value) {
    throw new Error(message);
  }
}

async function readPayload(inputPath, sourcePath) {
  const payload = JSON.parse(await readFile(inputPath, "utf-8"));
  return {
    ...payload,
    quality: payload.quality ?? mode,
    sourceVideoPath: withFileProtocol(sourcePath),
  };
}

function requireComposition(compositions) {
  const match = compositions.find((composition) => composition.id === "LaunchifyRender");
  if (!match) {
    throw new Error("LaunchifyRender composition was not found.");
  }
  return match;
}

function withFileProtocol(sourcePath) {
  if (sourcePath.startsWith("http://") || sourcePath.startsWith("https://")) {
    return sourcePath;
  }
  return `file://${sourcePath}`;
}
