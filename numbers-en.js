/* AL-ZOOL locale rule: English digits everywhere; Arabic labels and services stay Arabic. */
(function () {
  "use strict";
  const digitMap = {"٠":"0","١":"1","٢":"2","٣":"3","٤":"4","٥":"5","٦":"6","٧":"7","٨":"8","٩":"9",
                    "۰":"0","۱":"1","۲":"2","۳":"3","۴":"4","۵":"5","۶":"6","۷":"7","۸":"8","۹":"9"};
  const convert = value => String(value ?? "").replace(/[٠-٩۰-۹]/g, ch => digitMap[ch]);
  const textAttrs = ["placeholder", "title", "aria-label", "alt"];
  function normalizeText(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const normalized = convert(node.nodeValue);
      if (normalized !== node.nodeValue) node.nodeValue = normalized;
    }
  }
  function normalizeInputs(root) {
    root.querySelectorAll?.("input:not([type=password]):not([type=email]), textarea").forEach(el => {
      if (el.value) {
        const normalized = convert(el.value);
        if (normalized !== el.value) {
          const start = el.selectionStart, end = el.selectionEnd;
          el.value = normalized;
          try { if (start !== null && end !== null) el.setSelectionRange(start, end); } catch (_) {}
        }
      }
      if (el.type === "number") {
        el.lang = "en";
        el.dir = "ltr";
        el.style.fontVariantNumeric = "tabular-nums";
      }
    });
  }
  function normalizeAttributes(root) {
    if (root.nodeType === Node.ELEMENT_NODE) {
      for (const name of textAttrs) {
        const value = root.getAttribute(name);
        if (value !== null && convert(value) !== value) root.setAttribute(name, convert(value));
      }
    }
    root.querySelectorAll?.("[placeholder],[title],[aria-label],[alt]").forEach(el => {
      for (const name of textAttrs) {
        const value = el.getAttribute(name);
        if (value !== null && convert(value) !== value) el.setAttribute(name, convert(value));
      }
    });
  }
  function apply(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      const value = convert(root.nodeValue);
      if (value !== root.nodeValue) root.nodeValue = value;
      return;
    }
    normalizeText(root);
    normalizeInputs(root);
    normalizeAttributes(root);
  }
  apply(document.body);
  document.addEventListener("input", event => {
    const el = event.target;
    if ((el instanceof HTMLInputElement && el.type !== "password" && el.type !== "email") ||
        el instanceof HTMLTextAreaElement) {
      const normalized = convert(el.value);
      if (normalized !== el.value) {
        const start = el.selectionStart, end = el.selectionEnd;
        el.value = normalized;
        try { if (start !== null && end !== null) el.setSelectionRange(start, end); } catch (_) {}
      }
    }
  }, true);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "characterData") apply(record.target);
      if (record.type === "attributes") {
        const value = record.target.getAttribute(record.attributeName);
        if (value !== null && convert(value) !== value) record.target.setAttribute(record.attributeName, convert(value));
      }
      for (const node of record.addedNodes || []) apply(node);
    }
  }).observe(document.body, {
    subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ["placeholder", "title", "aria-label", "alt"]
  });
})();