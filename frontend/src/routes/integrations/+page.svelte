<script lang="ts">
import { getConfig, regenerateApiKey, updateConfig } from '$lib/api';
import type { ConfigSummary } from '$lib/types';

let config: ConfigSummary | null = $state(null);
let loading = $state(true);
let error = $state('');
let saving = $state(false);
let saveMsg = $state('');
let keyCopied = $state(false);
let keyVisible = $state(false);
let regenerating = $state(false);

async function fetchConfig() {
  loading = true;
  error = '';
  try {
    config = await getConfig();
  } catch (e) {
    error = e instanceof Error ? e.message : 'Failed to load config';
  } finally {
    loading = false;
  }
}

async function saveNested(section: 'sonarr' | 'radarr', field: string, value: unknown) {
  saving = true;
  saveMsg = '';
  try {
    await updateConfig({ [section]: { [field]: value } });
    saveMsg = 'Saved';
    setTimeout(() => {
      saveMsg = '';
    }, 1500);
  } catch (e) {
    saveMsg = e instanceof Error ? e.message : 'Save failed';
  } finally {
    saving = false;
  }
}

async function copyKey() {
  if (!config?.api_key) return;
  await navigator.clipboard.writeText(config.api_key);
  keyCopied = true;
  setTimeout(() => {
    keyCopied = false;
  }, 2000);
}

async function handleRegenerate() {
  if (!confirm('Regenerate the API key? Existing Sonarr/Radarr webhooks will need the new key.'))
    return;
  regenerating = true;
  try {
    const res = await regenerateApiKey();
    if (config) config.api_key = res.api_key;
    keyVisible = true;
  } catch (e) {
    error = e instanceof Error ? e.message : 'Failed to regenerate key';
  } finally {
    regenerating = false;
  }
}

$effect(() => {
  fetchConfig();
});
</script>

<svelte:head>
  <title>Integrations · remuXcode</title>
</svelte:head>

<div class="space-y-6">

  {#if loading}
    <div class="flex justify-center py-12">
      <span class="loading loading-spinner loading-lg"></span>
    </div>
  {:else if error}
    <div class="alert alert-error">
      <span>{error}</span>
    </div>
  {:else if config}
    {#if saveMsg}
      <div class="toast toast-top toast-end z-50">
        <div class="alert alert-success alert-sm py-2">
          <span class="text-xs">{saveMsg}</span>
        </div>
      </div>
    {/if}

    <!-- API Key -->
    <div class="card-glass rounded-box">
      <div class="p-5">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">API Key</h2>
        <p class="text-xs opacity-60 mb-3">
          Use this key as the <code class="bg-base-300 px-1 rounded">X-API-Key</code> header
          when configuring Sonarr/Radarr webhook connections.
        </p>
        <div class="flex gap-2 items-center">
          <input
            class="input input-sm input-bordered flex-1 font-mono"
            type={keyVisible ? 'text' : 'password'}
            value={config.api_key}
            readonly
          />
          <button class="btn btn-sm btn-ghost" onclick={() => (keyVisible = !keyVisible)} title={keyVisible ? 'Hide' : 'Show'}>
            {#if keyVisible}
              <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12c1.292 4.338 5.31 7.5 10.066 7.5.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" /></svg>
            {:else}
              <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" /></svg>
            {/if}
          </button>
          <button class="btn btn-sm btn-ghost" onclick={copyKey} title="Copy to clipboard">
            {#if keyCopied}
              <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
            {:else}
              <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M15.666 3.888A2.25 2.25 0 0 0 13.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 0 1-.75.75H9.75a.75.75 0 0 1-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 0 1 1.927-.184" /></svg>
            {/if}
          </button>
          <button class="btn btn-sm btn-ghost text-warning" onclick={handleRegenerate} disabled={regenerating} title="Regenerate API key">
            {#if regenerating}
              <span class="loading loading-spinner loading-xs"></span>
            {:else}
              <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M21.015 4.356v4.992" /></svg>
            {/if}
          </button>
        </div>
      </div>
    </div>

    <div class="grid md:grid-cols-2 gap-4">
      <!-- Sonarr -->
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">Sonarr</h2>
          <p class="text-xs text-base-content/40 mb-3">Connect to Sonarr for library browsing, metadata, and post-conversion rename triggers.</p>
          <div class="space-y-3 text-sm">
            <div class="flex items-center justify-between" title="Sonarr instance URL (e.g. http://192.168.1.100:8989)">
              <span class="text-xs">URL<span class="block text-xs text-base-content/30 font-normal">Sonarr server address</span></span>
              <input
                type="text"
                class="input input-xs input-bordered w-52 font-mono text-xs"
                value={config.sonarr.url}
                placeholder="http://localhost:8989"
                onchange={(e) => { config!.sonarr.url = e.currentTarget.value; saveNested('sonarr', 'url', e.currentTarget.value); }}
              />
            </div>
            <div class="flex items-center justify-between" title="Sonarr API key from Settings → General → Security">
              <span class="text-xs">API Key<span class="block text-xs text-base-content/30 font-normal">Found in Sonarr → Settings → General</span></span>
              <input
                type="password"
                class="input input-xs input-bordered w-52 font-mono text-xs"
                value={config.sonarr.api_key}
                placeholder="Your Sonarr API key"
                onchange={(e) => { config!.sonarr.api_key = e.currentTarget.value; saveNested('sonarr', 'api_key', e.currentTarget.value); }}
              />
            </div>
            <div class="flex items-center gap-2">
              <span class="badge badge-xs {config.sonarr.configured ? 'badge-success' : 'badge-ghost'}">
                {config.sonarr.configured ? 'Connected' : 'Not configured'}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Radarr -->
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">Radarr</h2>
          <p class="text-xs text-base-content/40 mb-3">Connect to Radarr for library browsing, metadata, and post-conversion rename triggers.</p>
          <div class="space-y-3 text-sm">
            <div class="flex items-center justify-between" title="Radarr instance URL (e.g. http://192.168.1.100:7878)">
              <span class="text-xs">URL<span class="block text-xs text-base-content/30 font-normal">Radarr server address</span></span>
              <input
                type="text"
                class="input input-xs input-bordered w-52 font-mono text-xs"
                value={config.radarr.url}
                placeholder="http://localhost:7878"
                onchange={(e) => { config!.radarr.url = e.currentTarget.value; saveNested('radarr', 'url', e.currentTarget.value); }}
              />
            </div>
            <div class="flex items-center justify-between" title="Radarr API key from Settings → General → Security">
              <span class="text-xs">API Key<span class="block text-xs text-base-content/30 font-normal">Found in Radarr → Settings → General</span></span>
              <input
                type="password"
                class="input input-xs input-bordered w-52 font-mono text-xs"
                value={config.radarr.api_key}
                placeholder="Your Radarr API key"
                onchange={(e) => { config!.radarr.api_key = e.currentTarget.value; saveNested('radarr', 'api_key', e.currentTarget.value); }}
              />
            </div>
            <div class="flex items-center gap-2">
              <span class="badge badge-xs {config.radarr.configured ? 'badge-success' : 'badge-ghost'}">
                {config.radarr.configured ? 'Connected' : 'Not configured'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Path Mappings -->
    {#if config.path_mappings.length > 0}
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-3">Path Mappings</h2>
          <div class="overflow-x-auto">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th>Container</th>
                  <th>Host</th>
                </tr>
              </thead>
              <tbody>
                {#each config.path_mappings as m}
                  <tr>
                    <td class="font-mono">{m.container}</td>
                    <td class="font-mono">{m.host}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    {/if}

  {/if}
</div>
