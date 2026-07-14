import { describe, expect, it } from "vitest";

import { getAuthViewModel, normalizePlanType, renderAuth } from "../src/views/auth.js";

describe("auth view", () => {
  it("normalizes only recognized ChatGPT plan names", () => {
    expect(normalizePlanType("PLUS")).toBe("Plus");
    expect(normalizePlanType('<img src=x onerror="alert(1)">')).toBe("Unknown");
  });

  it("offers live sign-in controls and phone guidance without exposing a verification URL", () => {
    const model = getAuthViewModel({
      state: "login_running",
      user_code: "ABCD-EFGH",
      verification_uri: "https://chatgpt.example/device?secret=abc",
      account: { plan_type: "pro" },
    });

    expect(model).toMatchObject({ code: "ABCD-EFGH", canCopyCode: true, canOpen: true, plan: "Pro" });
    expect(model.actions.map((action) => action.id)).toContain("cancel-sign-in");
    expect(model.verificationUrl).toBeUndefined();
    expect(model.guidance).toContain("phone");
  });

  it("clears terminal device codes and requires an explicit sign-out confirmation", () => {
    const terminal = getAuthViewModel({ state: "signed_out", user_code: "ABCD-EFGH" });
    const confirmation = getAuthViewModel({ state: "ok", signedOutConfirmed: false });

    expect(terminal.code).toBeNull();
    expect(terminal.canCopyCode).toBe(false);
    expect(confirmation.actions.map((action) => action.id)).toContain("confirm-sign-out");
  });

  it.each([
    ["login_canceling", "Cancelling"],
    ["login_completing", "Finishing"],
    ["logout_running", "Signing out"],
  ])("blocks duplicate account mutations while %s", (state, message) => {
    const model = getAuthViewModel({ state, user_code: "MUST-HIDE" });

    expect(model.busy).toBe(true);
    expect(model.actions).toEqual([]);
    expect(model.code).toBeNull();
    expect(model.message).toContain(message);
  });

  it("offers a deliberate retry after sign-out fails", () => {
    const model = getAuthViewModel({ state: "logout_failed", signedOutConfirmed: true });

    expect(model.message).toContain("Sign-out did not complete");
    expect(model.actions.map((action) => action.id)).toEqual(["sign-out"]);
  });

  it("rejects non-ChatGPT authentication modes without exposing credential instructions", () => {
    const model = getAuthViewModel({
      state: "ok",
      auth_mode: "apikey",
      account: { available: true, auth_mode: "apikey", plan_type: "plus" },
    });

    expect(model.signedIn).toBe(false);
    expect(model.message).toBe("Only ChatGPT account sign-in is supported.");
    expect(model.actions.map((action) => action.id)).toEqual(["confirm-sign-out"]);
    expect(JSON.stringify(model)).not.toContain("credential");
  });

  it("renders code actions without unsafe auth payloads or credentials", () => {
    const container = document.createElement("div");
    renderAuth(container, getAuthViewModel({
      state: "login_running",
      user_code: "CODE-1234",
      verification_uri: "https://chatgpt.example/device?secret=abc",
      refresh_token: "do-not-display",
      api_key: "do-not-display",
    }));

    expect(container.querySelector("[data-action='copy-auth-code']")?.textContent).toBe("Copy code");
    expect(container.querySelector("[data-action='open-chatgpt']")?.textContent).toBe("Open ChatGPT");
    expect(container.textContent).not.toContain("secret=abc");
    expect(container.textContent).not.toContain("do-not-display");
  });
});
