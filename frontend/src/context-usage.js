/** Use the last model request, never cumulative billing tokens, for context. */
export function contextUsage(value) {
  const used = value?.used_tokens;
  const window = value?.context_window;
  if (!Number.isSafeInteger(used) || used < 0) {
    return { known: false, percent: null, label: "Context usage not reported yet" };
  }
  const hasWindow = Number.isSafeInteger(window) && window > 0;
  const percent = hasWindow ? Math.min(100, Math.max(0, Math.round(used / window * 100))) : null;
  const count = new Intl.NumberFormat("en-GB").format(used);
  return {
    known: true, used, window: hasWindow ? window : null, percent,
    label: hasWindow
      ? `${percent}% of context used · ${count} of ${new Intl.NumberFormat("en-GB").format(window)} tokens`
      : `${count} context tokens · window size not reported`,
  };
}
