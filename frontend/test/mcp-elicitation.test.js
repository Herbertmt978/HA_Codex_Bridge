import { describe, expect, it } from "vitest";
import { collectMcpContent, renderMcpElicitation } from "../src/views/mcp-elicitation.js";

const form = {
  interaction_id: "mcp-1", kind: "mcp_form", status: "pending",
  allowed_actions: ["answer", "decline", "cancel"],
  display: {
    title: "MCP server question", summary: '<img src=x onerror="window.__xss=1">',
    mcp_server: "calendar", mcp_fields: [
      { name: "choice", label: "Choose", kind: "select", options: ["yes", "no"], required: true },
      { name: "count", label: "Count", kind: "integer", required: false, minimum: 1, maximum: 3 },
      { name: "enabled", label: "Enabled", kind: "boolean", required: true },
    ],
  },
};

describe("MCP elicitation card", () => {
  it("renders untrusted text safely and collects typed answers", () => {
    const root = document.createElement("div");
    renderMcpElicitation(root, form);
    expect(root.querySelector("img")).toBeNull();
    expect(root.textContent).toContain(form.display.summary);
    expect(root.querySelector("[role='alertdialog']")).toBeTruthy();
    expect(root.querySelector('[data-action="answer-mcp-form"]')).toBeTruthy();
    const fields = root.querySelectorAll("[data-mcp-field]");
    fields[0].querySelector("select").value = "yes";
    fields[1].querySelector("input").value = "2";
    fields[2].querySelector("select").value = "false";
    expect(collectMcpContent(root, form)).toEqual({ choice: "yes", count: 2, enabled: false });
  });

  it("shows the HTTPS destination only as an explicit user link", () => {
    const root = document.createElement("div");
    const url = "https://login.example.com/authorise?one_time=private";
    const interaction = {
      ...form, kind: "mcp_url", allowed_actions: ["accept", "decline", "cancel"],
      display: { ...form.display, mcp_url_host: "login.example.com" },
      authorization_url: url,
    };
    renderMcpElicitation(root, interaction);
    const link = root.querySelector("a");
    expect(link.href).toBe(url);
    expect(link.rel).toContain("noopener");
    expect(link.rel).toContain("noreferrer");
    expect(link.target).toBe("_blank");
    expect(root.textContent).not.toContain("one_time=private");
    renderMcpElicitation(root, { ...interaction, authorization_url: "http://private.local/" });
    expect(root.querySelector("a")).toBeNull();
    expect(root.querySelector('[data-action="accept-interaction"]').disabled).toBe(true);
  });

  it("submits date-time fields with an explicit UTC offset", () => {
    const root = document.createElement("div");
    const interaction = {
      ...form,
      display: {
        ...form.display,
        mcp_fields: [{ name: "when", label: "When", kind: "string", format: "date-time", required: true }],
      },
    };
    renderMcpElicitation(root, interaction);
    root.querySelector("input").value = "2026-09-23T20:30";
    const content = collectMcpContent(root, interaction);
    expect(content.when).toMatch(/^2026-09-\d{2}T\d{2}:\d{2}:00\.000Z$/u);
    expect(Date.parse(content.when)).toBe(Date.parse("2026-09-23T20:30"));
  });

  it("rejects unsupported URLs, malformed email and selection counts before sending", () => {
    const root = document.createElement("div");
    const interaction = {
      ...form,
      display: { ...form.display, mcp_fields: [
        { name: "email", label: "Email", kind: "string", format: "email", required: true },
        { name: "site", label: "Site", kind: "string", format: "uri", required: true },
        { name: "items", label: "Items", kind: "multi_select", required: true,
          options: ["a", "b"], min_items: 2 },
      ] },
    };
    renderMcpElicitation(root, interaction);
    const fields = root.querySelectorAll("[data-mcp-field]");
    fields[0].querySelector("input").value = "person@example.com";
    fields[1].querySelector("input").value = "ftp://example.com/resource";
    fields[2].querySelector("input").checked = true;
    expect(collectMcpContent(root, interaction)).toBeNull();
    fields[1].querySelector("input").value = "https://example.com/resource";
    expect(collectMcpContent(root, interaction)).toBeNull();
    fields[2].querySelectorAll("input")[1].checked = true;
    expect(collectMcpContent(root, interaction)).toEqual({
      email: "person@example.com", site: "https://example.com/resource", items: ["a", "b"],
    });
    fields[0].querySelector("input").value = "person@example";
    expect(collectMcpContent(root, interaction)).toBeNull();
  });
});
