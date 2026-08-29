/*
 * One-shot zyBooks DOM observer.  It inventories and classifies only; it
 * contains no answer logic and no click/fill/dispatch implementation.
 *
 * Evaluate as:
 *   () => window.__zybooksDeterministicObserver.inspect({targetId: "..."})
 *
 * The retained MutationObserver changes page generation when durable DOM
 * structure changes.  dispose() is the explicit cancellation path.
 */
(() => {
  "use strict";

  const RUNTIME_KEY = "__zybooksDeterministicObserver";
  const RUNTIME_VERSION = 4;
  const SAFE_ACTIVITY_CONTROLS = new Set([
    "radio",
    "checkbox",
    "animation_button",
    "play_button",
    "render_button",
    "check_button",
    "button",
    "svg",
  ]);
  const PRIMARY_ACTIVITY_CONTROLS = new Set(["radio", "checkbox", "animation_button", "play_button"]);

  function hash(input) {
    let value = 2166136261;
    for (let index = 0; index < input.length; index += 1) {
      value ^= input.charCodeAt(index);
      value = Math.imul(value, 16777619);
    }
    return (value >>> 0).toString(16).padStart(8, "0");
  }

  function sectionIdentity() {
    const match = location.pathname.match(/\/chapter\/([^/]+)\/section\/([^/]+)/i);
    if (!match) return { chapter: null, section: null, key: "unknown" };
    const chapter = match[1].replace(/[^a-z0-9_.-]/gi, "");
    const section = match[2].replace(/[^a-z0-9_.-]/gi, "");
    return { chapter, section, key: `${chapter}.${section}` };
  }

  function classTokens(element) {
    return Array.from(element.classList || []).map((token) => token.toLowerCase());
  }

  function attrNames(element) {
    return Array.from(element.attributes || [])
      .map((attribute) => attribute.name.toLowerCase())
      .filter((name) => name !== "value" && name !== "title" && !name.startsWith("aria-label"))
      .sort();
  }

  function visible(element) {
    if (!(element instanceof Element)) return false;
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
  }

  function safeIdentity(element, fallback) {
    const candidates = [
      element.getAttribute("data-activity-id"),
      element.getAttribute("data-activity"),
      element.id,
    ];
    for (const candidate of candidates) {
      if (candidate && /^[a-z0-9_.:-]{1,80}$/i.test(candidate) && /\d/.test(candidate)) return candidate;
    }
    return fallback;
  }

  function structuralFingerprint(root) {
    const parts = [];
    const nodes = [root, ...Array.from(root.querySelectorAll("*"))].slice(0, 500);
    for (const node of nodes) {
      const classes = classTokens(node).filter((token) => token.length < 80).sort().join(".");
      const role = node.getAttribute("role") || "";
      const type = node.getAttribute("type") || "";
      const disabled = node.matches("button,input,select,textarea") && (node.disabled || node.getAttribute("aria-disabled") === "true");
      parts.push(`${node.tagName.toLowerCase()}|${classes}|${role}|${type}|${disabled}|${node.children.length}|${attrNames(node).join(",")}`);
    }
    return hash(parts.join("\n"));
  }

  function pageFingerprint(section) {
    const activities = Array.from(document.querySelectorAll(".interactive-activity-container"));
    return hash([
      location.pathname,
      document.title,
      section.key,
      ...activities.map((activity) => structuralFingerprint(activity)),
    ].join("\n"));
  }

  function sectionHeading() {
    for (const heading of document.querySelectorAll("h1, h2, h3")) {
      const text = (heading.textContent || "").replace(/\s+/g, " ").trim();
      if (text && /section|chapter|\d+\.\d+/.test(text.toLowerCase())) return text.slice(0, 160);
    }
    return null;
  }

  function controlTypes(root) {
    const types = new Set();
    if (root.querySelector("input[type='radio'], .zb-radio-button input")) types.add("radio");
    if (root.querySelector("input[type='checkbox'], .zb-checkbox input")) types.add("checkbox");
    if (root.querySelector("input:not([type]), input[type='text'], textarea, .zb-input")) types.add("text_input");
    if (root.querySelector("select")) types.add("select");
    if (root.querySelector("button.start-button")) types.add("animation_button");
    if (root.querySelector("button[aria-label='Play']")) types.add("play_button");
    if (root.querySelector(".render-webpage")) types.add("render_button");
    if (root.querySelector("button.check-button")) types.add("check_button");
    if (root.querySelector("button")) types.add("button");
    if (root.querySelector("iframe")) types.add("iframe");
    if (root.querySelector("canvas")) types.add("canvas");
    if (root.querySelector("svg")) types.add("svg");
    return Array.from(types).sort();
  }

  function roles(root) {
    return Array.from(root.querySelectorAll("[role]"))
      .map((element) => element.getAttribute("role"))
      .filter((role) => role && /^[a-z-]{1,40}$/i.test(role))
      .filter((role, index, values) => values.indexOf(role) === index)
      .sort();
  }

  function structuralSource(root) {
    return [root, ...Array.from(root.querySelectorAll("*"))]
      .slice(0, 500)
      .map((element) => `${element.id || ""} ${classTokens(element).join(" ")} ${attrNames(element).join(" ")}`)
      .join(" ")
      .toLowerCase();
  }

  function countMatching(root, selector) {
    return (root.matches(selector) ? 1 : 0) + root.querySelectorAll(selector).length;
  }

  function interactionAriaMarkers(root) {
    const markers = [];
    if (root.querySelector("[aria-grabbed]")) markers.push("aria_grabbed");
    if (root.querySelector("[aria-dropeffect]")) markers.push("aria_dropeffect");
    if (root.querySelector("[aria-keyshortcuts]")) markers.push("aria_keyshortcuts");
    return markers;
  }

  function keyboardReorderMarkers(root, source, structure) {
    const markers = [];
    if (root.querySelector("[aria-grabbed], [aria-dropeffect], [aria-keyshortcuts]")) markers.push("aria_reorder_semantics");
    if (root.querySelector("[onkeydown], [onkeyup]")) markers.push("keyboard_handler");
    if (structure.focusable_custom_item_count > 0 && (structure.sortable_item_count > 0 || structure.term_bank_container_count > 0 || /reorder|sortable|drag|drop|matching/.test(source))) {
      markers.push("keyboard_focusable_reorder");
    }
    return markers;
  }

  function sortableMatchingCandidate(record) {
    const hasOptionRole = record.drag_drop_roles.some((role) => role.toLowerCase() === "option");
    return record.sortable_items > 0 && (
      record.term_bank_containers > 0 ||
      (record.sortable_containers > 0 && (hasOptionRole || record.focusable_custom_items > 0))
    );
  }

  function completionMarkers(root) {
    const markers = new Set();
    for (const element of [root, ...Array.from(root.querySelectorAll("*"))]) {
      for (const token of classTokens(element)) {
        if (/^(complete|completed|correct|success|finished|done)$/.test(token)) markers.add(token);
      }
      for (const name of ["data-complete", "data-completed", "data-correct", "aria-complete"]) {
        if (element.hasAttribute(name)) markers.add(name);
      }
    }
    return Array.from(markers).sort();
  }

  function controlState(root, selector) {
    const controls = Array.from(root.querySelectorAll(selector));
    return {
      count: controls.length,
      enabled: controls.filter((control) => !control.disabled && control.getAttribute("aria-disabled") !== "true").length,
    };
  }

  function classify(record) {
    if (record.challenge_markers.length) return { kind: "PROTECTED_CHALLENGE", protected: true, reason: record.challenge_markers[0] };
    if (record.lab_markers.length) return { kind: "PROTECTED_LAB", protected: true, reason: record.lab_markers[0] };
    if (record.sortable_matching_candidate) return { kind: "PROTECTED_SORTABLE_MATCHING", protected: true, reason: "paired_sortable_matching_structure" };
    if (
      record.native_draggable > 0 ||
      record.sortable_markers.length ||
      record.term_bank_markers.length ||
      record.drag_drop_roles.length ||
      record.pointer_markers.length ||
      record.interaction_aria_markers.length ||
      record.canvas > 0 ||
      record.keyboard_reorder_markers.length
    ) return { kind: "PROTECTED_DRAG_AND_DROP", protected: true, reason: "gesture_or_sortable_signal" };
    const controls = new Set(record.major_control_types);
    if (record.iframes > 0 || !record.participation_marker || !controls.size || Array.from(controls).some((type) => !SAFE_ACTIVITY_CONTROLS.has(type)) || !Array.from(controls).some((type) => PRIMARY_ACTIVITY_CONTROLS.has(type))) {
      return { kind: "UNKNOWN", protected: true, reason: "unsupported_or_insufficient_contract" };
    }
    return { kind: "KNOWN_SAFE_ACTIVITY", protected: false, reason: null };
  }

  function inspectActivity(root, index, section) {
    const classes = classTokens(root);
    const source = structuralSource(root);
    const challengeMarkers = source.match(/challenge|assessment|quiz/) ? ["challenge_marker"] : [];
    const labMarkers = source.match(/zylab|editor|grader|code-editor|codemirror|monaco/) ? ["lab_or_editor_marker"] : [];
    const sortableContainers = countMatching(root, ".zb-sortable-container, [class*='sortable-container']");
    const sortableItems = countMatching(root, ".zb-sortable-item, [class*='sortable-item'], .definition-match-term");
    const termBankContainers = countMatching(root, ".term-bank, [class*='term-bank'], [class*='termbank']");
    const focusableCustomItems = countMatching(root, "[role='option'][tabindex], .zb-sortable-item[tabindex], .definition-match-term[tabindex]");
    const nonNativeDraggable = countMatching(root, "[draggable='false'], [draggable=false]");
    const sortableMarkers = [];
    if (sortableContainers > 0) sortableMarkers.push("sortable_container");
    if (sortableItems > 0) sortableMarkers.push("sortable_item");
    if (/drop-target/.test(source)) sortableMarkers.push("drop_target_marker");
    if (/matching/.test(source)) sortableMarkers.push("matching_marker");
    const termBankMarkers = termBankContainers > 0 ? ["term_bank"] : [];
    const dragDropRoles = roles(root).filter((role) => /drag|drop|option/i.test(role));
    const pointerMarkers = [];
    if (root.querySelector("[draggable='true'], [draggable=true]")) pointerMarkers.push("native_draggable");
    if (root.querySelector("[style*='cursor: grab'], [onpointerdown], [ontouchstart], [onmousedown]")) pointerMarkers.push("pointer_handler");
    const ariaMarkers = interactionAriaMarkers(root);
    const keyboardMarkers = keyboardReorderMarkers(root, source, {
      sortable_item_count: sortableItems,
      term_bank_container_count: termBankContainers,
      focusable_custom_item_count: focusableCustomItems,
    });
    const check = controlState(root, "button.check-button");
    const submit = controlState(root, "button.submit-button, button[type='submit']");
    const controls = controlTypes(root);
    const participationMarker = !!(
      source.match(/participation|interactive-activity/) ||
      root.querySelector(".question-choices, .zb-radio-button, .zb-checkbox, button.start-button, button[aria-label='Play']")
    );
    const record = {
      activity_index: index + 1,
      activity_id: safeIdentity(root, `${section.key}.${index + 1}`),
      section: section.key,
      participation_marker: participationMarker,
      challenge_markers: challengeMarkers,
      lab_markers: labMarkers,
      major_control_types: controls,
      aria_roles: roles(root),
      iframes: root.querySelectorAll("iframe").length,
      native_draggable: root.querySelectorAll("[draggable='true'], [draggable=true]").length,
      sortable_markers: sortableMarkers,
      term_bank_markers: termBankMarkers,
      sortable_containers: sortableContainers,
      sortable_items: sortableItems,
      term_bank_containers: termBankContainers,
      focusable_custom_items: focusableCustomItems,
      non_native_draggable: nonNativeDraggable,
      drag_drop_roles: dragDropRoles,
      pointer_markers: pointerMarkers,
      interaction_aria_markers: ariaMarkers,
      keyboard_reorder_markers: keyboardMarkers,
      canvas: root.querySelectorAll("canvas").length,
      svg: root.querySelectorAll("svg").length,
      completion_markers: completionMarkers(root),
      check_controls: check,
      submit_controls: submit,
      visible: visible(root),
      fingerprint: structuralFingerprint(root),
    };
    record.sortable_matching_candidate = sortableMatchingCandidate(record);
    const classification = classify(record);
    const customSignals = [];
    if (record.sortable_containers > 0) customSignals.push("sortable_container");
    if (record.sortable_items > 0) customSignals.push("sortable_item");
    if (record.term_bank_containers > 0) customSignals.push("term_bank");
    if (record.focusable_custom_items > 0) customSignals.push("focusable_custom_item");
    if (record.non_native_draggable > 0 && (record.sortable_containers > 0 || record.sortable_items > 0 || record.term_bank_containers > 0 || record.drag_drop_roles.length > 0)) customSignals.push("non_native_draggable");
    if (record.native_draggable > 0) customSignals.push("native_draggable");
    for (const marker of record.sortable_markers) customSignals.push(`sortable:${marker}`);
    for (const marker of record.term_bank_markers) customSignals.push(`term_bank:${marker}`);
    for (const role of record.drag_drop_roles) customSignals.push(`role:${role}`);
    for (const marker of record.pointer_markers) customSignals.push(`pointer:${marker}`);
    for (const marker of record.interaction_aria_markers) customSignals.push(`aria:${marker}`);
    for (const marker of record.keyboard_reorder_markers) customSignals.push(`keyboard:${marker}`);
    if (record.canvas > 0) customSignals.push("canvas");
    if (record.svg > 0 && (record.pointer_markers.length || record.keyboard_reorder_markers.length)) customSignals.push("svg_gesture");
    const uniqueSignals = Array.from(new Set(customSignals));
    return {
      ...record,
      kind: classification.kind,
      protected: classification.protected,
      protected_reason: classification.reason,
      custom_interaction_candidate: uniqueSignals.length > 0,
      custom_interaction_signals: uniqueSignals,
    };
  }

  function createRuntime() {
    const state = {
      targetId: null,
      version: RUNTIME_VERSION,
      generation: 0,
      pageFingerprint: null,
      observer: null,
      inspect(options = {}) {
        const section = sectionIdentity();
        const currentPageFingerprint = pageFingerprint(section);
        if (state.targetId !== (options.targetId || null) || state.pageFingerprint !== currentPageFingerprint) {
          state.generation += 1;
          state.targetId = options.targetId || null;
          state.pageFingerprint = currentPageFingerprint;
        }
        const activities = Array.from(document.querySelectorAll(".interactive-activity-container"));
        return {
          target_id: state.targetId,
          generation: state.generation,
          page_fingerprint: state.pageFingerprint,
          path: location.pathname,
          title: document.title,
          section,
          section_heading: sectionHeading(),
          activity_count: activities.length,
          activities: activities.map((activity, index) => inspectActivity(activity, index, section)),
        };
      },
      dispose() {
        if (state.observer) state.observer.disconnect();
        state.observer = null;
        delete window[RUNTIME_KEY];
        return { disposed: true };
      },
    };
    state.observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => mutation.type === "childList" || mutation.type === "attributes")) {
        state.generation += 1;
        state.pageFingerprint = null;
      }
    });
    if (document.documentElement) {
      state.observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["class", "id", "data-activity-id", "data-activity"],
      });
    }
    return state;
  }

  if (window[RUNTIME_KEY] && window[RUNTIME_KEY].version !== RUNTIME_VERSION && typeof window[RUNTIME_KEY].dispose === "function") {
    window[RUNTIME_KEY].dispose();
  }
  if (!window[RUNTIME_KEY]) window[RUNTIME_KEY] = createRuntime();
  return window[RUNTIME_KEY];
})();
