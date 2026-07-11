<script lang="ts">
import { convertFile } from '$lib/api';
import type { EncodeOptions, TargetResolution } from '$lib/types';

interface Props {
  paths: string | string[];
  label?: string;
  poster_url?: string;
  media_type?: string;
  onclose: () => void;
  onqueued?: () => void;
}

const { paths, label, poster_url, media_type, onclose, onqueued }: Props = $props();

const pathArray = $derived(Array.isArray(paths) ? paths : [paths]);
const isBulk = $derived(pathArray.length > 1);

type HdrMode = 'keep' | 'keep_dv' | 'strip';

let targetResolution: TargetResolution = $state('1080p');
let hdrMode = $state<HdrMode>('keep');
let queuing = $state(false);
let error = $state('');

const stripHdr = $derived(hdrMode === 'strip');
const retainDv = $derived(hdrMode === 'keep_dv');

// DV retention keeps the RPU aligned to the source raster — no downscale.
$effect(() => {
  if (retainDv && targetResolution !== 'original') {
    targetResolution = 'original';
  }
});

function fileName(p: string): string {
  return p.split('/').pop() ?? p;
}

const summaryLines = $derived.by(() => {
  const lines: string[] = [];
  if (targetResolution !== 'original') {
    lines.push(`Downscale to ${targetResolution} (source will not be upscaled)`);
  }
  if (stripHdr) {
    lines.push('Tone-map HDR/DV → SDR (BT.709)');
  }
  if (retainDv) {
    lines.push('Retain Dolby Vision as Profile 8.1 (HDR10 fallback included)');
  }
  lines.push(
    retainDv
      ? 'Force video re-encode to HEVC (software x265)'
      : 'Force video re-encode to configured codec (AV1/HEVC)',
  );
  lines.push('Audio and cleanup run per your configuration settings');
  return lines;
});

async function handleQueue() {
  queuing = true;
  error = '';
  const opts: EncodeOptions = {
    target_resolution: targetResolution,
    strip_hdr: stripHdr,
    force_encode: true,
    retain_dv: retainDv,
  };
  const errors: string[] = [];
  for (const p of pathArray) {
    try {
      await convertFile(p, 'full', poster_url, media_type, opts);
    } catch (e: unknown) {
      errors.push(e instanceof Error ? e.message : String(e));
    }
  }
  queuing = false;
  if (errors.length > 0) {
    error =
      errors.length === 1
        ? errors[0]
        : `${errors.length} of ${pathArray.length} files failed to queue`;
    if (errors.length === pathArray.length) return;
  }
  onqueued?.();
  onclose();
}
</script>

<div class="modal modal-open" role="dialog" aria-modal="true">
  <div class="modal-box max-w-sm">
    <button
      class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2"
      onclick={onclose}
    >✕</button>

    <h3 class="text-lg font-semibold mb-1">Custom Encode</h3>
    {#if isBulk}
      <p class="text-xs text-base-content/85 mb-4">{label ?? `${pathArray.length} files`} &middot; {pathArray.length} episode{pathArray.length !== 1 ? 's' : ''}</p>
    {:else}
      <p class="text-xs text-base-content/85 truncate mb-4" title={pathArray[0]}>{fileName(pathArray[0])}</p>
    {/if}

    <!-- Resolution -->
    <div class="mb-4">
      <div class="text-sm font-medium mb-2">Target Resolution</div>
      <div class="flex gap-2">
        {#each [['original', 'Original'], ['1080p', '1080p'], ['720p', '720p']] as [val, label]}
          <button
            class="btn btn-sm flex-1 {targetResolution === val ? 'btn-primary' : 'btn-ghost border border-base-300'}"
            disabled={retainDv && val !== 'original'}
            onclick={() => targetResolution = val as TargetResolution}
          >
            {label}
          </button>
        {/each}
      </div>
      {#if retainDv}
        <p class="text-xs text-base-content/85 mt-1">Locked to original resolution while retaining Dolby Vision.</p>
      {:else if targetResolution !== 'original'}
        <p class="text-xs text-base-content/85 mt-1">Source files already at or below {targetResolution} will not be upscaled.</p>
      {/if}
    </div>

    <!-- HDR -->
    <div class="mb-5">
      <div class="text-sm font-medium mb-2">HDR Handling</div>
      <div class="flex gap-2">
        <button
          class="btn btn-sm flex-1 {hdrMode === 'keep' ? 'btn-primary' : 'btn-ghost border border-base-300'}"
          onclick={() => hdrMode = 'keep'}
        >
          Keep HDR
        </button>
        <button
          class="btn btn-sm flex-1 {hdrMode === 'keep_dv' ? 'btn-primary' : 'btn-ghost border border-base-300'}"
          onclick={() => hdrMode = 'keep_dv'}
        >
          Keep HDR + DV
        </button>
        <button
          class="btn btn-sm flex-1 {hdrMode === 'strip' ? 'btn-warning' : 'btn-ghost border border-base-300'}"
          onclick={() => hdrMode = 'strip'}
        >
          Strip to SDR
        </button>
      </div>
      {#if hdrMode === 'strip'}
        <p class="text-xs text-base-content/85 mt-1">HDR (including Dolby Vision) will be tone-mapped to BT.709 SDR.</p>
      {:else if hdrMode === 'keep_dv'}
        <p class="text-xs text-base-content/85 mt-1">Dolby Vision is re-encoded as Profile 8.1 (dynamic metadata kept, HDR10 fallback included). Requires a DV source and software HEVC encoding; forces original resolution.</p>
      {:else}
        <p class="text-xs text-base-content/85 mt-1">HDR10 metadata is preserved. Dolby Vision RPU is stripped (HDR10 base layer kept).</p>
      {/if}
    </div>

    <!-- Summary -->
    <div class="bg-base-200 rounded-box p-3 mb-4">
      <p class="text-xs font-medium text-base-content/95 mb-1">This job will:</p>
      <ul class="text-xs text-base-content/95 space-y-0.5">
        {#each summaryLines as line}
          <li class="flex items-start gap-1.5"><span class="text-primary mt-0.5">›</span>{line}</li>
        {/each}
      </ul>
    </div>

    {#if error}
      <div class="alert alert-error alert-sm mb-3 text-sm py-2">{error}</div>
    {/if}

    <div class="modal-action">
      <button class="btn btn-ghost btn-sm" onclick={onclose}>Cancel</button>
      <button
        class="btn btn-primary btn-sm"
        onclick={handleQueue}
        disabled={queuing}
      >
        {#if queuing}
          <span class="loading loading-spinner loading-xs"></span>
        {:else}
          Queue {isBulk ? `${pathArray.length} Encodes` : 'Encode'}
        {/if}
      </button>
    </div>
  </div>
  <div
    class="modal-backdrop"
    role="button"
    tabindex="-1"
    aria-label="Close"
    onclick={onclose}
    onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') onclose(); }}
  ></div>
</div>
