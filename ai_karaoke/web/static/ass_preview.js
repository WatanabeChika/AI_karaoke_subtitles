const KARAOKE_TAG = /\{\\k(?:f|o)?(\d+)\}/gi;
const OVERRIDE_TAG = /\{[^}]*\}/g;
const COUNTDOWN_PREFIX = /^[\s\u3000●]+/;

export function parseAssTime(value) {
  const match = /^(\d+):(\d{2}):(\d{2})\.(\d{2})$/.exec(value.trim());
  if (!match) return Number.NaN;
  return (
    Number(match[1]) * 3600
    + Number(match[2]) * 60
    + Number(match[3])
    + Number(match[4]) / 100
  );
}

function splitDialogue(line) {
  const payload = line.slice("Dialogue:".length).trimStart();
  const fields = [];
  let offset = 0;
  for (let index = 0; index < 9; index += 1) {
    const comma = payload.indexOf(",", offset);
    if (comma < 0) return null;
    fields.push(payload.slice(offset, comma));
    offset = comma + 1;
  }
  fields.push(payload.slice(offset));
  return fields;
}

function cleanAssText(value) {
  return value
    .replace(OVERRIDE_TAG, "")
    .replace(/\\[Nn]/g, " ")
    .replace(/\\h/g, "\u00a0");
}

function splitReading(value) {
  const separator = value.indexOf("|<");
  if (separator < 0) return { text: value, reading: "" };
  return {
    text: value.slice(0, separator),
    reading: value.slice(separator + 2).trim(),
  };
}

function parseKaraokeSegments(text, eventStart) {
  const tags = [...text.matchAll(KARAOKE_TAG)];
  if (!tags.length) {
    const visible = splitReading(cleanAssText(text));
    return [{ ...visible, start: eventStart, end: eventStart }];
  }

  let cursor = eventStart;
  return tags.map((tag, index) => {
    const fragmentStart = tag.index + tag[0].length;
    const fragmentEnd = index + 1 < tags.length ? tags[index + 1].index : text.length;
    const duration = Number(tag[1]) / 100;
    const visible = splitReading(cleanAssText(text.slice(fragmentStart, fragmentEnd)));
    const segment = {
      ...visible,
      start: cursor,
      end: cursor + duration,
    };
    cursor = segment.end;
    return segment;
  });
}

function timelineText(value) {
  return value.replace(COUNTDOWN_PREFIX, "").trim();
}

export function parseAss(content) {
  const entries = [];
  const chunks = content.match(/[^\n]*(?:\n|$)/g) || [];
  let sourceOffset = 0;

  chunks.forEach((chunk, sourceIndex) => {
    if (!chunk) return;
    const line = chunk.replace(/\r?\n$/, "");
    if (line.startsWith("Dialogue:")) {
      const fields = splitDialogue(line);
      const style = fields?.[3]?.trim().toLowerCase();
      if (fields && (style === "k1" || style === "k2")) {
        const start = parseAssTime(fields[1]);
        const end = parseAssTime(fields[2]);
        if (Number.isFinite(start) && Number.isFinite(end)) {
          const segments = parseKaraokeSegments(fields[9], start);
          const text = segments.map(segment => segment.text).join("");
          const reading = segments.map(segment => segment.reading || segment.text).join("");
          entries.push({
            id: `${sourceOffset}:${style}`,
            sourceIndex,
            sourceStart: sourceOffset,
            sourceEnd: sourceOffset + line.length,
            start,
            end,
            style,
            text,
            timelineText: timelineText(text),
            readingText: timelineText(reading),
            segments,
          });
        }
      }
    }
    sourceOffset += chunk.length;
  });

  return entries;
}

export function segmentProgress(segment, currentTime) {
  if (currentTime < segment.start) return 0;
  if (segment.end <= segment.start || currentTime >= segment.end) return 1;
  const progress = (currentTime - segment.start) / (segment.end - segment.start);
  return progress === 0 ? 0.015 : Math.min(1, Math.max(0, progress));
}
