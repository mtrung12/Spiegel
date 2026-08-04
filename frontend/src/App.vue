<template>
  <a class="skip-link" href="#main-content">{{ $t('a11y.skipToContent') }}</a>
  <router-view />
</template>

<script setup>
// Pages are managed by Vue Router
</script>

<style>
/* Global style reset */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

/* ---------------------------------------------------------------------------
   Design tokens. Every colour, font and breakpoint in the app resolves here,
   so a palette change is one edit rather than a thousand.
   Text greys are chosen to clear WCAG AA (4.5:1) against a white background:
   --muted #666666 = 5.74:1, --muted-soft #767676 = 4.54:1.
   --------------------------------------------------------------------------- */
:root {
  /* Neutrals */
  --white: #FFFFFF;
  --black: #000000;
  --ink: #111111;
  --ink-2: #333333;
  --ink-3: #4D4D4D;
  --muted: #666666;
  --muted-soft: #767676;

  /* Surfaces */
  --surface: #FAFAFA;
  --surface-2: #F5F5F5;
  --surface-3: #F0F0F0;

  /* Borders */
  --border: #E5E5E5;
  --border-soft: #EAEAEA;
  --border-strong: #D4D4D4;

  /* Brand and status */
  --accent: #F97316;
  --accent-strong: #EA580C;
  --accent-soft: rgba(249, 115, 22, 0.10);
  --success: #10B981;
  --success-soft: rgba(16, 185, 129, 0.10);
  --danger: #DC2626;
  --warning: #F59E0B;
  --warning-soft: rgba(245, 158, 11, 0.12);

  /* Type */
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, -apple-system, sans-serif;

  /* Type ramp. Sizes were a mix of px and rem picked per component; these are
     the only steps the shared surfaces are allowed to use. */
  --text-xs: 0.75rem;
  --text-sm: 0.8125rem;
  --text-base: 0.9375rem;
  --text-md: 1rem;
  --text-lg: 1.125rem;

  /* Spacing scale, 4px base. Replaces the ad-hoc 14/24/35/56/96 paddings that
     made two adjacent sections look accidentally misaligned. */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --space-7: 48px;
  --space-8: 64px;
  --space-9: 96px;

  /* Radii. The same page carried 0, 2, 3, 4, 6 and 8px corners. */
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 10px;

  /* Elevation, tinted with the ink colour rather than pure black so shadows
     read as depth instead of grey haze. */
  --shadow-1: 0 1px 2px rgba(17, 17, 17, 0.05);
  --shadow-2: 0 1px 2px rgba(17, 17, 17, 0.04), 0 4px 12px rgba(17, 17, 17, 0.06);
  --shadow-3: 0 2px 6px rgba(17, 17, 17, 0.05), 0 12px 32px rgba(17, 17, 17, 0.10);

  /* Motion */
  --dur-fast: 120ms;
  --dur: 180ms;
  --ease: cubic-bezier(0.2, 0, 0, 1);

  /* Page rhythm: one gutter and one measure for every top-level section, so
     the header rule, the title and the cards below it share an edge. */
  --page-max: 1200px;
  --page-pad: 40px;

  /* Focus */
  --focus-ring: var(--accent);
}

/* The gutter narrows with the viewport once, here, rather than in a per-page
   media query that each surface got slightly wrong. */
@media (max-width: 720px) {
  :root {
    --page-pad: 20px;
  }
}

#app {
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--ink);
  background-color: var(--white);
}

/* ---------------------------------------------------------------------------
   Focus. Without a visible ring the app is unusable by keyboard, so this is a
   floor every interactive element inherits; components may restyle but the
   outline stays.
   --------------------------------------------------------------------------- */
:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
  border-radius: 2px;
}

/* Suppress the ring for pointer users only, never for keyboard users. */
:focus:not(:focus-visible) {
  outline: none;
}

/* Skip link: first tab stop, visible only when focused. */
.skip-link {
  position: absolute;
  left: -9999px;
  top: 0;
  z-index: 1000;
  padding: 12px 20px;
  background: var(--ink);
  color: var(--white);
  font-family: var(--font-sans);
  font-size: 14px;
  text-decoration: none;
}

.skip-link:focus {
  left: 8px;
  top: 8px;
}

/* Scrollbar styling */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--surface-2);
}

::-webkit-scrollbar-thumb {
  background: var(--black);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--ink-2);
}

/* Global button styling */
button {
  font-family: inherit;
}

/* A button that only carries an icon or arrow still needs a hit target. */
button:not(:disabled) {
  cursor: pointer;
}

/* ---------------------------------------------------------------------------
   Reduced motion. The app runs a dozen looping spin/pulse/ripple animations;
   for users who ask for less motion they are a vestibular hazard, not polish.
   --------------------------------------------------------------------------- */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

/* ---------------------------------------------------------------------------
   The split-pane shell used by every step view. Lived as an identical copy in
   five files; the widths are driven by [data-mode] rather than inline styles so
   the responsive rules below can actually take effect.
   --------------------------------------------------------------------------- */
.main-view {
  min-height: 100vh;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--white);
  overflow: hidden;
  font-family: var(--font-sans);
}

.content-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
  min-height: 0;
}

.panel-wrapper {
  height: 100%;
  overflow: hidden;
  transition: width 0.4s cubic-bezier(0.25, 0.8, 0.25, 1), opacity 0.3s ease, transform 0.3s ease;
  will-change: width, opacity, transform;
}

.panel-wrapper.left {
  border-right: 1px solid var(--border-soft);
}

.content-area[data-mode="split"] .panel-wrapper { width: 50%; opacity: 1; transform: none; }

.content-area[data-mode="graph"] .panel-wrapper.left { width: 100%; opacity: 1; transform: none; }
.content-area[data-mode="graph"] .panel-wrapper.right { width: 0; opacity: 0; transform: translateX(20px); }

.content-area[data-mode="workbench"] .panel-wrapper.left { width: 0; opacity: 0; transform: translateX(-20px); }
.content-area[data-mode="workbench"] .panel-wrapper.right { width: 100%; opacity: 1; transform: none; }

/* Below this the two panes cannot both be useful side by side, so the shell
   scrolls as one column instead of squeezing each pane past its content. */
@media (max-width: 1024px) {
  .main-view {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }

  .content-area {
    flex-direction: column;
    overflow: visible;
  }

  .panel-wrapper {
    height: auto;
    transition: none;
  }

  .panel-wrapper.left {
    border-right: none;
    border-bottom: 1px solid var(--border-soft);
  }

  .content-area[data-mode] .panel-wrapper { width: 100%; opacity: 1; transform: none; }

  /* A hidden pane still collapses, it just does so vertically now. */
  .content-area[data-mode="graph"] .panel-wrapper.right,
  .content-area[data-mode="workbench"] .panel-wrapper.left {
    display: none;
  }

  /* Side by side is meaningless at this width; the graph gets a fixed slice. */
  .content-area[data-mode="split"] .panel-wrapper.left {
    height: 60vh;
  }
}

/* ---------------------------------------------------------------------------
   Motion layer. One place for the app's animated primitives, so a surface that
   wants life borrows a class instead of inventing a fourth spinner. Everything
   here is decoration: no rule below changes layout flow, hit targets or state,
   and the reduced-motion block above switches all of it off.

   The budget is deliberate - one slow ambient pattern per page, one accent on
   the element under the cursor, one loop per thing that is genuinely working.
   Anything more and a tool starts reading as a screensaver.
   --------------------------------------------------------------------------- */

/* Slow parallax drift for the page-wide grid. One full cell per cycle, so the
   pattern is seamless and never appears to jump back. */
@keyframes fx-drift {
  from { background-position: 0 0, 0 0; }
  to { background-position: 56px 56px, 56px 56px; }
}

/* Two accent glows breathing out of phase, which is what stops the backdrop
   from looking like a static gradient. */
@keyframes fx-glow {
  0%, 100% { transform: translate3d(0, 0, 0) scale(1); opacity: 0.55; }
  50% { transform: translate3d(-3%, 4%, 0) scale(1.12); opacity: 0.85; }
}

/* Entrance. Small travel: content that flies in from off-screen makes a page
   feel slow, because the user waits for it to arrive. */
@keyframes fx-rise {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}

/* Highlight sweeping across a control - the hover cue for primary actions. */
@keyframes fx-sheen {
  from { transform: translateX(-120%); }
  to { transform: translateX(220%); }
}

/* Placeholder / working shimmer for text and chips. */
@keyframes fx-shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

/* Marching ants for a dashed drop target that has become live. */
@keyframes fx-march {
  to { background-position: 24px 0, -24px 0, 0 -24px, 0 24px; }
}

/* Expanding ring behind a live status dot. */
@keyframes fx-ring {
  0% { transform: scale(1); opacity: 0.55; }
  70%, 100% { transform: scale(2.6); opacity: 0; }
}

/* Travelling band for bars and rules that represent work in flight. */
@keyframes fx-flow {
  from { background-position: 0 0; }
  to { background-position: 200% 0; }
}

/* ---------- Ambient page backdrop ----------
   A drifting hairline grid with two slow accent blooms. Both layers are pinned
   to the viewport and sit under the page's own content, so scrolling does not
   drag them around and nothing can be clicked through by accident. Opacity is
   deliberately near the threshold of visibility: on a white working surface it
   reads as depth, not as pattern. */
.fx-ambient {
  position: relative;
  isolation: isolate;
}

.fx-ambient::before,
.fx-ambient::after {
  content: '';
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}

.fx-ambient::before {
  background-image:
    linear-gradient(to right, rgba(17, 17, 17, 0.028) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(17, 17, 17, 0.028) 1px, transparent 1px);
  background-size: 56px 56px, 56px 56px;
  /* Fades out towards the bottom so the grid never competes with content. */
  -webkit-mask-image: radial-gradient(120% 90% at 50% 0%, #000 35%, transparent 100%);
  mask-image: radial-gradient(120% 90% at 50% 0%, #000 35%, transparent 100%);
  animation: fx-drift 32s linear infinite;
}

.fx-ambient::after {
  background:
    radial-gradient(40vw 40vw at 12% 8%, rgba(249, 115, 22, 0.07), transparent 65%),
    radial-gradient(46vw 46vw at 88% 22%, rgba(16, 185, 129, 0.055), transparent 65%);
  animation: fx-glow 22s ease-in-out infinite;
}

/* The page's own content rides above both layers. */
.fx-ambient > * {
  position: relative;
  z-index: 1;
}

/* ---------- Entrance ----------
   Used on the handful of blocks that make up a page's first impression. The
   fill mode matters: without `both` the element flashes at full opacity for a
   frame before its delay elapses. */
.fx-rise {
  animation: fx-rise 420ms var(--ease) both;
}

/* Stagger, as explicit steps rather than an nth-child loop, so a caller can
   see exactly how long the sequence takes to finish (7 x 60ms). */
.fx-delay-1 { animation-delay: 60ms; }
.fx-delay-2 { animation-delay: 120ms; }
.fx-delay-3 { animation-delay: 180ms; }
.fx-delay-4 { animation-delay: 240ms; }
.fx-delay-5 { animation-delay: 300ms; }
.fx-delay-6 { animation-delay: 360ms; }
.fx-delay-7 { animation-delay: 420ms; }

/* ---------- Sheen ----------
   For filled buttons. The sweep is clipped to the control and only runs on
   hover, so a page of resting buttons is still. */
.fx-sheen {
  position: relative;
  overflow: hidden;
}

.fx-sheen::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 45%;
  pointer-events: none;
  background: linear-gradient(
    100deg,
    transparent,
    rgba(255, 255, 255, 0.32),
    transparent
  );
  transform: translateX(-120%);
}

.fx-sheen:hover:not(:disabled)::after,
.fx-sheen:focus-visible:not(:disabled)::after {
  animation: fx-sheen 900ms var(--ease);
}

/* ---------- Live status dot ----------
   The ring is drawn by the dot's own ::after, so any 8px dot can opt in
   without extra markup. */
.fx-live {
  position: relative;
}

.fx-live::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: inherit;
  pointer-events: none;
  animation: fx-ring 1.8s var(--ease) infinite;
}

/* ---------- Working shimmer ----------
   A band travelling through the element's own colour, for chips that say
   something is running. Keeps the text legible, unlike a pulsing opacity. */
.fx-working {
  background-image: linear-gradient(
    100deg,
    transparent 20%,
    rgba(255, 255, 255, 0.28) 42%,
    rgba(255, 255, 255, 0.28) 58%,
    transparent 80%
  );
  background-size: 200% 100%;
  background-repeat: no-repeat;
  animation: fx-shimmer 2.2s linear infinite;
}

/* Screen-reader-only text that still needs to reach assistive tech. */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
