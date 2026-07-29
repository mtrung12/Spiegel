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
  --success: #10B981;
  --danger: #DC2626;
  --warning: #F59E0B;

  /* Type */
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, -apple-system, sans-serif;

  /* Focus */
  --focus-ring: var(--accent);
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
