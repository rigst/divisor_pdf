/* =================================================================
   Stölben UI — comportamentos opcionais
   Inclua no fim do <body>:  <script src="stolben-ui.js"></script>
   ================================================================= */
(function () {
  "use strict";

  /* Revela elementos .ds-reveal quando entram na viewport. */
  function setupReveal() {
    var els = document.querySelectorAll(".ds-reveal");
    if (!els.length || !("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("is-visible"); });
      return;
    }
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          obs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.14 });
    els.forEach(function (el) { obs.observe(el); });
  }

  /* Alterna .is-scrolled na .ds-nav após rolar (muda o estilo da pílula). */
  function setupNav() {
    var nav = document.querySelector(".ds-nav");
    if (!nav) return;
    var onScroll = function () { nav.classList.toggle("is-scrolled", window.scrollY > 12); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* Destaca o link da seção visível (.ds-nav-links a[href^="#"]). */
  function setupActiveSection() {
    var links = document.querySelectorAll('.ds-nav-links a[href^="#"]');
    var sections = document.querySelectorAll("section[id]");
    if (!links.length || !sections.length || !("IntersectionObserver" in window)) return;
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var link = document.querySelector('.ds-nav-links a[href="#' + entry.target.id + '"]');
        links.forEach(function (l) { l.classList.remove("is-active"); });
        if (link) link.classList.add("is-active");
      });
    }, { rootMargin: "-40% 0px -45% 0px", threshold: 0.01 });
    sections.forEach(function (s) { obs.observe(s); });
  }

  function init() { setupReveal(); setupNav(); setupActiveSection(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
