// Set the theme before anything is drawn, so the page never flashes the other one. A file of its own (not inline in
// the page) so the security policy can forbid inline scripts.
(function () {
  let t = "navy";
  try { t = localStorage.getItem("theme") || "navy"; } catch (e) { /* storage blocked: keep navy */ }
  if (t === "system") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "navy";
  document.documentElement.dataset.theme = ["dark", "navy", "light"].includes(t) ? t : "navy";
})();
