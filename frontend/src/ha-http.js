const refreshes = new WeakMap();

function abortIfNeeded(signal) {
  if (signal?.aborted) throw new DOMException("The request was cancelled.", "AbortError");
}

function validatePath(path) {
  const hasControlOrSpace = (value) => [...value].some((character) => character.charCodeAt(0) <= 32);
  if (typeof path !== "string" || !path.startsWith("/api/codex_bridge/") || /[\\?#]/u.test(path) || hasControlOrSpace(path)) throw new Error("Invalid Home Assistant API path");
  for (const segment of path.slice(1).split("/")) {
    let decoded;
    try { decoded = decodeURIComponent(segment); } catch { throw new Error("Invalid Home Assistant API path"); }
    if (!decoded || decoded === "." || decoded === ".." || /[\\/]/u.test(decoded) || hasControlOrSpace(decoded)) throw new Error("Invalid Home Assistant API path");
  }
}

/** Raw Bridge HTTP uses HA's auth owner; upload mutations are never replayed here. */
export async function fetchHomeAssistantApi(hass, path, init = {}, { fetchImpl = fetch, accessToken = () => "" } = {}) {
  validatePath(path);
  abortIfNeeded(init.signal);
  const headers = init.headers instanceof Headers || Array.isArray(init.headers)
    ? Object.fromEntries(new Headers(init.headers).entries()) : { ...init.headers };
  for (const name of Object.keys(headers)) if (name.toLowerCase() === "authorization") delete headers[name];
  const request = { ...init, headers, credentials: "same-origin", mode: "same-origin", redirect: "error" };
  if (typeof hass?.fetchWithAuth === "function") return hass.fetchWithAuth(path, request);

  // Legacy panel hosts and the local harness may lack fetchWithAuth. Refresh
  // through their existing Auth instance, without owning tokens or login state.
  const auth = hass?.auth || hass?.connection?.options?.auth;
  if (auth?.expired === true) {
    if (typeof auth.refreshAccessToken !== "function") throw new Error("Home Assistant sign-in expired. Reload the page and sign in again.");
    let pending = refreshes.get(auth);
    if (!pending) {
      pending = Promise.resolve().then(() => auth.refreshAccessToken());
      refreshes.set(auth, pending);
    }
    try { await pending; }
    catch { throw new Error("Home Assistant sign-in could not be refreshed. Reload the page and sign in again."); }
    finally { if (refreshes.get(auth) === pending) refreshes.delete(auth); }
  }
  abortIfNeeded(init.signal);
  const token = accessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetchImpl(path, request);
}
