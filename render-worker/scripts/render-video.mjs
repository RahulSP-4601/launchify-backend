import { mkdir, mkdtemp, readFile, rename, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import os from "node:os";
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
  const bundled = await getBundledServeUrl(entryPoint);
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

async function getBundledServeUrl(entryPoint) {
  const cacheRoot = path.join(os.tmpdir(), "launchify-remotion-cache");
  const cacheKey = renderBundleCacheKey();
  const cachedBundlePath = path.join(cacheRoot, cacheKey);
  await mkdir(cacheRoot, { recursive: true });
  if (existsSync(cachedBundlePath)) {
    return cachedBundlePath;
  }
  return buildBundleAtomically(cacheRoot, cachedBundlePath, entryPoint);
}

async function buildBundleAtomically(cacheRoot, cachedBundlePath, entryPoint) {
  const tempBundlePath = await mkdtemp(path.join(cacheRoot, "bundle-"));
  try {
    const bundledPath = await bundle({
      entryPoint,
      onProgress: () => undefined,
      webpackOverride: (config) => config,
      outDir: tempBundlePath,
    });
    if (existsSync(cachedBundlePath)) {
      await rm(tempBundlePath, { recursive: true, force: true });
      return cachedBundlePath;
    }
    await rename(bundledPath, cachedBundlePath);
    return cachedBundlePath;
  } catch (error) {
    if (isAlreadyExistsError(error) && existsSync(cachedBundlePath)) {
      return cachedBundlePath;
    }
    throw error;
  } finally {
    if (existsSync(tempBundlePath) && tempBundlePath !== cachedBundlePath) {
      await rm(tempBundlePath, { recursive: true, force: true });
    }
  }
}

function isAlreadyExistsError(error) {
  return typeof error === "object" && error !== null && "code" in error && error.code === "EEXIST";
}

function renderBundleCacheKey() {
  const hash = createHash("sha1");
  for (const filePath of trackedBundleFiles()) {
    hash.update(filePath);
    hash.update(String(statSync(filePath).mtimeMs));
    hash.update(readFileSync(filePath));
  }
  return hash.digest("hex").slice(0, 16);
}

function trackedBundleFiles() {
  const srcDir = path.join(__dirname, "../src");
  const packageLockPath = path.join(__dirname, "../package-lock.json");
  return [packageLockPath, ...walkFiles(srcDir)];
}

function walkFiles(rootDir) {
  const entries = readdirSync(rootDir, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const fullPath = path.join(rootDir, entry.name);
    if (entry.isDirectory()) {
      return walkFiles(fullPath);
    }
    return [fullPath];
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
