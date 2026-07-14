// Security helpers for the Home Assistant panel.  These functions deliberately
// avoid parsing untrusted strings as HTML; callers should use textContent and
// setAttribute with the returned values.

const RASTER_MIME_TYPES = new Set([
  "image/avif",
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);

const TEXT_EXTENSIONS = new Set([
  "c",
  "cfg",
  "conf",
  "cpp",
  "css",
  "csv",
  "diff",
  "env",
  "h",
  "ini",
  "js",
  "json",
  "log",
  "md",
  "py",
  "rst",
  "sh",
  "sql",
  "text",
  "toml",
  "ts",
  "tsx",
  "txt",
  "yaml",
  "yml",
]);

function removeControlChars(value) {
  return [...value].filter((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint > 31 && codePoint !== 127;
  }).join("");
}

export function sanitizeId(value, fallback = "") {
  const text = removeControlChars(String(value ?? ""))
    .replace(/[^A-Za-z0-9_.:-]/g, "")
    .trim();
  return text.slice(0, 200) || fallback;
}

export function sanitizeFilename(value, fallback = "download") {
  const text = removeControlChars(String(value ?? ""))
    .replace(/[\\/]/g, "_")
    .replace(/["']/g, "")
    .trim()
    .replace(/^\.+$/, "");
  return (text || fallback).slice(0, 255);
}

/**
 * Return an explicitly safe HTTP(S) URL, or null.  Relative URLs are resolved
 * against the current origin.  Remote origins are rejected unless callers
 * explicitly pass allowRemote=true (the panel does not do so for embeds).
 */
export function sanitizeUrl(value, { base, allowRemote = false } = {}) {
  if (typeof value !== "string" || !value.trim()) return null;
  let parsed;
  try {
    const origin = base || globalThis.location?.origin || "http://ha.invalid";
    parsed = new URL(value, origin);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (!allowRemote) {
    try {
      if (parsed.origin !== new URL(base || globalThis.location?.origin || "http://ha.invalid").origin) return null;
    } catch {
      return null;
    }
  }
  parsed.username = "";
  parsed.password = "";
  return parsed.href;
}

export const safeUrl = sanitizeUrl;

export function sanitizeBlobUrl(value, { origin } = {}) {
  if (typeof value !== "string" || !value.startsWith("blob:")) return null;
  try {
    const parsed = new URL(value);
    if (origin && parsed.origin !== origin) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function setText(node, value) {
  if (!node) return node;
  node.textContent = String(value ?? "");
  return node;
}

export const renderText = setText;

export function setSafeAttribute(node, name, value) {
  if (!node || typeof name !== "string") return false;
  const attr = name.toLowerCase();
  if (attr.startsWith("on") || attr === "srcdoc" || attr === "style") return false;
  if (["href", "src", "action", "formaction"].includes(attr)) {
    const safe = attr === "src" && String(value).startsWith("blob:")
      ? sanitizeBlobUrl(String(value), { origin: globalThis.location?.origin })
      : sanitizeUrl(String(value));
    if (!safe) return false;
    node.setAttribute(attr, safe);
    return true;
  }
  node.setAttribute(attr, String(value ?? ""));
  return true;
}

export function createSafeLink(document, value, label, { allowRemote = false } = {}) {
  const href = sanitizeUrl(value, { base: document?.defaultView?.location?.origin, allowRemote });
  if (!href || !document) return null;
  const link = document.createElement("a");
  link.href = href;
  link.textContent = String(label ?? href);
  if (allowRemote || new URL(href).origin !== document.defaultView?.location?.origin) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  return link;
}

function extensionOf(filename) {
  const name = String(filename ?? "").toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1) : "";
}

export function previewDescriptor(artifact = {}, blob = {}) {
  const mime = String(blob?.type || artifact?.mime_type || "").toLowerCase().split(";", 1)[0].trim();
  const filename = sanitizeFilename(artifact?.filename || artifact?.relative_path || "artifact", "artifact");
  const base = { artifactId: sanitizeId(artifact?.artifact_id), filename, contentType: mime || "application/octet-stream" };
  if (RASTER_MIME_TYPES.has(mime)) return { ...base, kind: "image", url: null };
  if (mime.startsWith("text/") && mime !== "text/html" && mime !== "text/xml") return { ...base, kind: "text", text: "" };
  if (TEXT_EXTENSIONS.has(extensionOf(filename)) && !mime.includes("html") && !mime.includes("svg") && !mime.includes("xml")) {
    return { ...base, kind: "text", text: "" };
  }
  // PDFs, HTML, SVG, and all other binary formats must not be embedded.
  return { ...base, kind: "binary" };
}

export function isSafeRasterMime(value) {
  return RASTER_MIME_TYPES.has(String(value ?? "").toLowerCase().split(";", 1)[0].trim());
}

export function createPreviewElement(document, descriptor, { blobUrl } = {}) {
  if (!document || !descriptor) return null;
  if (descriptor.kind === "text") {
    const pre = document.createElement("pre");
    pre.textContent = String(descriptor.text ?? "");
    return pre;
  }
  if (descriptor.kind === "image") {
    const url = sanitizeBlobUrl(blobUrl || descriptor.url, { origin: document.defaultView?.location?.origin });
    if (!url || !RASTER_MIME_TYPES.has(descriptor.contentType)) return null;
    const image = document.createElement("img");
    image.src = url;
    image.alt = descriptor.filename || "artifact preview";
    return image;
  }
  const empty = document.createElement("div");
  empty.textContent = `${descriptor.filename || "Artifact"} preview unavailable`;
  return empty;
}

export { RASTER_MIME_TYPES, TEXT_EXTENSIONS };
