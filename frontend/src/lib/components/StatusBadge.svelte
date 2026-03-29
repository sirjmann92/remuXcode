<script lang="ts">
import type { JobStatus } from '$lib/types';

interface Props {
  status: JobStatus;
}

const { status }: Props = $props();

const styles: Record<JobStatus, string> = {
  pending: 'badge-warning',
  running: 'badge-info',
  completed: 'badge-success',
  failed: 'badge-error',
  cancelled: 'badge-ghost',
};

const labels: Record<JobStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};
</script>

<span class="badge {styles[status] ?? 'badge-ghost'} badge-sm gap-1">
  {#if status === 'running'}
    <span class="loading loading-spinner loading-xs"></span>
  {/if}
  {labels[status] ?? status}
</span>
