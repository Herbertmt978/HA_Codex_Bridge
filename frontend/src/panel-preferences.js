export const DEFAULT_PREFERENCES = Object.freeze({ theme: "ha", textSize: "default", motion: "system", mode: "full-auto", model: "", thinking: "" });

export function normalisePreferences(value = {}) {
  const result = { ...DEFAULT_PREFERENCES };
  for (const [key, allowed] of Object.entries({ theme: ["ha", "light", "dark"], textSize: ["default", "large", "larger"], motion: ["system", "reduced"], mode: ["observe", "edit", "full-auto"] })) {
    if (allowed.includes(value?.[key])) result[key] = value[key];
  }
  for (const key of ["model", "thinking"]) {
    if (typeof value?.[key] === "string" && /^[a-zA-Z0-9._-]{0,100}$/u.test(value[key])) result[key] = value[key];
  }
  return result;
}

export function readPreferences(storage, key) {
  try { return normalisePreferences(JSON.parse(storage.getItem(key) || "{}")); }
  catch { return { ...DEFAULT_PREFERENCES }; }
}

export function savePreferences(storage, key, value) {
  const preferences = normalisePreferences(value);
  storage.setItem(key, JSON.stringify(preferences));
  return preferences;
}
