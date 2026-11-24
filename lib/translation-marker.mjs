export const INTERNAL_TRANSLATION_MARKER =
  /Q[\s-]*Z(?:[\s-]*[A-Z]){5,8}|\[{1,4}\s*[A-Z]{5,10}\s*\]{1,4}/;

export function findTranslationMarker(value) {
  const match = INTERNAL_TRANSLATION_MARKER.exec(String(value || ''));
  return match ? { marker: match[0], index: match.index } : null;
}

export function translationMarkerContext(value, marker) {
  const text = String(value || '');
  const position = text.indexOf(marker);
  if (position < 0) return text.slice(0, 160);
  const start = Math.max(0, position - 80);
  const end = Math.min(text.length, position + marker.length + 120);
  return text.slice(start, end);
}
