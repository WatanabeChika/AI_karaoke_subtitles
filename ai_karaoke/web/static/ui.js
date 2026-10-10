export function createElement(tag, className = "", text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

export function assToHex(value) {
  const match = /^&H[0-9A-Fa-f]{2}([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$/.exec(
    (value || "").trim(),
  );
  return match ? `#${match[3]}${match[2]}${match[1]}` : null;
}

function hexToAss(value) {
  const hex = value.replace("#", "").toUpperCase();
  return `&H00${hex.slice(4, 6)}${hex.slice(2, 4)}${hex.slice(0, 2)}`;
}

export function bindColor(textId, pickerId) {
  const text = document.getElementById(textId);
  const picker = document.getElementById(pickerId);
  const syncFromText = () => {
    const hex = assToHex(text.value);
    if (hex) picker.value = hex;
  };
  text.addEventListener("input", syncFromText);
  picker.addEventListener("input", () => {
    text.value = hexToAss(picker.value);
  });
  syncFromText();
}

export function formatClock(seconds) {
  const safe = Math.max(0, seconds || 0);
  const minutes = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  const centis = Math.floor((safe % 1) * 100);
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(centis).padStart(2, "0")}`;
}
