<script lang="ts">
import { cleanupTempDirs, getConfig, getSystemInfo, updateConfig } from '$lib/api';
import LanguageSelect from '$lib/components/LanguageSelect.svelte';
import type { ConfigSummary, HWAccelCaps } from '$lib/types';

let config: ConfigSummary | null = $state(null);
let cpuCount = $state(0);
let hwAccel: HWAccelCaps | null = $state(null);
let pCoreCount = $state(0);
let isHybridCpu = $state(false);
let loading = $state(true);
let error = $state('');
let saving = $state(false);
let saveMsg = $state('');
let cleaningTemp = $state(false);
let cleanTempMsg = $state('');

const effectiveMethod = $derived.by(() => {
  if (!config) return 'none';
  const mode = config.video.hw_accel;
  if (mode === 'none') return 'none';
  if (mode !== 'auto') return mode;
  // Auto: prefer QSV > VAAPI > NVENC (same order as backend)
  if (hwAccel?.qsv_available) return 'qsv';
  if (hwAccel?.vaapi_available) return 'vaapi';
  if (hwAccel?.nvenc_available) return 'nvenc';
  return 'none';
});

const qualityLabel = $derived(
  effectiveMethod === 'qsv'
    ? 'ICQ'
    : effectiveMethod === 'vaapi'
      ? 'QP'
      : effectiveMethod === 'nvenc'
        ? 'CQ'
        : 'CRF',
);

const isAv1Sw = $derived.by(() => {
  if (!config) return false;
  return effectiveMethod === 'none' && config.video.codec === 'av1';
});

const animeQualityField = $derived(
  effectiveMethod === 'qsv'
    ? 'qsv_anime_quality'
    : effectiveMethod === 'vaapi'
      ? 'vaapi_anime_quality'
      : effectiveMethod === 'nvenc'
        ? 'nvenc_anime_quality'
        : isAv1Sw
          ? 'av1_anime_crf'
          : 'anime_crf',
);

const liveQualityField = $derived(
  effectiveMethod === 'qsv'
    ? 'qsv_live_action_quality'
    : effectiveMethod === 'vaapi'
      ? 'vaapi_live_action_quality'
      : effectiveMethod === 'nvenc'
        ? 'nvenc_live_action_quality'
        : isAv1Sw
          ? 'av1_live_action_crf'
          : 'live_action_crf',
);

const qualityMax = $derived(effectiveMethod === 'vaapi' ? 52 : isAv1Sw ? 63 : 51);

// Compute which codecs are actually supported for the active hw_accel method.
// Caps lists only contain encoders that passed a live 1-frame encode probe,
// so this correctly excludes e.g. av1_qsv on Xe-LP iGPUs.
const av1SupportedByMethod = $derived.by(() => {
  if (!hwAccel) return true; // unknown — allow until caps load
  if (effectiveMethod === 'none') return true; // software SVT-AV1 always available
  const methodKey = `${effectiveMethod}_` as const; // "qsv_" | "vaapi_" | "nvenc_"
  return hwAccel.av1_encoders.some((e) => e.startsWith(methodKey));
});

const heveSupportedByMethod = $derived.by(() => {
  if (!hwAccel) return true;
  if (effectiveMethod === 'none') return true;
  const methodKey = `${effectiveMethod}_` as const;
  return hwAccel.hevc_encoders.some((e) => e.startsWith(methodKey));
});

const qualityRangeHint = $derived(
  effectiveMethod === 'qsv'
    ? '1–51'
    : effectiveMethod === 'vaapi'
      ? '1–52'
      : effectiveMethod === 'nvenc'
        ? '1–51'
        : isAv1Sw
          ? '1–63'
          : '0–51',
);

// --- Codec availability logic ---
const supportedCodecs = $derived.by(() => {
  if (!hwAccel) return ['hevc', 'av1']; // fallback
  if (effectiveMethod === 'none') return ['hevc', 'av1']; // software always available
  const codecs: string[] = [];
  if (effectiveMethod === 'qsv') {
    if (hwAccel.hevc_encoders.includes('hevc_qsv')) codecs.push('hevc');
    if (hwAccel.av1_encoders.includes('av1_qsv')) codecs.push('av1');
  } else if (effectiveMethod === 'vaapi') {
    if (hwAccel.hevc_encoders.includes('hevc_vaapi')) codecs.push('hevc');
    if (hwAccel.av1_encoders.includes('av1_vaapi')) codecs.push('av1');
  } else if (effectiveMethod === 'nvenc') {
    if (hwAccel.hevc_encoders.includes('hevc_nvenc')) codecs.push('hevc');
    if (hwAccel.av1_encoders.includes('av1_nvenc')) codecs.push('av1');
  }
  return codecs;
});

$effect(() => {
  if (!config) return;
  // If the selected codec is not supported, auto-switch to the first available
  if (!supportedCodecs.includes(config.video.codec)) {
    const fallback = supportedCodecs[0] || 'hevc';
    config.video.codec = fallback;
    save('video', 'codec', fallback);
  }
});

async function fetchConfig() {
  loading = true;
  error = '';
  try {
    const [cfg, sys] = await Promise.all([getConfig(), getSystemInfo()]);
    config = cfg;
    cpuCount = sys.cpu_count;
    hwAccel = sys.hw_accel;
    pCoreCount = sys.p_core_count;
    isHybridCpu = sys.is_hybrid_cpu;
  } catch (e) {
    error = e instanceof Error ? e.message : 'Failed to load config';
  } finally {
    loading = false;
  }
}

async function save(section: string, field: string, value: unknown) {
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

async function toggleBool(section: 'audio' | 'video' | 'cleanup', field: string) {
  if (!config) return;
  const current = (config[section] as Record<string, unknown>)[field] as boolean;
  const next = !current;
  (config[section] as Record<string, unknown>)[field] = next;
  await save(section, field, next);
}

function clampInt(
  e: Event & { currentTarget: HTMLInputElement },
  min: number,
  max: number,
): number {
  let v = parseInt(e.currentTarget.value, 10);
  if (Number.isNaN(v)) v = min;
  v = Math.max(min, Math.min(max, v));
  e.currentTarget.value = String(v);
  return v;
}

function saveStr(
  section: 'audio' | 'video' | 'cleanup',
  field: string,
  e: Event & { currentTarget: HTMLInputElement | HTMLSelectElement },
) {
  const v = e.currentTarget.value;
  if (!config) return;
  (config[section] as Record<string, unknown>)[field] = v;
  save(section, field, v || null);
}

async function saveTop(field: string, value: unknown) {
  saving = true;
  saveMsg = '';
  try {
    await updateConfig({ [field]: value });
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

$effect(() => {
  fetchConfig();

  // Re-fetch when the tab regains visibility after being idle/backgrounded.
  // Without this, number inputs can display stale values once the browser
  // throttles or discards the tab's rendering state.
  function onVisible() {
    if (document.visibilityState === 'visible') fetchConfig();
  }
  // Also handle bfcache restores (back/forward navigation in some browsers)
  function onPageShow(e: PageTransitionEvent) {
    if (e.persisted) fetchConfig();
  }
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('pageshow', onPageShow);
  return () => {
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('pageshow', onPageShow);
  };
});
</script>

<svelte:head>
  <title>Settings · remuXcode</title>
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

    <!-- Hardware Acceleration -->
    <div class="card-glass rounded-box">
      <div class="p-5">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">Hardware Acceleration</h2>
        <p class="text-xs text-base-content/40 mb-3">Use GPU hardware encoding for faster video conversion. Requires <code class="bg-base-300 px-1 rounded">/dev/dri</code> passthrough in your compose file.</p>
        <div class="space-y-2 text-sm">
          <div class="flex items-center justify-between">
            <span>Mode<span class="block text-xs text-base-content/30 font-normal">
              {#if !hwAccel?.render_devices?.length}
                No GPU detected — mount <code class="bg-base-300 px-0.5 rounded">/dev/dri</code> to enable
              {:else if hwAccel.gpu_vendor}
                {hwAccel.gpu_vendor.charAt(0).toUpperCase() + hwAccel.gpu_vendor.slice(1)} GPU detected
              {:else}
                GPU detected
              {/if}
            </span></span>
            <select
              class="select select-xs select-bordered w-28"
              value={config.video.hw_accel}
              onchange={(e) => { config!.video.hw_accel = e.currentTarget.value; save('video', 'hw_accel', e.currentTarget.value); }}
            >
              <option value="none">None (CPU)</option>
              <option value="auto" disabled={!hwAccel?.qsv_available && !hwAccel?.vaapi_available && !hwAccel?.nvenc_available}>Auto</option>
              <option value="qsv" disabled={!hwAccel?.qsv_available}>QSV (Intel)</option>
              <option value="vaapi" disabled={!hwAccel?.vaapi_available}>VAAPI</option>
              <option value="nvenc" disabled={!hwAccel?.nvenc_available}>NVENC (NVIDIA)</option>
            </select>
          </div>
          {#if config.video.hw_accel === 'auto' && effectiveMethod !== 'none'}
            <div class="flex items-center gap-2 mt-1">
              <span class="badge badge-sm badge-accent gap-1">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
                Using {effectiveMethod.toUpperCase()}
              </span>
              <span class="text-xs text-base-content/30">auto-detected</span>
            </div>
          {/if}
          {#if hwAccel?.render_devices?.length}
            <div class="flex flex-wrap gap-1 mt-1">
              {#if hwAccel.qsv_available}
                <span class="badge badge-xs badge-success">QSV</span>
              {/if}
              {#if hwAccel.vaapi_available}
                <span class="badge badge-xs badge-success">VAAPI</span>
              {/if}
              {#if hwAccel.nvenc_available}
                <span class="badge badge-xs badge-success">NVENC</span>
              {/if}
              {#if !hwAccel.qsv_available && !hwAccel.vaapi_available && !hwAccel.nvenc_available}
                <span class="badge badge-xs badge-warning">No HW encoders available</span>
              {/if}
            </div>
            <details class="border-t border-base-content/10 pt-2 mt-2">
              <summary class="text-xs text-base-content/40 cursor-pointer hover:text-base-content/60 select-none">Details</summary>
              <div class="mt-2 space-y-1 text-xs text-base-content/50">
                <div><span class="font-medium">Devices:</span> {hwAccel.render_devices.join(', ')}</div>
                <div><span class="font-medium">HEVC encoders:</span> {hwAccel.hevc_encoders.join(', ') || 'none'}</div>
                <div><span class="font-medium">AV1 encoders:</span> {hwAccel.av1_encoders.join(', ') || 'none'}</div>
              </div>
            </details>
          {/if}
        </div>
      </div>
    </div>

    <div class="grid md:grid-cols-2 gap-4">
      <!-- Audio -->
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">Audio Processing</h2>
          <p class="text-xs text-base-content/40 mb-3">Convert lossless and legacy audio codecs to smaller, compatible formats.</p>
          <div class="space-y-2 text-sm">
            <label class="flex items-center justify-between cursor-pointer" title="Enable automatic audio conversion">
              <span>Enabled<span class="block text-xs text-base-content/30 font-normal">Enable automatic audio conversion</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.enabled} onchange={() => toggleBool('audio', 'enabled')} />
            </label>
            <div class="{config.audio.enabled ? '' : 'opacity-40 pointer-events-none'} space-y-2 transition-opacity">
            <!-- Process Anime -->
            <label class="flex items-center justify-between cursor-pointer" title="Apply audio conversion to anime content">
              <span>Process Anime<span class="block text-xs text-base-content/30 font-normal">Apply audio conversion to anime content</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.process_anime} onchange={() => toggleBool('audio', 'process_anime')} />
            </label>
            <!-- Process Standard -->
            <label class="flex items-center justify-between cursor-pointer" title="Apply audio conversion to standard (non-anime) content">
              <span>Process Standard<span class="block text-xs text-base-content/30 font-normal">Apply audio conversion to standard (non-anime) content</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.process_live_action} onchange={() => toggleBool('audio', 'process_live_action')} />
            </label>
            <!-- Convert DTS -->
            <label class="flex items-center justify-between cursor-pointer" title="Re-encode DTS/DTS-HD audio to EAC3 or AC3">
              <span>Convert DTS<span class="block text-xs text-base-content/30 font-normal">Re-encode DTS/DTS-HD audio to EAC3 or AC3</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.convert_dts} onchange={() => toggleBool('audio', 'convert_dts')} />
            </label>
            <div class="pl-4 {config.audio.convert_dts ? '' : 'opacity-40 pointer-events-none'} transition-opacity">
              <label class="flex items-center justify-between cursor-pointer" title="Retain the original DTS track alongside the converted one">
                <span>Keep Original<span class="block text-xs text-base-content/30 font-normal">Retain the original DTS track alongside the converted one</span></span>
                <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.keep_original} onchange={() => toggleBool('audio', 'keep_original')} />
              </label>
            </div>
            <!-- Convert DTS:X -->
            <label class="flex items-center justify-between cursor-pointer" title="Re-encode DTS:X (object-based) audio to EAC3 or AC3">
              <span>Convert DTS:X<span class="block text-xs text-base-content/30 font-normal">Re-encode DTS:X (object-based) audio to EAC3 or AC3</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.convert_dts_x} onchange={() => toggleBool('audio', 'convert_dts_x')} />
            </label>
            <div class="pl-4 {config.audio.convert_dts_x ? '' : 'opacity-40 pointer-events-none'} transition-opacity">
              <label class="flex items-center justify-between cursor-pointer" title="Retain the original DTS:X track alongside the converted one">
                <span>Keep Original DTS:X<span class="block text-xs text-base-content/30 font-normal">Retain the original DTS:X track alongside the converted one</span></span>
                <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.keep_original_dts_x} onchange={() => toggleBool('audio', 'keep_original_dts_x')} />
              </label>
            </div>
            <!-- Convert TrueHD -->
            <label class="flex items-center justify-between cursor-pointer" title="Re-encode Dolby TrueHD/Atmos audio (lossless → lossy)">
              <span>Convert TrueHD<span class="block text-xs text-base-content/30 font-normal">Re-encode Dolby TrueHD/Atmos audio (lossless → lossy)</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.convert_truehd} onchange={() => toggleBool('audio', 'convert_truehd')} />
            </label>
            <!-- Original as Secondary -->
            <label class="flex items-center justify-between cursor-pointer" title="Place the converted track before the original so players use it by default">
              <span>Original as Secondary<span class="block text-xs text-base-content/30 font-normal">Place the converted track first so players use it by default</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.original_as_secondary} onchange={() => toggleBool('audio', 'original_as_secondary')} />
            </label>
            <!-- Prefer AC3 -->
            <label class="flex items-center justify-between cursor-pointer" title="Use AC3 (Dolby Digital) instead of EAC3 for wider device support">
              <span>Prefer AC3<span class="block text-xs text-base-content/30 font-normal">Use AC3 (Dolby Digital) instead of EAC3 for wider device support</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.audio.prefer_ac3} onchange={() => toggleBool('audio', 'prefer_ac3')} />
            </label>
            <!-- Advanced -->
            <details class="border-t border-base-content/10 pt-2 mt-3">
              <summary class="text-xs text-base-content/40 cursor-pointer hover:text-base-content/60 select-none">Advanced</summary>
              <div class="space-y-2 mt-2">
                {#each [
                  { field: 'ac3_bitrate', label: 'AC3 Bitrate', hint: 'kbps for Dolby Digital 5.1 output (64–640)', min: 64, max: 640 },
                  { field: 'eac3_bitrate', label: 'EAC3 Bitrate', hint: 'kbps for Dolby Digital Plus output (64–6144)', min: 64, max: 6144 },
                  { field: 'aac_surround_bitrate', label: 'AAC Surround Bitrate', hint: 'kbps for AAC 5.1+ output (64–1024)', min: 64, max: 1024 },
                  { field: 'aac_stereo_bitrate', label: 'AAC Stereo Bitrate', hint: 'kbps for AAC stereo output (64–320)', min: 64, max: 320 },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="number"
                      class="input input-xs input-bordered w-20 text-center font-mono"
                      min={item.min}
                      max={item.max}
                      value={(config.audio as Record<string, unknown>)[item.field]}
                      onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, item.min, item.max); (config!.audio as Record<string, unknown>)[item.field] = v; save('audio', item.field, v); }}
                    />
                  </div>
                {/each}
              </div>
            </details>
            </div>
          </div>
        </div>
      </div>

      <!-- Video -->
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">Video Processing</h2>
          <p class="text-xs text-base-content/40 mb-3">Re-encode video to modern codecs for smaller file sizes.</p>
          <div class="space-y-2 text-sm">
            <label class="flex items-center justify-between cursor-pointer" title="Enable automatic video conversion">
              <span>Enabled<span class="block text-xs text-base-content/30 font-normal">Enable automatic video conversion</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.video.enabled} onchange={() => toggleBool('video', 'enabled')} />
            </label>
            <div class="{config.video.enabled ? '' : 'opacity-40 pointer-events-none'} space-y-2 transition-opacity">
            <div class="flex items-center justify-between">
              <span>Target Codec<span class="block text-xs text-base-content/30 font-normal">Output codec for converted video</span></span>
              <select
                class="select select-xs select-bordered w-24"
                value={config.video.codec}
                onchange={(e) => { config!.video.codec = e.currentTarget.value; save('video', 'codec', e.currentTarget.value); }}
              >
                {#if supportedCodecs.includes('hevc')}
                  <option value="hevc">HEVC</option>
                {/if}
                {#if supportedCodecs.includes('av1')}
                  <option value="av1">AV1</option>
                {/if}
              </select>
            </div>
            {#if !av1SupportedByMethod}
              <div class="flex items-center gap-2 p-2 rounded-lg bg-warning/10 border border-warning/30 text-xs text-warning">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" /></svg>
                AV1 is not supported by {effectiveMethod.toUpperCase()} on this GPU. Only HEVC is available for hardware encoding.
              </div>
            {/if}
            {#each [
              { field: 'convert_10bit_x264', label: 'Convert 10-bit x264', hint: 'Re-encode 10-bit H.264 files (common in anime releases)' },
              { field: 'convert_8bit_x264', label: 'Convert 8-bit x264', hint: 'Re-encode standard 8-bit H.264 files' },
              { field: 'convert_legacy_codecs', label: 'Convert Legacy Codecs', hint: 'Re-encode VC-1, MPEG-2, MPEG-4/Xvid/DivX to the target codec' },
              { field: 'process_anime', label: 'Process Anime', hint: 'Apply video conversion to anime content' },
              { field: 'process_live_action', label: 'Process Standard', hint: 'Apply video conversion to standard (non-anime) content' },
              { field: 'dv_to_hdr10', label: 'Convert Dolby Vision → HDR10', hint: 'Strip DV RPU layer and encode; static HDR10 base is preserved. Off = skip DV files.', shortHint: 'Re-encode to static HDR10. Off = skip DV files.' },
              { field: 'hdr10plus_to_hdr10', label: 'Convert HDR10+ → HDR10', hint: 'Strip dynamic SMPTE 2094-40 metadata and encode; static HDR10 base is preserved. Off = skip HDR10+ files.', shortHint: 'Re-encode to static HDR10. Off = skip HDR10+ files.' },
            ] as item}
              <label
                class="flex items-center justify-between cursor-pointer"
                title={item.hint}
              >
                <span>{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.shortHint ?? item.hint}</span></span>
                <input
                  type="checkbox"
                  class="toggle toggle-sm toggle-primary shrink-0 ml-3"
                  checked={(config.video as Record<string, unknown>)[item.field] as boolean}
                  onchange={() => toggleBool('video', item.field)}
                />
              </label>
            {/each}
            <div class="flex items-center justify-between">
              <span>Anime {qualityLabel}<span class="block text-xs text-base-content/30 font-normal">Quality for anime ({qualityRangeHint}, lower = better)</span></span>
              {#key animeQualityField}
              <input
                type="number"
                class="input input-xs input-bordered w-16 text-center font-mono"
                min="0"
                max={qualityMax}
                autocomplete="off"
                value={(config.video as Record<string, unknown>)[animeQualityField]}
                onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, 0, qualityMax); (config!.video as Record<string, unknown>)[animeQualityField] = v; save('video', animeQualityField, v); }}
              />
              {/key}
            </div>
            <div class="flex items-center justify-between">
              <span>Standard {qualityLabel}<span class="block text-xs text-base-content/30 font-normal">Quality for standard content ({qualityRangeHint}, lower = better)</span></span>
              {#key liveQualityField}
              <input
                type="number"
                class="input input-xs input-bordered w-16 text-center font-mono"
                min="0"
                max={qualityMax}
                autocomplete="off"
                value={(config.video as Record<string, unknown>)[liveQualityField]}
                onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, 0, qualityMax); (config!.video as Record<string, unknown>)[liveQualityField] = v; save('video', liveQualityField, v); }}
              />
              {/key}
            </div>
            {#if effectiveMethod !== 'none'}
              <p class="text-xs text-base-content/30 -mt-1">Using {effectiveMethod.toUpperCase()} hardware encoder</p>
            {/if}
            <!-- Advanced -->
            <details class="border-t border-base-content/10 pt-2 mt-3">
              <summary class="text-xs text-base-content/40 cursor-pointer hover:text-base-content/60 select-none">Advanced</summary>
              <div class="space-y-2 mt-2">
                <label class="flex items-center justify-between cursor-pointer" title="Use path patterns and metadata to identify anime content">
                  <span class="text-xs">Auto-Detect Anime<span class="block text-xs text-base-content/30 font-normal">Use path patterns and metadata to identify anime</span></span>
                  <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.video.anime_auto_detect} onchange={() => toggleBool('video', 'anime_auto_detect')} />
                </label>
                <!-- HW Encoder Settings -->
                {#if effectiveMethod === 'qsv'}
                  <h3 class="text-xs font-semibold uppercase tracking-wider text-accent/60 pt-2">QSV Encoder</h3>
                  <div class="flex items-center justify-between">
                    <span class="text-xs">Preset<span class="block text-xs text-base-content/30 font-normal">QSV encoding speed (medium recommended)</span></span>
                    <select
                      class="select select-xs select-bordered w-28 font-mono"
                      value={config.video.qsv_preset}
                      onchange={(e) => saveStr('video', 'qsv_preset', e as Event & { currentTarget: HTMLSelectElement })}
                    >
                      {#each ['veryfast','faster','fast','medium','slow','slower','veryslow'] as opt}
                        <option value={opt}>{opt}</option>
                      {/each}
                    </select>
                  </div>
                {:else if effectiveMethod === 'nvenc'}
                  <h3 class="text-xs font-semibold uppercase tracking-wider text-accent/60 pt-2">NVENC Encoder</h3>
                  <div class="flex items-center justify-between">
                    <span class="text-xs">Preset<span class="block text-xs text-base-content/30 font-normal">NVENC quality preset (p1=fastest, p7=best)</span></span>
                    <select
                      class="select select-xs select-bordered w-28 font-mono"
                      value={config.video.nvenc_preset}
                      onchange={(e) => saveStr('video', 'nvenc_preset', e as Event & { currentTarget: HTMLSelectElement })}
                    >
                      {#each ['p1','p2','p3','p4','p5','p6','p7'] as opt}
                        <option value={opt}>{opt}</option>
                      {/each}
                    </select>
                  </div>
                {:else if effectiveMethod === 'vaapi'}
                  <h3 class="text-xs font-semibold uppercase tracking-wider text-accent/60 pt-2">VAAPI Encoder</h3>
                  <p class="text-xs text-base-content/30">Quality (QP) is configured above. VAAPI has no additional encoder settings.</p>
                {/if}
                <!-- Software Encoding Settings (snippet to avoid conditional tag nesting) -->
                {#snippet swSettings()}
                <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/30 pt-2">{effectiveMethod !== 'none' ? 'HEVC (x265)' : 'HEVC Encoding'}</h3>
                {#each [
                  { field: 'anime_crf', label: 'Anime CRF', type: 'number', hint: 'Quality for anime (0–51, lower = better)', min: 0, max: 51 },
                  { field: 'live_action_crf', label: 'Standard CRF', type: 'number', hint: 'Quality for standard content (0–51)', min: 0, max: 51 },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="number"
                      class="input input-xs input-bordered w-16 text-center font-mono"
                      min={item.min}
                      max={item.max}
                      value={(config!.video as Record<string, unknown>)[item.field]}
                      onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, item.min!, item.max!); (config!.video as Record<string, unknown>)[item.field] = v; save('video', item.field, v); }}
                    />
                  </div>
                {/each}
                {#each [
                  { field: 'anime_preset', label: 'Anime Preset', type: 'select', options: ['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow','placebo'], hint: 'Encoding speed vs quality for anime' },
                  { field: 'anime_tune', label: 'Anime Tune', type: 'select', options: ['','animation','grain','psnr','ssim','fastdecode','zerolatency'], hint: 'x265 tuning profile (empty = none)' },
                  { field: 'anime_framerate', label: 'Anime Framerate', type: 'text', hint: 'e.g. 24000/1001 for 23.976fps (empty = auto)' },
                  { field: 'live_action_preset', label: 'Standard Preset', type: 'select', options: ['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow','placebo'], hint: 'Encoding speed vs quality for standard content' },
                  { field: 'live_action_tune', label: 'Standard Tune', type: 'select', options: ['','grain','psnr','ssim','fastdecode','zerolatency'], hint: 'x265 tuning profile (empty = none)' },
                  { field: 'live_action_framerate', label: 'Standard Framerate', type: 'text', hint: 'Framerate override (empty = auto-detect)' },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    {#if item.type === 'select'}
                      <select
                        class="select select-xs select-bordered w-28 font-mono"
                        value={(config!.video as Record<string, unknown>)[item.field] as string}
                        onchange={(e) => saveStr('video', item.field, e as Event & { currentTarget: HTMLSelectElement })}
                      >
                        {#each item.options! as opt}
                          <option value={opt}>{opt || '(none)'}</option>
                        {/each}
                      </select>
                    {:else}
                      <input
                        type="text"
                        class="input input-xs input-bordered w-28 font-mono text-xs"
                        value={(config!.video as Record<string, unknown>)[item.field] as string}
                        onchange={(e) => saveStr('video', item.field, e as Event & { currentTarget: HTMLInputElement })}
                      />
                    {/if}
                  </div>
                {/each}
                <!-- VBV / Output Format -->
                {#each [
                  { field: 'vbv_maxrate', label: 'VBV Max Rate', hint: 'Max bitrate in kbps for rate control', min: 0, max: 100000 },
                  { field: 'vbv_bufsize', label: 'VBV Buffer Size', hint: 'Buffer size in kbps for rate control', min: 0, max: 200000 },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="number"
                      class="input input-xs input-bordered w-20 text-center font-mono"
                      min={item.min}
                      max={item.max}
                      value={(config!.video as Record<string, unknown>)[item.field]}
                      onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, item.min, item.max); (config!.video as Record<string, unknown>)[item.field] = v; save('video', item.field, v); }}
                    />
                  </div>
                {/each}
                {#each [
                  { field: 'level', label: 'Level', hint: 'H.265 level (e.g. 4.1)' },
                  { field: 'profile', label: 'Profile', hint: 'H.265 profile (e.g. main10)' },
                  { field: 'pix_fmt', label: 'Pixel Format', hint: 'Output pixel format (e.g. yuv420p10le)' },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="text"
                      class="input input-xs input-bordered w-28 font-mono text-xs"
                      value={(config!.video as Record<string, unknown>)[item.field] as string}
                      onchange={(e) => saveStr('video', item.field, e as Event & { currentTarget: HTMLInputElement })}
                    />
                  </div>
                {/each}
                <!-- AV1 -->
                <h3 class="text-xs font-semibold uppercase tracking-wider text-base-content/30 pt-2">{effectiveMethod !== 'none' ? 'AV1 (SVT-AV1)' : 'AV1 Encoding (SVT-AV1)'}</h3>
                {#each [
                  { field: 'av1_anime_crf', label: 'Anime CRF', hint: 'Quality for anime (0–63, lower = better)', min: 0, max: 63 },
                  { field: 'av1_anime_preset', label: 'Anime Preset', hint: 'Speed 0–13 (lower = slower/better)', min: 0, max: 13 },
                  { field: 'av1_anime_film_grain', label: 'Anime Film Grain', hint: 'Synthetic grain level 0–50; keep 0 for clean cel-shaded content', min: 0, max: 50 },
                  { field: 'av1_live_action_crf', label: 'Standard CRF', hint: 'Quality for standard content (0–63)', min: 0, max: 63 },
                  { field: 'av1_live_action_preset', label: 'Standard Preset', hint: 'Speed 0–13 (lower = slower/better)', min: 0, max: 13 },
                  { field: 'av1_live_action_film_grain', label: 'Standard Film Grain', hint: 'Synthetic grain 0–50; 4–8 helps cinematic/noisy sources, 0 for clean digital', min: 0, max: 50 },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="number"
                      class="input input-xs input-bordered w-16 text-center font-mono"
                      min={item.min}
                      max={item.max}
                      value={(config!.video as Record<string, unknown>)[item.field]}
                      onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, item.min, item.max); (config!.video as Record<string, unknown>)[item.field] = v; save('video', item.field, v); }}
                    />
                  </div>
                {/each}
                {#each [
                  { field: 'av1_anime_framerate', label: 'Anime Framerate', hint: 'e.g. 24000/1001 (empty = auto)' },
                  { field: 'av1_live_action_framerate', label: 'Standard Framerate', hint: 'Framerate override (empty = auto)' },
                ] as item}
                  <div class="flex items-center justify-between" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="text"
                      class="input input-xs input-bordered w-28 font-mono text-xs"
                      value={(config!.video as Record<string, unknown>)[item.field] as string}
                      onchange={(e) => saveStr('video', item.field, e as Event & { currentTarget: HTMLInputElement })}
                    />
                  </div>
                {/each}
                {/snippet}
                {#if effectiveMethod !== 'none'}
                <details class="border border-base-content/5 rounded-lg px-2 py-1 mt-2">
                  <summary class="text-xs text-base-content/30 cursor-pointer hover:text-base-content/50 select-none">Software Fallback Settings</summary>
                  <p class="text-xs text-base-content/25 mt-1 mb-2">These only apply when falling back to software encoding (libx265 / SVT-AV1).</p>
                  <div class="space-y-2">
                    {@render swSettings()}
                  </div>
                </details>
                {:else}
                  {@render swSettings()}
                {/if}
              </div>
            </details>
            </div>
          </div>
        </div>
      </div>

      <!-- Cleanup -->
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">Stream Cleanup</h2>
          <p class="text-xs text-base-content/40 mb-3">Remove unwanted audio tracks and subtitles based on language preferences.</p>
          <div class="space-y-2 text-sm">
            <label class="flex items-center justify-between cursor-pointer" title="Enable automatic removal of unwanted streams">
              <span>Enabled<span class="block text-xs text-base-content/30 font-normal">Enable automatic removal of unwanted streams</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.cleanup.enabled} onchange={() => toggleBool('cleanup', 'enabled')} />
            </label>
            <div class="{config.cleanup.enabled ? '' : 'opacity-40 pointer-events-none'} space-y-2 transition-opacity">
            <!-- Process Anime -->
            <label class="flex items-center justify-between cursor-pointer" title="Apply stream cleanup to anime content">
              <span>Process Anime<span class="block text-xs text-base-content/30 font-normal">Apply stream cleanup to anime content</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.cleanup.process_anime} onchange={() => toggleBool('cleanup', 'process_anime')} />
            </label>
            <!-- Process Standard -->
            <label class="flex items-center justify-between cursor-pointer" title="Apply stream cleanup to standard (non-anime) content">
              <span>Process Standard<span class="block text-xs text-base-content/30 font-normal">Apply stream cleanup to standard (non-anime) content</span></span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary shrink-0 ml-3" checked={config.cleanup.process_live_action} onchange={() => toggleBool('cleanup', 'process_live_action')} />
            </label>
            <div class="flex items-center justify-between">
              <span>Languages<span class="block text-xs text-base-content/30 font-normal">Audio and subtitle languages to keep</span></span>
              <LanguageSelect
                selected={config.cleanup.keep_languages}
                onchange={(codes) => {
                  config!.cleanup.keep_languages = codes;
                  save('cleanup', 'keep_languages', codes);
                }}
              />
            </div>
            {#each [
              { field: 'clean_audio', label: 'Clean Audio', hint: 'Remove audio tracks not in your language list' },
              { field: 'clean_subtitles', label: 'Clean Subtitles', hint: 'Remove subtitle tracks not in your language list' },
              { field: 'keep_commentary', label: 'Keep Commentary', hint: 'Preserve director/cast commentary tracks regardless of language' },
              { field: 'deprioritize_commentary', label: 'Deprioritize Commentary', hint: 'Sort commentary audio/subtitle tracks after non-commentary ones and strip their default flag' },
              { field: 'anime_keep_original_audio', label: 'Keep Original Anime Audio', hint: 'Always keep the original-language audio in anime files (Japanese, Korean, etc.)' },
              { field: 'keep_original_audio', label: 'Keep Original Audio', hint: 'Always keep the original-language audio in non-anime content' },
            ] as item}
              <label class="flex items-center justify-between cursor-pointer" title={item.hint}>
                <span>{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                <input
                  type="checkbox"
                  class="toggle toggle-sm toggle-primary shrink-0 ml-3"
                  checked={(config.cleanup as Record<string, unknown>)[item.field] as boolean}
                  onchange={() => toggleBool('cleanup', item.field)}
                />
              </label>
            {/each}
            <!-- Advanced -->
            <details class="border-t border-base-content/10 pt-2 mt-3">
              <summary class="text-xs text-base-content/40 cursor-pointer hover:text-base-content/60 select-none">Advanced</summary>
              <div class="space-y-2 mt-2">
                {#each [
                  { field: 'keep_undefined', label: 'Keep Undefined Language', hint: 'Keep tracks with no language tag instead of removing them' },
                  { field: 'keep_audio_description', label: 'Keep Audio Description', hint: 'Preserve audio description tracks for visually impaired' },
                  { field: 'keep_sdh', label: 'Keep SDH Subtitles', hint: 'Preserve subtitles for the deaf and hard of hearing' },
                ] as item}
                  <label class="flex items-center justify-between cursor-pointer" title={item.hint}>
                    <span class="text-xs">{item.label}<span class="block text-xs text-base-content/30 font-normal">{item.hint}</span></span>
                    <input
                      type="checkbox"
                      class="toggle toggle-sm toggle-primary shrink-0 ml-3"
                      checked={(config.cleanup as Record<string, unknown>)[item.field] as boolean}
                      onchange={() => toggleBool('cleanup', item.field)}
                    />
                  </label>
                {/each}
              </div>
            </details>
            </div>
          </div>
        </div>
      </div>

      <!-- System -->
      <div class="card-glass rounded-box">
        <div class="p-5">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-1">System</h2>
          <p class="text-xs text-base-content/40 mb-3">Worker concurrency, resource limits, and job retention.</p>
          <div class="space-y-3 text-sm">
            <div class="flex items-center justify-between" title="Number of files that can be processed simultaneously">
              <span class="text-xs">Workers<span class="block text-xs text-base-content/30 font-normal">Concurrent processing threads (1–16)</span></span>
              <input
                type="number"
                class="input input-xs input-bordered w-16 text-center font-mono"
                min="1"
                max="16"
                value={config.workers}
                onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, 1, 16); config!.workers = v; saveTop('workers', v); }}
              />
            </div>
            <div title="Limit CPU threads ffmpeg uses for encoding. 0 = auto. Lower values leave more CPU for other tasks.">
              <div class="flex items-center justify-between">
                <span class="text-xs">FFmpeg Threads<span class="block text-xs text-base-content/30 font-normal">{config.ffmpeg_threads === 0 ? `Auto (${config.effective_ffmpeg_threads} of ${isHybridCpu ? pCoreCount + ' P-cores' : cpuCount + ' CPUs'})` : `${config.ffmpeg_threads} of ${cpuCount} CPUs`}</span></span>
                <input
                  type="number"
                  class="input input-xs input-bordered w-16 text-center font-mono"
                  min="0"
                  max={cpuCount || 128}
                  value={config.ffmpeg_threads}
                  onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, 0, cpuCount || 128); config!.ffmpeg_threads = v; saveTop('ffmpeg_threads', v); }}
                />
              </div>
              {#if cpuCount > 0}
                <input
                  type="range"
                  class="range range-xs range-primary mt-1"
                  min="0"
                  max={cpuCount}
                  value={config.ffmpeg_threads}
                  oninput={(e) => { const v = parseInt(e.currentTarget.value, 10); config!.ffmpeg_threads = v; }}
                  onchange={(e) => { const v = parseInt(e.currentTarget.value, 10); config!.ffmpeg_threads = v; saveTop('ffmpeg_threads', v); }}
                />
                <div class="flex justify-between text-[0.6rem] text-base-content/25 px-0.5">
                  <span>Auto</span>
                  <span>{cpuCount}</span>
                </div>
              {/if}
            </div>
            <label class="flex items-center justify-between cursor-pointer" title="Pin ffmpeg to high-performance (P) cores on hybrid CPUs. No-op on AMD / homogeneous Intel CPUs.">
              <span class="text-xs">Pin to P-cores
                <span class="block text-xs text-base-content/30 font-normal">
                  {#if isHybridCpu}
                    Restrict encode to {pCoreCount} P-core threads; E-cores stay free
                  {:else}
                    {pCoreCount > 0 ? `${pCoreCount} threads detected — no E-cores (no-op)` : 'No frequency data — no-op on this CPU'}
                  {/if}
                </span>
              </span>
              <input
                type="checkbox"
                class="toggle toggle-sm toggle-primary"
                checked={config.ffmpeg_pin_to_p_cores}
                onchange={(e) => { config!.ffmpeg_pin_to_p_cores = e.currentTarget.checked; saveTop('ffmpeg_pin_to_p_cores', e.currentTarget.checked); }}
              />
            </label>
            <label class="flex items-center justify-between cursor-pointer" title="Remove embedded poster images from outputs. Prevents rare ffmpeg failures on malformed cover art.">
              <span class="text-xs">Strip Cover Art
                <span class="block text-xs text-base-content/30 font-normal">Remove embedded poster images from outputs</span>
              </span>
              <input
                type="checkbox"
                class="toggle toggle-sm toggle-primary"
                checked={config.strip_cover_art}
                onchange={(e) => { config!.strip_cover_art = e.currentTarget.checked; saveTop('strip_cover_art', e.currentTarget.checked); }}
              />
            </label>
            <div class="flex items-center justify-between" title="Completed and failed jobs are removed after this many days">
              <span class="text-xs">Job History<span class="block text-xs text-base-content/30 font-normal">Days to retain completed job records (1–365)</span></span>
              <input
                type="number"
                class="input input-xs input-bordered w-16 text-center font-mono"
                min="1"
                max="365"
                value={config.job_history_days}
                onchange={(e) => { const v = clampInt(e as Event & { currentTarget: HTMLInputElement }, 1, 365); config!.job_history_days = v; saveTop('job_history_days', v); }}
              />
            </div>
            <div class="flex items-center justify-between" title="Remove leftover .remuxcode-temp-* and .remuxcode-chain-* directories from failed or interrupted jobs">
              <span class="text-xs">Temp Files<span class="block text-xs text-base-content/30 font-normal">Remove orphaned temp dirs from failed encodes</span></span>
              <button
                class="btn btn-xs btn-outline"
                disabled={cleaningTemp}
                onclick={async () => {
                  cleaningTemp = true;
                  cleanTempMsg = '';
                  try {
                    const r = await cleanupTempDirs();
                    cleanTempMsg = r.cleaned > 0 ? `Removed ${r.cleaned} item(s)` : 'Nothing to clean';
                  } catch (e) {
                    cleanTempMsg = e instanceof Error ? e.message : 'Failed';
                  } finally {
                    cleaningTemp = false;
                  }
                }}
              >
                {#if cleaningTemp}
                  <span class="loading loading-spinner loading-xs"></span>
                {:else}
                  Clean Up
                {/if}
              </button>
            </div>
            {#if cleanTempMsg}
              <p class="text-xs text-base-content/50">{cleanTempMsg}</p>
            {/if}
          </div>
        </div>
      </div>
    </div>

  {/if}
</div>
