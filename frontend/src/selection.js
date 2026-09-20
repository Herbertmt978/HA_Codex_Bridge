let sequence = 0;

/** A select-backed combobox. The native field keeps form values and events;
 * the popover keeps the visible menu inside the panel's theme and viewport. */
export function selection(doc, { name, label, value = "", options = [] }) {
  const root = doc.createElement("div"); root.className = "panel-selection";
  const select = doc.createElement("select");
  select.name = name; select.hidden = true; select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  const trigger = doc.createElement("button");
  trigger.type = "button"; trigger.className = "selection-trigger";
  trigger.setAttribute("role", "combobox"); trigger.setAttribute("aria-label", label);
  trigger.setAttribute("aria-haspopup", "listbox"); trigger.setAttribute("aria-expanded", "false");
  const caption = doc.createElement("span");
  const arrow = doc.createElement("span"); arrow.className = "selection-arrow"; arrow.setAttribute("aria-hidden", "true");
  trigger.append(caption, arrow);
  const menu = doc.createElement("div"); menu.className = "selection-menu";
  menu.id = `panel-selection-${++sequence}`; menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", label); menu.setAttribute("popover", "auto"); menu.hidden = true;
  trigger.setAttribute("aria-controls", menu.id);
  root.append(select, trigger, menu);
  let active = -1;
  let search = "";
  let searchedAt = 0;
  let openListeners;
  let removalObserver;
  const isOpen = () => trigger.getAttribute("aria-expanded") === "true";
  const activate = (index) => {
    active = index;
    [...menu.children].forEach((item, position) => item.classList.toggle("active", position === active));
    if (menu.children[active]) {
      trigger.setAttribute("aria-activedescendant", menu.children[active].id);
      menu.children[active].scrollIntoView?.({ block: "nearest" });
    }
  };
  const close = () => {
    openListeners?.abort(); removalObserver?.disconnect();
    if (menu.matches(":popover-open")) menu.hidePopover();
    menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); trigger.removeAttribute("aria-activedescendant");
  };
  const sync = () => {
    caption.textContent = select.options[select.selectedIndex]?.textContent || "Choose…";
    [...menu.children].forEach((item, index) => item.setAttribute("aria-selected", String(index === select.selectedIndex)));
  };
  const choose = (index) => {
    if (!select.options[index] || select.options[index].disabled) return;
    select.selectedIndex = index; sync(); close(); trigger.focus();
    select.dispatchEvent(new doc.defaultView.Event("change", { bubbles: true }));
  };
  const open = () => {
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 240), doc.documentElement.clientWidth - 24);
    const below = doc.defaultView.innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const upwards = below < 220 && above > below;
    menu.style.width = `${Math.max(0, width)}px`;
    menu.style.left = `${Math.max(12, Math.min(rect.right - width, doc.documentElement.clientWidth - width - 12))}px`;
    menu.style.maxHeight = `${Math.max(44, Math.min(320, upwards ? above : below))}px`;
    menu.style.top = upwards ? "auto" : `${rect.bottom + 4}px`;
    menu.style.bottom = upwards ? `${doc.defaultView.innerHeight - rect.top + 4}px` : "auto";
    menu.hidden = false; menu.showPopover?.(); trigger.setAttribute("aria-expanded", "true");
    openListeners = new doc.defaultView.AbortController();
    const signal = openListeners.signal;
    doc.addEventListener("scroll", (event) => { if (event.target !== menu) close(); }, { capture: true, signal });
    doc.defaultView.addEventListener("resize", close, { signal });
    if (!menu.showPopover) doc.addEventListener("pointerdown", (event) => { if (!event.composedPath().includes(root)) close(); }, { signal });
    removalObserver = new doc.defaultView.MutationObserver(() => { if (!root.isConnected) close(); });
    removalObserver.observe(root.getRootNode(), { childList: true, subtree: true });
    activate(select.selectedIndex >= 0 && !select.options[select.selectedIndex].disabled ? select.selectedIndex : [...select.options].findIndex((item) => !item.disabled));
  };
  trigger.addEventListener("click", () => isOpen() ? close() : open());
  trigger.addEventListener("keydown", (event) => {
    const enabled = [...select.options].map((item, index) => item.disabled ? -1 : index).filter((index) => index >= 0);
    if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " ", "Escape"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      if (event.key === "Escape") { close(); return; }
      if (!isOpen()) { open(); return; }
      if (event.key === "Enter" || event.key === " ") { choose(active); return; }
      const position = enabled.indexOf(active);
      activate(event.key === "Home" ? enabled[0] : event.key === "End" ? enabled.at(-1)
        : enabled[(position + (event.key === "ArrowUp" ? -1 : 1) + enabled.length) % enabled.length]);
    } else if (event.key === "Tab") close();
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault(); event.stopPropagation();
      if (!isOpen()) open();
      search = Date.now() - searchedAt < 700 ? search + event.key : event.key; searchedAt = Date.now();
      const match = enabled.find((index) => select.options[index].textContent.toLowerCase().startsWith(search.toLowerCase()));
      if (match !== undefined) activate(match);
    }
  });
  trigger.addEventListener("blur", () => { if (!menu.matches(":popover-open")) close(); });
  menu.addEventListener("toggle", (event) => { if (event.newState === "closed") close(); });
  menu.addEventListener("pointerdown", (event) => event.preventDefault());
  select.addEventListener("change", sync);
  root.setOptions = (choices, selected = select.value) => {
    close(); select.replaceChildren(); menu.replaceChildren();
    choices.forEach(([key, title, disabled = false], index) => {
      const option = doc.createElement("option"); option.value = key; option.textContent = title; option.disabled = disabled; select.append(option);
      const item = doc.createElement("div"); item.id = `${menu.id}-${index}`; item.setAttribute("role", "option");
      item.setAttribute("aria-disabled", String(disabled)); item.textContent = title;
      item.addEventListener("click", () => choose(index)); menu.append(item);
    });
    select.value = selected; sync();
  };
  root.setOptions(options, value);
  return root;
}

export const SELECTION_STYLES = `
  .panel-selection { min-width: 0; width: max-content; max-width: 65%; }
  .selection-trigger { width: 100%; min-height: 44px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid transparent; border-radius: 10px; padding: 8px 12px; background: transparent; color: var(--text-color); font: inherit; font-size: var(--font-body-size, 14px); cursor: pointer; text-align: left; }
  .selection-trigger > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .selection-trigger:hover, .selection-trigger[aria-expanded="true"] { background: var(--surface-alt); border-color: var(--border-color); }
  .selection-trigger:focus-visible { outline: 2px solid var(--accent-color); outline-offset: 2px; }
  .selection-arrow { display: grid; place-items: center; width: 16px; height: 16px; flex-shrink: 0; color: var(--muted-color); }
  .selection-arrow::before { content: ""; width: 6px; height: 6px; border-right: 1.75px solid currentColor; border-bottom: 1.75px solid currentColor; transform: translateY(-2px) rotate(45deg); }
  .selection-menu { position: fixed; inset: auto; margin: 0; box-sizing: border-box; padding: 6px; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--border-color); border-radius: 12px; background: var(--surface-bg); color: var(--text-color); box-shadow: 0 8px 32px #0003; font-size: var(--font-body-size, 14px); z-index: 100; }
  .selection-menu[hidden] { display: none; }
  .selection-menu [role="option"] { min-height: 44px; box-sizing: border-box; display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 7px; cursor: pointer; overflow-wrap: anywhere; }
  .selection-menu [role="option"]::after { content: ""; margin-left: auto; width: 16px; flex-shrink: 0; }
  .selection-menu [aria-selected="true"]::after { content: "✓"; }
  .selection-menu [role="option"]:hover, .selection-menu .active { background: var(--surface-alt); }
  .selection-menu [aria-disabled="true"] { opacity: .5; cursor: default; }
  @media (max-width: 540px) { .panel-selection { max-width: 60%; } .selection-trigger { font-size: 14px; padding-inline: 8px; } }
`;
