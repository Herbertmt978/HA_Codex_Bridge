export const hostileCorpus = [
  "</div><script>globalThis.__xss = true</script>",
  '" onmouseover="globalThis.__xss=true',
  "<svg><script>globalThis.__xss=true</script></svg>",
  '<iframe srcdoc="<script>globalThis.__xss=true</script>"></iframe>',
  "java" + "script:alert(1)",
  "data:text/html,<script>globalThis.__xss=true</script>",
  "vbscript:msgbox(1)",
  "\\..\\..\\secrets.txt\r\nContent-Disposition: attachment",
  "<img src=x onerror=alert(1)>",
];

export function makeArtifact(overrides = {}) {
  return {
    artifact_id: "art_safe",
    filename: "note.txt",
    mime_type: "text/plain",
    ...overrides,
  };
}

export function makeEvent(overrides = {}) {
  return {
    event_id: `evt_${overrides.sequence || 1}`,
    thread_id: "thr_safe",
    sequence: 1,
    event_type: "message.completed",
    payload: { text: "hello" },
    ...overrides,
  };
}
