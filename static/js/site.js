// Header shrink-on-scroll, ported from maker-cards views/partials/header.hbs.
// The original needed jQuery for this; it does not.
(function () {
   var SHRINK_AT = 20;
   function update() {
      var shrunk = window.scrollY >= SHRINK_AT;
      document.querySelectorAll(".shrinkable").forEach(function (el) {
         el.classList.toggle("shrink", shrunk);
      });
   }
   window.addEventListener("scroll", update, { passive: true });
   update();
})();

// Forms marked data-autosubmit submit as soon as a control changes, so a
// filter checkbox takes effect without a button. The noscript fallback in the
// template covers browsers without script.
document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
   form.addEventListener("change", function () { form.submit(); });
});
