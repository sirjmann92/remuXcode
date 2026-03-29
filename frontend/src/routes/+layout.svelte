<script lang="ts">
import { page } from '$app/stores';
import Navbar from '$lib/components/Navbar.svelte';
import '../app.css';

const { children } = $props();

const drawerId = 'app-drawer';

const pageTitle = $derived.by(() => {
  const path = $page.url.pathname;
  if (path === '/') return 'Dashboard';
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
