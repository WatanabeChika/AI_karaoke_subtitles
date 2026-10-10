import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../ai_karaoke/web/static/ass_preview.js", import.meta.url),
  "utf8",
);
const preview = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

const ass = [
  "[Events]",
  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
  "Dialogue: 0,0:00:01.00,0:00:04.00,K1,,0,0,0,,{\\kf100}●{\\kf50}涙|<なみだ{\\kf150}になる,よ",
  "Dialogue: 0,0:00:05.00,0:00:06.00,Other,,0,0,0,,{\\kf100}ignored",
].join("\n");

const entries = preview.parseAss(ass);
assert.equal(entries.length, 1);
assert.equal(entries[0].text, "●涙になる,よ");
assert.equal(entries[0].timelineText, "涙になる,よ");
assert.equal(entries[0].readingText, "なみだになる,よ");
assert.deepEqual(
  entries[0].segments.map(({ text, reading }) => ({ text, reading })),
  [
    { text: "●", reading: "" },
    { text: "涙", reading: "なみだ" },
    { text: "になる,よ", reading: "" },
  ],
);

const segment = { start: 10, end: 12 };
assert.equal(preview.segmentProgress(segment, 9.99), 0);
assert.equal(preview.segmentProgress(segment, 10), 0.015);
assert.equal(preview.segmentProgress(segment, 11), 0.5);
assert.equal(preview.segmentProgress(segment, 12), 1);
assert.equal(preview.segmentProgress({ start: 3, end: 3 }, 3), 1);
