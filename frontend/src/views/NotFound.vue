<template>
  <div class="notfound-view">
    <nav class="navbar">
      <button type="button" class="nav-brand" @click="router.push('/')">
        <span class="sr-only">{{ $t('a11y.backToProjectList') }}</span>
        <span aria-hidden="true">SPIEGEL</span>
      </button>
      <LanguageSwitcher />
    </nav>

    <main id="main-content" class="body">
      <span class="code" aria-hidden="true">{{ $t('notFound.code') }}</span>
      <h1 class="title">{{ $t('notFound.title') }}</h1>
      <p class="text">{{ $t('notFound.body') }}</p>
      <button type="button" class="cta" @click="router.push('/')">
        <span class="arrow" aria-hidden="true">←</span>
        {{ $t('notFound.home') }}
      </button>
    </main>
  </div>
</template>

<script setup>
// A mistyped or stale URL used to render an empty white page: no chrome, no
// wordmark, no way out but the browser's own back button.
import { useRouter } from 'vue-router'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'

const router = useRouter()
</script>

<style scoped>
.notfound-view {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--ink);
}

.navbar {
  height: 72px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
  max-width: 1200px;
  margin: 0 auto;
  border-bottom: 1px solid var(--border);
}

.nav-brand {
  background: none;
  border: none;
  padding: 4px;
  color: inherit;
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 0.85rem;
}

.body {
  max-width: 1200px;
  margin: 0 auto;
  padding: 120px 40px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 16px;
}

.code {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  letter-spacing: 1.5px;
  color: var(--accent);
}

.title {
  font-size: clamp(1.8rem, 3.4vw, 2.6rem);
  line-height: 1.1;
  letter-spacing: -1px;
  font-weight: 500;
  margin: 0;
}

.text {
  font-size: 1.02rem;
  line-height: 1.55;
  color: var(--muted);
  max-width: 520px;
  margin: 0;
}

.cta {
  margin-top: 16px;
  background: var(--accent);
  color: var(--white);
  border: none;
  padding: 15px 28px;
  font-family: var(--font-sans);
  font-size: 1rem;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  transition: background 0.2s ease, transform 0.2s ease;
}

.cta:hover {
  background: var(--accent-strong);
  transform: translateY(-2px);
}

.arrow {
  font-family: var(--font-mono);
}

@media (max-width: 720px) {
  .navbar {
    padding: 0 24px;
  }

  .body {
    padding: 72px 24px;
  }
}
</style>
