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
