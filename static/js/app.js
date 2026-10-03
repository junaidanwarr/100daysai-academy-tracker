/*
 * Shell behaviour. Plain JS, no build step, and nothing here is required for a
 * page to work: without it the sidebar is simply always shown on wide screens
 * and forms submit as normal.
 */
(function () {
  "use strict";

  // --- Navigation drawer (tablet and phone) ---------------------------------
  var shell = document.querySelector("[data-shell]");
  var toggle = document.querySelector("[data-nav-toggle]");
  var sidebar = document.getElementById("sidebar");

  function setOpen(open) {
    if (!shell || !toggle) return;
    shell.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    document.body.style.overflow = open ? "hidden" : "";
    if (open) {
      var current = sidebar.querySelector("[aria-current='page']") || sidebar.querySelector("a, button");
      if (current) current.focus();
    } else {
      toggle.focus({ preventScroll: true });
    }
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      setOpen(!shell.classList.contains("nav-open"));
    });
    document.querySelectorAll("[data-nav-close]").forEach(function (el) {
      el.addEventListener("click", function () { setOpen(false); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && shell.classList.contains("nav-open")) setOpen(false);
    });
    // Leaving the drawer layout (rotating a tablet, resizing) closes it.
    window.matchMedia("(min-width: 1025px)").addEventListener("change", function (e) {
      if (e.matches && shell.classList.contains("nav-open")) setOpen(false);
    });
  }

  // --- Submit feedback ------------------------------------------------------
  // A POST that takes a moment (a sync, a scan, a review) shows that it is
  // working and cannot be clicked twice. GET forms (filters) are left alone.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || (form.method || "").toLowerCase() !== "post") return;
    var button = e.submitter || form.querySelector("button[type='submit'], button:not([type])");
    if (!button || button.hasAttribute("data-no-busy")) return;
    // Defer so the button's own name/value is still sent with the form.
    setTimeout(function () {
      button.classList.add("is-busy");
      button.setAttribute("aria-busy", "true");
    }, 0);
  });

  // Coming back via the browser's back button restores a page from cache with
  // its buttons still marked busy; clear them.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll(".is-busy").forEach(function (el) {
      el.classList.remove("is-busy");
      el.removeAttribute("aria-busy");
    });
  });
})();

/* Page tabs: <div data-tabs> holding [role=tab][data-tab] buttons, with panels
   [data-panel] anywhere on the page. The open tab follows the URL hash, so a
   link such as /research/#channel-research opens that tab directly. */
(function () {
  "use strict";
  document.querySelectorAll("[data-tabs]").forEach(function (bar) {
    var tabs = bar.querySelectorAll("[role='tab'][data-tab]");
    if (!tabs.length) return;
    function panel(name) { return document.querySelector("[data-panel='" + name + "']"); }
    function show(name, focus) {
      tabs.forEach(function (t) {
        var on = t.dataset.tab === name;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var p = panel(t.dataset.tab);
        if (p) p.hidden = !on;
        if (on && focus) t.focus();
      });
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () {
        show(t.dataset.tab);
        history.replaceState(null, "", i === 0 ? location.pathname + location.search : "#" + t.dataset.tab);
      });
      t.addEventListener("keydown", function (e) {
        var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        var next = tabs[(i + step + tabs.length) % tabs.length];
        show(next.dataset.tab, true);
        next.click();
      });
    });
    var wanted = location.hash.slice(1);
    var match = Array.prototype.some.call(tabs, function (t) { return t.dataset.tab === wanted; });
    show(match ? wanted : tabs[0].dataset.tab);
  });
})();
