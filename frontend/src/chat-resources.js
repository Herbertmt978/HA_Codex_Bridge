// References come only from this chat. Never fetch links or infer PR status.
export function chatResources(events = []) {
  const pullRequests = new Map();
  const sources = new Map();
  const add = (value, title = "") => {
    if (typeof value !== "string" || value.length > 4096) return;
    let url;
    try { url = new URL(value); } catch { return; }
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return;
    const match = url.hostname === "github.com" && url.protocol === "https:"
      ? url.pathname.match(/^\/([\w.-]+)\/([\w.-]+)\/pull\/([1-9]\d*)(?:\/|$)/) : null;
    if (match) {
      const href = `https://github.com/${match[1]}/${match[2]}/pull/${match[3]}`;
      if (pullRequests.size < 100 && !pullRequests.has(href)) {
        pullRequests.set(href, { href, title: String(title || `${match[1]}/${match[2]} #${match[3]}`).slice(0, 240) });
      }
    } else if (sources.size < 100 && !sources.has(url.href)) {
      sources.set(url.href, { href: url.href, title: String(title || url.hostname).slice(0, 240) });
    }
  };
  for (const event of events.slice(-2000)) {
    const payload = event?.payload || {};
    if (["message.created", "message.completed"].includes(event?.event_type) && typeof payload.text === "string") {
      const text = payload.text.slice(0, 200000).replace(/```[\s\S]*?```|`[^`]*`/g, "");
      for (const match of text.matchAll(/\[([^\]\n]{1,240})\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>"')\]]+)/g)) {
        add(match[2] || match[3]?.replace(/[.,;!?]+$/, ""), match[1]);
      }
    }
    for (const source of [...(Array.isArray(payload.sources) ? payload.sources.slice(0, 100) : []), ...(Array.isArray(payload.citations) ? payload.citations.slice(0, 100) : [])]) {
      if (source && typeof source === "object") add(source.url, source.title);
    }
  }
  return { pullRequests: [...pullRequests.values()], sources: [...sources.values()] };
}

export function authenticatedChatUrl(location, threadId) {
  if (typeof threadId !== "string" || !/^[A-Za-z0-9_.:-]{1,200}$/.test(threadId)) return null;
  const url = new URL(location.href);
  if (!["http:", "https:"].includes(url.protocol)) return null;
  url.username = "";
  url.password = "";
  url.search = "";
  url.hash = "";
  url.searchParams.set("thread", threadId);
  return url.href;
}
