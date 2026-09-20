import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@xterm/xterm", () => ({ Terminal: class {
  options = {};
  parser = { registerOscHandler: vi.fn() };
  loadAddon() {}
  open() {}
  attachCustomKeyEventHandler() {}
  onData() {}
  write = vi.fn();
  dispose() {}
  focus() {}
} }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { proposeDimensions() { return null; } } }));
import { WorkspaceTerminalView } from "../src/workspace-terminal.js";

let terminal;
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(async () => { await terminal?.close(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("closes an orphan session when leaving during startup", async () => {
  let resolve;
  const call = vi.fn((operation) => operation === "open" ? new Promise((done) => { resolve = done; }) : Promise.resolve({}));
  terminal = new WorkspaceTerminalView(document.createElement("div"), call, vi.fn());
  const opening = terminal.open("chat");
  await vi.advanceTimersByTimeAsync(0);
  await terminal.close();
  resolve({ session_id: "late-session", state: "running" });
  await opening;
  expect(call).toHaveBeenCalledWith("close", { thread_id: "chat", session_id: "late-session" });
  expect(terminal.session).toBeNull();
});

it("drains remaining output after the shell exits", async () => {
  const call = vi.fn().mockResolvedValueOnce({ session_id: "session", state: "running", chunks: [] })
    .mockResolvedValueOnce({ state: "closed", chunks: [{ sequence: 1, data: btoa("first") }], has_more: true })
    .mockResolvedValueOnce({ state: "closed", chunks: [{ sequence: 2, data: btoa("last") }], has_more: false });
  terminal = new WorkspaceTerminalView(document.createElement("div"), call, vi.fn());
  await terminal.open("chat");
  const screen = terminal.term;
  await vi.advanceTimersByTimeAsync(150);
  expect(screen.write.mock.calls.map(([data]) => new TextDecoder().decode(data))).toEqual(["first", "last"]);
  expect(call.mock.calls.filter(([operation]) => operation === "read")).toHaveLength(2);
});

it("closes instead of replaying input after an ambiguous write", async () => {
  const call = vi.fn((operation) => operation === "open"
    ? Promise.resolve({ session_id: "session", state: "running", chunks: [] })
    : operation === "write" ? Promise.reject(new Error("connection lost")) : Promise.resolve({}));
  terminal = new WorkspaceTerminalView(document.createElement("div"), call, vi.fn());
  await terminal.open("chat");
  terminal._input("command\r", terminal.epoch);
  terminal._input("must not follow\r", terminal.epoch);
  await terminal.queue;
  expect(call.mock.calls.filter(([operation]) => operation === "write")).toHaveLength(1);
  expect(call).toHaveBeenCalledWith("close", { thread_id: "chat", session_id: "session" });
  expect(terminal.session).toBeNull();
});
