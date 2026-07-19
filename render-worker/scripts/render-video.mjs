import { mkdir, mkdtemp, readFile, rename, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { bundle } from "@remotion/bundler";
import { getCompositions, renderMedia } from "@remotion/renderer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const mode = process.argv[2] ?? "preview";
const options = parseArgs(process.argv.slice(3));
const isPreviewRender = mode === "preview";
const renderConcurrency = positiveInt(process.env.RENDER_CONCURRENCY, 1);
const offthreadVideoThreads = positiveInt(process.env.RENDER_OFFTHREAD_VIDEO_THREADS, 1);
const mediaCacheSizeInBytes = clampMegabytes(
  positiveInt(process.env.RENDER_MEDIA_CACHE_SIZE_MB, 32),
  isPreviewRender ? 12 : 24,
) * 1024 * 1024;
const offthreadVideoCacheSizeInBytes = clampMegabytes(
  positiveInt(process.env.RENDER_OFFTHREAD_VIDEO_CACHE_SIZE_MB, 32),
  isPreviewRender ? 8 : 16,
) * 1024 * 1024;
const renderScale = boundedFloat(process.env.RENDER_SCALE, isPreviewRender ? 0.65 : 1);
const remotionTimeoutInMilliseconds = positiveInt(process.env.REMOTION_TIMEOUT_MS, isPreviewRender ? 240000 : 420000);

await renderVideo(mode, options);

async function renderVideo(mode, options) {
  assertOption(options.input, "--input is required");
  assertOption(options.output, "--output is required");
  assertOption(options.source, "--source is required");
  const rawPayload = JSON.parse(await readFile(options.input, "utf-8"));
  const clipAudioPaths = Array.isArray(rawPayload.voiceover?.clips)
    ? rawPayload.voiceover.clips.map((clip) => clip.audio_storage_path).filter(Boolean)
    : [];
  const assetPaths = [options.source, rawPayload.voiceoverAudioPath, ...clipAudioPaths].filter(Boolean);
  await withServedAssets(assetPaths, async (assetServer) => {
    const payload = preparePayload(rawPayload, options.source, assetServer);
    const entryPoint = path.join(__dirname, "../src/index.ts");
    console.log(
      `[launchify-render-worker] Starting ${mode} render at ${payload.dimensions.width}x${payload.dimensions.height}` +
        ` with concurrency=${renderConcurrency}, offthreadVideoThreads=${offthreadVideoThreads}.`,
    );
    console.log("[launchify-render-worker] Resolving bundled Remotion project.");
    const bundled = await getBundledServeUrl(entryPoint);
    console.log("[launchify-render-worker] Discovering compositions.");
    const compositions = await getCompositions(bundled, {
      inputProps: payload,
      logLevel: "info",
      timeoutInMilliseconds: remotionTimeoutInMilliseconds,
      onBrowserLog: (log) => console.log(`[launchify-render-worker] browser:${log.type} ${log.text}`),
    });
    const composition = requireComposition(compositions);
    console.log(`[launchify-render-worker] Rendering composition ${composition.id}.`);
    await renderMedia({
      codec: "h264",
      concurrency: renderConcurrency,
      composition,
      chromiumOptions: {
        disableWebSecurity: true,
        enableMultiProcessOnLinux: false,
        gl: "swangle",
      },
      disallowParallelEncoding: true,
      imageFormat: isPreviewRender ? "jpeg" : undefined,
      jpegQuality: isPreviewRender ? 80 : undefined,
      logLevel: "info",
      mediaCacheSizeInBytes,
      onBrowserLog: (log) => console.log(`[launchify-render-worker] browser:${log.type} ${log.text}`),
      onProgress: ({ renderedFrames, encodedFrames, renderedDoneIn }) => {
        console.log(
          `[launchify-render-worker] progress rendered=${renderedFrames} encoded=${encodedFrames ?? 0}` +
            ` elapsedMs=${renderedDoneIn ?? 0}`,
        );
      },
      onStart: () => {
        console.log("[launchify-render-worker] Browser ready, frame rendering started.");
      },
      inputProps: payload,
      offthreadVideoCacheSizeInBytes,
      offthreadVideoThreads,
      outputLocation: options.output,
      pixelFormat: "yuv420p",
      scale: renderScale,
      serveUrl: bundled,
      timeoutInMilliseconds: remotionTimeoutInMilliseconds,
    });
    console.log(`[launchify-render-worker] Completed ${mode} render: ${options.output}`);
  });
}

async function withServedAssets(assetPaths, callback) {
  if (assetPaths.every(isRemoteAsset)) {
    return callback({ urlFor: (assetPath) => assetPath });
  }
  const assets = new Map(
    assetPaths.map((assetPath) => {
      const absolutePath = path.resolve(assetPath);
      return [assetPath, { absolutePath, fileName: encodeURIComponent(path.basename(absolutePath)) }];
    }),
  );
  const server = createServer((req, res) => {
    const asset = [...assets.values()].find((item) => req.url === `/${item.fileName}`);
    if (!req.url || !asset) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    res.writeHead(200, {
      "Access-Control-Allow-Origin": "*",
      "Content-Length": statSync(asset.absolutePath).size,
      "Content-Type": asset.absolutePath.endsWith(".mp3") ? "audio/mpeg" : "video/mp4",
    });
    createReadStream(asset.absolutePath).pipe(res);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("Could not start local render asset server.");
  }
  try {
    return await callback({
      urlFor: (assetPath) => {
        if (!assetPath || isRemoteAsset(assetPath)) {
          return assetPath;
        }
        const asset = assets.get(assetPath);
        return asset ? `http://127.0.0.1:${address.port}/${asset.fileName}` : assetPath;
      },
    });
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

async function getBundledServeUrl(entryPoint) {
  const cacheRoot = path.join(os.tmpdir(), "launchify-remotion-cache");
  const cacheKey = renderBundleCacheKey();
  const cachedBundlePath = path.join(cacheRoot, cacheKey);
  await mkdir(cacheRoot, { recursive: true });
  if (existsSync(cachedBundlePath)) {
    console.log(`[launchify-render-worker] Using cached bundle ${cacheKey}.`);
    return cachedBundlePath;
  }
  console.log(`[launchify-render-worker] Building new bundle ${cacheKey}.`);
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

function positiveInt(value, fallback) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return fallback;
  }
  return parsed;
}

function boundedFloat(value, fallback) {
  const parsed = Number.parseFloat(value ?? "");
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > 1) {
    return fallback;
  }
  return parsed;
}

function clampMegabytes(value, limit) {
  return Math.max(4, Math.min(value, limit));
}

function preparePayload(payload, sourcePath, assetServer) {
  return {
    ...payload,
    quality: payload.quality ?? mode,
    sourceVideoPath: withFileProtocol(assetServer.urlFor(sourcePath)),
    voiceoverAudioPath: assetServer.urlFor(payload.voiceoverAudioPath),
    voiceover: {
      ...payload.voiceover,
      clips: Array.isArray(payload.voiceover?.clips)
        ? payload.voiceover.clips.map((clip) => ({
            ...clip,
            audio_storage_path: assetServer.urlFor(clip.audio_storage_path),
          }))
        : [],
    },
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
  return sourcePath;
}

function isRemoteAsset(assetPath) {
  return assetPath.startsWith("http://") || assetPath.startsWith("https://");
}
