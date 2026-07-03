<script lang="ts">
import { page } from '$app/stores';
import Navbar from '$lib/components/Navbar.svelte';
import '../app.css';

const { children } = $props();

const drawerId = 'app-drawer';

type Theme = 'dark' | 'light';
// ssr is disabled for this app, so `document` is always available here —
// read the theme the inline bootstrap script (app.html) already applied,
// so the toggle button's icon matches on first render (no flash).
let theme: Theme = $state(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');

function toggleTheme() {
  theme = theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('remuxcode-theme', theme);
}

const pageTitle = $derived.by(() => {
  const path = $page.url.pathname;
  if (path === '/') return 'Dashboard';
  if (path.startsWith('/movies')) return 'Movies';
  if (path.startsWith('/shows')) return 'Shows';
  if (path.startsWith('/jobs')) return 'Jobs';
  if (path.startsWith('/config')) return 'Configuration';
  return '';
});
</script>

<div class="drawer lg:drawer-open min-h-screen">
  <input id={drawerId} type="checkbox" class="drawer-toggle" />

  <!-- Main content -->
  <div class="drawer-content flex flex-col bg-base-100">
    <!-- Top bar -->
    <header class="sticky top-0 z-30 flex items-center justify-between h-14 px-4 lg:px-6 border-b border-base-content/5 bg-base-100/80 backdrop-blur-md">
      <div class="flex items-center gap-3">
        <!-- Mobile hamburger -->
        <label for={drawerId} class="btn btn-ghost btn-sm btn-square lg:hidden" aria-label="Open menu">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </label>
        <!-- Mobile brand -->
        <a href="/" class="text-lg font-bold text-primary tracking-tight lg:hidden">
          remu<span class="text-secondary">X</span>code
        </a>
        <!-- Page title (desktop) -->
        <h1 class="hidden lg:block text-sm font-medium text-base-content/60">{pageTitle}</h1>
      </div>

      <!-- Right side placeholder for future profile/actions -->
      <div class="flex items-center gap-2">
        <button
          class="btn btn-ghost btn-sm btn-square"
          onclick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label="Toggle theme"
        >
          {#if theme === 'dark'}
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" />
            </svg>
          {:else}
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
            </svg>
          {/if}
        </button>
        <div class="w-8 h-8 rounded-full bg-base-200 border border-base-content/10 flex items-center justify-center">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
          </svg>
        </div>
      </div>
    </header>

    <main class="flex-1 p-4 lg:p-6 max-w-6xl w-full mx-auto">
      {@render children()}
    </main>
  </div>

  <!-- Sidebar -->
  <div class="drawer-side z-40">
    <label for={drawerId} class="drawer-overlay" aria-label="Close menu"></label>
    <Navbar drawerCheckbox={drawerId} />
  </div>
</div>
