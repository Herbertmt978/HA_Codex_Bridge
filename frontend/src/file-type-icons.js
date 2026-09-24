// Bundled, self-authored document icons. No file content or filenames enter SVG markup.
const documentBase = `<path d="M10 2h19l9 9v32a3 3 0 0 1-3 3H10a3 3 0 0 1-3-3V5a3 3 0 0 1 3-3Z" fill="#fff" stroke="#c9d1db" stroke-width="1.4"/><path d="M29 2v9h9" fill="#edf1f7" stroke="#c9d1db" stroke-width="1.4"/>`;
const lines = `<path d="M18 17h15M18 22h15M18 27h12M18 32h15M18 37h11" stroke="#b8c7d8" stroke-width="1.5" stroke-linecap="round"/>`;
const sheet = `<path d="M17 17h16v20H17zM17 23h16M17 30h16M24 17v20" fill="none" stroke="#9ac9af" stroke-width="1.2"/>`;
const slide = `<rect x="16" y="17" width="18" height="17" rx="1" fill="#fff4ec" stroke="#e5b394"/><path d="M20 21h10M20 25h7" stroke="#d88759" stroke-width="1.5"/>`;
const photo = `<rect x="16" y="17" width="18" height="16" rx="1" fill="#e9f6f1" stroke="#8ebdad"/><circle cx="29" cy="21" r="2" fill="#e2bb6a"/><path d="m18 30 5-5 4 4 3-3 3 4" fill="none" stroke="#579a81" stroke-width="1.5"/>`;
const code = `<path d="m22 21-4 4 4 4m7-8 4 4-4 4m-2-10-3 12" stroke="#7188ad" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`;
const media = `<circle cx="25" cy="27" r="9" fill="#ecf5f6" stroke="#a3c9cd"/><path d="m23 23 7 4-7 4z" fill="#4e93a0"/>`;
const type = {
  word: { colour: "#185abd", glyph: "W", detail: lines },
  excel: { colour: "#107c41", glyph: "X", detail: sheet },
  powerpoint: { colour: "#c43e1c", glyph: "P", detail: slide },
  pdf: { colour: "#c43835", glyph: "PDF", detail: lines },
  image: { colour: "#397f76", glyph: "", detail: photo },
  text: { colour: "#657486", glyph: "", detail: lines },
  archive: { colour: "#856944", glyph: "ZIP", detail: lines },
  code: { colour: "#596f9f", glyph: "", detail: code },
  audio: { colour: "#725ca5", glyph: "♫", detail: lines },
  video: { colour: "#397f8d", glyph: "", detail: media },
  email: { colour: "#47749d", glyph: "@", detail: lines },
  file: { colour: "#657486", glyph: "", detail: lines },
};

export function fileTypeKind(filename) {
  const extension = String(filename || "").split(".").pop().toLowerCase();
  if (["doc", "docx", "odt", "rtf"].includes(extension)) return "word";
  if (["xls", "xlsx", "ods", "csv"].includes(extension)) return "excel";
  if (["ppt", "pptx", "odp"].includes(extension)) return "powerpoint";
  if (extension === "pdf") return "pdf";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(extension)) return "image";
  if (["txt", "md", "log", "json", "xml", "yaml", "yml"].includes(extension)) return "text";
  if (["zip", "7z", "tar", "gz"].includes(extension)) return "archive";
  if (["js", "jsx", "ts", "tsx", "py", "html", "css", "sh", "ps1", "c", "cpp", "cs", "go", "rs", "java"].includes(extension)) return "code";
  if (["mp3", "wav", "ogg", "m4a", "flac"].includes(extension)) return "audio";
  if (["mp4", "mov", "mkv", "webm", "avi"].includes(extension)) return "video";
  if (["eml", "msg"].includes(extension)) return "email";
  return "file";
}

export function fileTypeIconMarkup(filename) {
  const icon = type[fileTypeKind(filename)];
  const tile = icon.glyph
    ? `<rect x="1" y="19" width="24" height="23" rx="2.5" fill="${icon.colour}"/><text x="13" y="35" text-anchor="middle" font-family="Arial,sans-serif" font-size="${icon.glyph.length > 1 ? 8 : 18}" font-weight="700" fill="#fff">${icon.glyph}</text>`
    : `<rect x="1" y="19" width="7" height="23" rx="2" fill="${icon.colour}"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 42 48" fill="none" aria-hidden="true" focusable="false">${documentBase}${icon.detail}${tile}</svg>`;
}
