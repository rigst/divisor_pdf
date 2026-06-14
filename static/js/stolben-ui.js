/* =====================================================================
   Stölben UI — comportamentos opcionais (vanilla JS, sem dependências)
   Inicializa via data-attributes. Tudo é progressive enhancement:
   sem JS, o conteúdo continua acessível.
   ===================================================================== */
(function () {
  "use strict";
  document.documentElement.classList.add("js");

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Revelar ao rolar (.ds-reveal) ---- */
  function initReveal() {
    var els = document.querySelectorAll(".ds-reveal");
    if (reduce || !("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("is-visible"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-visible"); io.unobserve(e.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (el) { io.observe(el); });
  }

  /* ---- Cabeçalho destacado ao rolar + link da seção atual ---- */
  function initTopbar() {
    var bar = document.querySelector("[data-ds-topbar]");
    if (bar) {
      var onScroll = function () { bar.classList.toggle("is-scrolled", window.scrollY > 8); };
      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();
    }
    var spyLinks = document.querySelectorAll("[data-ds-spy] a[href^='#']");
    var sections = [];
    spyLinks.forEach(function (a) {
      var s = document.querySelector(a.getAttribute("href"));
      if (s) sections.push(s);
    });
    if (sections.length && "IntersectionObserver" in window) {
      var so = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          var id = "#" + e.target.id;
          spyLinks.forEach(function (a) { a.classList.toggle("is-active", a.getAttribute("href") === id); });
        });
      }, { rootMargin: "-45% 0px -50% 0px" });
      sections.forEach(function (s) { so.observe(s); });
    }
  }

  /* ---- Menu dropdown ([data-ds-menu]) ---- */
  function initMenus() {
    document.querySelectorAll("[data-ds-menu]").forEach(function (wrap) {
      var btn = wrap.querySelector("[data-ds-menu-trigger]");
      if (!btn) return;
      btn.setAttribute("aria-haspopup", "true");
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = wrap.classList.toggle("is-open");
        btn.setAttribute("aria-expanded", String(open));
      });
    });
    document.addEventListener("click", function () {
      document.querySelectorAll("[data-ds-menu].is-open").forEach(function (w) {
        w.classList.remove("is-open");
        var t = w.querySelector("[data-ds-menu-trigger]");
        if (t) t.setAttribute("aria-expanded", "false");
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") document.querySelectorAll("[data-ds-menu].is-open").forEach(function (w) { w.classList.remove("is-open"); });
    });
  }

  /* ---- Modais ([data-ds-open="id"], [data-ds-close]) ---- */
  function initModals() {
    var lastFocus = null;
    function open(id) {
      var ov = document.getElementById(id);
      if (!ov) return;
      lastFocus = document.activeElement;
      ov.classList.add("is-open");
      var f = ov.querySelector("input, button, textarea, select, a[href]");
      if (f) f.focus();
    }
    function close(ov) { ov.classList.remove("is-open"); if (lastFocus) lastFocus.focus(); }
    document.querySelectorAll("[data-ds-open]").forEach(function (b) {
      b.addEventListener("click", function () { open(b.getAttribute("data-ds-open")); });
    });
    document.querySelectorAll(".ds-overlay").forEach(function (ov) {
      ov.addEventListener("click", function (e) { if (e.target === ov) close(ov); });
      ov.querySelectorAll("[data-ds-close]").forEach(function (b) { b.addEventListener("click", function () { close(ov); }); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") document.querySelectorAll(".ds-overlay.is-open").forEach(close);
    });
  }

  /* ---- Tabs ([data-ds-tabs]) ---- */
  function initTabs() {
    document.querySelectorAll("[data-ds-tabs]").forEach(function (group) {
      var tabs = group.querySelectorAll(".ds-tab");
      tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
          tabs.forEach(function (t) { t.classList.remove("is-active"); t.setAttribute("aria-selected", "false"); });
          tab.classList.add("is-active");
          tab.setAttribute("aria-selected", "true");
          var target = tab.getAttribute("data-ds-tab");
          group.querySelectorAll(".ds-tabpanel").forEach(function (p) { p.hidden = p.getAttribute("data-ds-panel") !== target; });
        });
      });
    });
  }

  /* ---- Tabela: marcar todas ([data-ds-check-all]) ---- */
  function initTableSelect() {
    document.querySelectorAll("[data-ds-check-all]").forEach(function (master) {
      var table = master.closest("table");
      if (!table) return;
      var boxes = table.querySelectorAll("tbody [data-ds-row-check]");
      master.addEventListener("change", function () {
        boxes.forEach(function (b) {
          b.checked = master.checked;
          b.closest("tr").classList.toggle("is-selected", b.checked);
        });
      });
      boxes.forEach(function (b) {
        b.addEventListener("change", function () {
          b.closest("tr").classList.toggle("is-selected", b.checked);
          master.checked = Array.prototype.every.call(boxes, function (x) { return x.checked; });
        });
      });
    });
  }

  /* ---- Toasts: window.dsToast(msg, tipo) e [data-ds-toast] ---- */
  function ensureToastHost() {
    var host = document.querySelector(".ds-toasts");
    if (!host) { host = document.createElement("div"); host.className = "ds-toasts"; host.setAttribute("aria-live", "polite"); document.body.appendChild(host); }
    return host;
  }
  var ICONS = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    danger: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
  };
  window.dsToast = function (msg, type) {
    type = type || "info";
    var host = ensureToastHost();
    var el = document.createElement("div");
    el.className = "ds-toast ds-toast--" + type;
    el.setAttribute("role", "status");
    el.innerHTML = (ICONS[type] || ICONS.info) + "<span>" + msg + "</span>";
    host.appendChild(el);
    setTimeout(function () { el.classList.add("is-leaving"); setTimeout(function () { el.remove(); }, 240); }, 3200);
  };
  function initToastTriggers() {
    document.querySelectorAll("[data-ds-toast]").forEach(function (b) {
      b.addEventListener("click", function () { window.dsToast(b.getAttribute("data-ds-toast"), b.getAttribute("data-ds-toast-type") || "info"); });
    });
  }

  /* ---- Segmented control ([data-ds-segment]) + troca de visualização ----
     Se o botão tiver data-view="x", mostra o [data-view-panel="x"] dentro do
     container [data-ds-views] mais próximo (alterna tabela/cards/lista/board). */
  function initSegments() {
    document.querySelectorAll("[data-ds-segment]").forEach(function (seg) {
      var btns = seg.querySelectorAll("button");
      btns.forEach(function (b) {
        b.addEventListener("click", function () {
          btns.forEach(function (x) { x.classList.remove("is-active"); x.setAttribute("aria-pressed", "false"); });
          b.classList.add("is-active"); b.setAttribute("aria-pressed", "true");
          var view = b.getAttribute("data-view");
          if (view) {
            var scope = seg.closest("[data-ds-views]");
            if (!scope || !scope.querySelector("[data-view-panel]")) scope = seg.closest("main") || document;
            scope.querySelectorAll("[data-view-panel]").forEach(function (p) {
              p.hidden = p.getAttribute("data-view-panel") !== view;
            });
          }
        });
      });
    });
  }

  /* ---- Accordion ([data-ds-accordion]) ---- */
  function initAccordions() {
    document.querySelectorAll("[data-ds-accordion]").forEach(function (acc) {
      acc.querySelectorAll(".ds-accordion-trigger").forEach(function (trig) {
        var item = trig.closest(".ds-accordion-item");
        var panel = item.querySelector(".ds-accordion-panel");
        trig.setAttribute("aria-expanded", String(item.classList.contains("is-open")));
        trig.addEventListener("click", function () {
          var open = item.classList.toggle("is-open");
          trig.setAttribute("aria-expanded", String(open));
          if (panel) panel.hidden = !open;
        });
      });
    });
  }

  /* ---- Dropzone ([data-ds-dropzone]) ---- */
  function initDropzones() {
    document.querySelectorAll("[data-ds-dropzone]").forEach(function (dz) {
      ["dragenter", "dragover"].forEach(function (ev) {
        dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("is-drag"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("is-drag"); });
      });
    });
  }

  /* ---- Seletor de tema de acento ([data-ds-theme-select]) ----
     Troca a classe .ds-theme-* na raiz ao vivo. value="" volta ao azul. */
  var DS_THEMES = ["ds-theme-indigo", "ds-theme-violet", "ds-theme-teal", "ds-theme-emerald", "ds-theme-amber", "ds-theme-rose", "ds-theme-slate"];
  function initThemeSelect() {
    document.querySelectorAll("[data-ds-theme-select]").forEach(function (sel) {
      sel.addEventListener("change", function () {
        var root = document.documentElement; /* mesmo elemento do .ds-dark, p/ derivar tons certos */
        DS_THEMES.forEach(function (t) { root.classList.remove(t); });
        if (sel.value) root.classList.add(sel.value);
      });
    });
  }

  /* ---- Modo claro / escuro / automático ----
     Modos: "light", "dark", "auto" (segue o sistema; é o padrão).
     Controle 3-vias: [data-ds-theme-mode] com botões [data-mode="light|dark|auto"].
     Variante legada: [data-ds-dark-toggle] (alterna claro/escuro).
     A leitura inicial deve ser feita por um script inline no <head> (evita flash). */
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  function getMode() { try { return localStorage.getItem("ds-theme-mode") || "auto"; } catch (e) { return "auto"; } }
  function applyMode() {
    var m = getMode();
    var dark = m === "dark" || (m === "auto" && mq.matches);
    document.documentElement.classList.toggle("ds-dark", dark);
  }
  function setMode(m) {
    try { if (m === "auto") localStorage.removeItem("ds-theme-mode"); else localStorage.setItem("ds-theme-mode", m); } catch (e) {}
    applyMode(); syncModeControls();
  }
  function syncModeControls() {
    var m = getMode();
    document.querySelectorAll("[data-ds-theme-mode] [data-mode]").forEach(function (btn) {
      var on = btn.getAttribute("data-mode") === m;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
    var dark = document.documentElement.classList.contains("ds-dark");
    document.querySelectorAll("[data-ds-dark-toggle]").forEach(function (b) { b.setAttribute("aria-pressed", String(dark)); });
  }
  function initThemeMode() {
    document.querySelectorAll("[data-ds-theme-mode] [data-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () { setMode(btn.getAttribute("data-mode")); });
    });
    document.querySelectorAll("[data-ds-dark-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () { setMode(document.documentElement.classList.contains("ds-dark") ? "light" : "dark"); });
    });
    var onSys = function () { if (getMode() === "auto") applyMode(); };
    if (mq.addEventListener) mq.addEventListener("change", onSys); else if (mq.addListener) mq.addListener(onSys);
    applyMode(); syncModeControls();
  }

  /* ---- Navegação mobile / gaveta ([data-ds-nav-toggle]) ----
     Abre/fecha a sidebar como gaveta no mobile. Fecha ao clicar no backdrop,
     em um link da sidebar, ou com Esc. */
  function initMobileNav() {
    document.querySelectorAll("[data-ds-nav-toggle]").forEach(function (btn) {
      var shell = btn.closest(".ds-shell") || document.querySelector(".ds-shell");
      if (!shell) return;
      function set(open) { shell.classList.toggle("is-nav-open", open); btn.setAttribute("aria-expanded", String(open)); }
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", function () { set(!shell.classList.contains("is-nav-open")); });
      var bd = shell.querySelector(".ds-nav-backdrop");
      if (bd) bd.addEventListener("click", function () { set(false); });
      shell.querySelectorAll(".ds-sidebar a").forEach(function (a) { a.addEventListener("click", function () { set(false); }); });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
    });
  }

  function init() {
    initReveal(); initTopbar(); initMenus(); initModals();
    initTabs(); initTableSelect(); initToastTriggers();
    initSegments(); initAccordions(); initDropzones(); initThemeSelect(); initThemeMode(); initMobileNav();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
