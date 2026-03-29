<script lang="ts">
  import { page } from '$app/stores';
  import { getHealth } from '$lib/api';
  import type { HealthStatus } from '$lib/types';

  let { drawerCheckbox }: { drawerCheckbox?: string } = $props();

  let health: HealthStatus | null = $state(null);
  let healthError = $state(false);

  async function checkHealth() {
    try {
      health = await getHealth();
      healthError = false;
    } catch {
      healthError = true;
    }
  }

  $effect(() => {
    checkHealth();
    const id = setInterval(checkHealth, 15000);
    return () => clearInterval(id);
  });

  const nav = [
    { href: '/', label: 'Dashboard', icon: 'M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25a2.25 2.25 0 0 1-2.25-2.25v-2.25Z' },
    { href: '/jobs', label: 'Jobs', icon: 'M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 0 1 0 3.75H5.625a1.875 1.875 0 0 1 0-3.75Z' },
    { href: '/config', label: 'Config', icon: 'M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z' },
  ];

  function closeMobileDrawer() {
    if (drawerCheckbox) {
      const el = document.getElementById(drawerCheckbox) as HTMLInputElement | null;
      if (el) el.checked = false;
    }
  }
</script>

<!-- Sidebar content -->
<div class="flex flex-col w-64 min-h-full bg-base-300 border-r border-base-content/5">
  <!-- Brand -->
  <div class="brand-section px-5 pt-6 pb-4">
    <a href="/" class="flex items-center gap-3" onclick={closeMobileDrawer}>
      <div class="w-9 h-9 rounded-lg bg-primary/15 flex items-center justify-center">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="m3.75 13.5 10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75Z" />
        </svg>
      </div>
      <div>
        <span class="text-lg font-bold text-primary tracking-tight">
          remu<span class="text-secondary">X</span>code
        </span>
        <p class="text-[10px] uppercase tracking-widest text-base-content/40 -mt-0.5">media pipeline</p>
      </div>
    </a>
  </div>

  <!-- Divider -->
  <div class="px-5 pb-2">
    <div class="border-t border-base-content/8"></div>
  </div>

  <!-- Nav links -->
  <ul class="menu menu-md flex-1 px-3 gap-0.5">
    <li class="menu-title text-[10px] uppercase tracking-wider text-base-content/30 px-3 pt-2 pb-1">Navigation</li>
    {#each nav as item}
      {@const active = $page.url.pathname === item.href}
      <li>
        <a
          href={item.href}
          class="rounded-lg transition-colors {active ? 'active bg-primary/10 text-primary' : 'hover:bg-base-content/5'}"
          onclick={closeMobileDrawer}
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 {active ? 'text-primary' : 'opacity-50'}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d={item.icon} />
          </svg>
          {item.label}
        </a>
      </li>
    {/each}
  </ul>

  <!-- Footer / health -->
  <div class="p-4 pt-2 border-t border-base-content/8">
    {#if health}
      <div class="flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-success"></span>
        </span>
        <span class="text-xs text-base-content/50">v{health.version}</span>
      </div>
    {:else if healthError}
      <div class="flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          <span class="relative inline-flex rounded-full h-2 w-2 bg-error"></span>
        </span>
        <span class="text-xs text-error/70">Offline</span>
      </div>
    {:else}
      <span class="loading loading-dots loading-xs"></span>
    {/if}
  </div>
</div>
