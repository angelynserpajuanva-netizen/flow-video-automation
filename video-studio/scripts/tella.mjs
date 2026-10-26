#!/usr/bin/env node
// Tella helper: list your videos, or export one and download the MP4 into inbox/.
//   node scripts/tella.mjs list
//   node scripts/tella.mjs pull <videoId> [--4k] [--60fps] [--subtitles]

import { createWriteStream, existsSync } from "node:fs";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const envFile = path.join(root, ".env");
if (existsSync(envFile)) process.loadEnvFile(envFile);

const API = "https://api.tella.com/v1";
const KEY = process.env.TELLA_API_KEY;
if (!KEY) {
  console.error("TELLA_API_KEY is missing. Add it to video-studio/.env (see .env.example).");
  process.exit(1);
}

async function tella(method, route, body) {
  const res = await fetch(API + route, {
    method,
    headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Tella ${method} ${route} → ${res.status}: ${text}`);
  return text ? JSON.parse(text) : {};
}

async function list() {
  let cursor;
  do {
    const q = new URLSearchParams({ limit: "100", ...(cursor && { cursor }) });
    const data = await tella("GET", `/videos?${q}`);
    for (const v of data.videos ?? []) {
      console.log(`${v.id}\t${v.name ?? v.title ?? "(untitled)"}`);
    }
    cursor = data.pagination?.nextCursor;
  } while (cursor);
}

async function pull(videoId, flags) {
  const body = {
    granularity: "video",
    resolution: flags.has("--4k") ? "4k" : "1080p",
    fps: flags.has("--60fps") ? "60" : "30",
    subtitles: flags.has("--subtitles"),
  };
  console.log(`Starting export of ${videoId} (${body.resolution}, ${body.fps}fps)…`);
  let { export: exp } = await tella("POST", `/videos/${videoId}/exports`, body);

  while (exp.status !== "completed") {
    if (exp.status === "failed") throw new Error("Tella reported the export failed.");
    process.stdout.write(`\r  ${exp.status} ${exp.progress ?? 0}%   `);
    await new Promise((r) => setTimeout(r, 5000));
    // Export IDs contain slashes, so they must be encoded as one path segment.
    ({ export: exp } = await tella("GET", `/videos/${videoId}/exports/${encodeURIComponent(exp.exportId)}`));
  }

  const out = path.join(root, "inbox", `${videoId}.mp4`);
  console.log(`\nDownloading → ${path.relative(root, out)}`);
  const res = await fetch(exp.downloadUrl);
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  await pipeline(Readable.fromWeb(res.body), createWriteStream(out));
  console.log("Done.");
}

const [cmd, arg, ...rest] = process.argv.slice(2);
try {
  if (cmd === "list") await list();
  else if (cmd === "pull" && arg) await pull(arg, new Set(rest));
  else {
    console.log("Usage:\n  npm run tella:list\n  npm run tella:pull -- <videoId> [--4k] [--60fps] [--subtitles]");
    process.exit(1);
  }
} catch (err) {
  console.error(err.message);
  process.exit(1);
}
