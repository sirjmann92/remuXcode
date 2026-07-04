// Shared resolution bucket definitions and matching for contextual filter dropdowns.
// Used by both Movies and Shows pages for identical filter behavior.

export interface ResolutionGroup {
  value: string;
  label: string;
}

const RESOLUTION_GROUPS: ResolutionGroup[] = [
  { value: '2160p', label: '2160p (4K)' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: 'sd', label: 'SD (480p and below)' },
];

/** Extract height in pixels from a "WIDTHxHEIGHT" resolution string. */
function heightOf(resolution: string): number | null {
  const match = resolution.match(/x(\d+)$/i);
  return match ? parseInt(match[1], 10) : null;
}

/** Test if a resolution string (e.g. "1920x1080") matches a filter bucket. */
export function resolutionMatches(resolution: string, filterValue: string): boolean {
  const height = heightOf(resolution);
  if (height == null) return false;
  switch (filterValue) {
    case '2160p':
      return height >= 1800;
    case '1080p':
      return height >= 900 && height < 1800;
    case '720p':
      return height >= 600 && height < 900;
    case 'sd':
      return height < 600;
    default:
      return false;
  }
}

/** Build contextual resolution dropdown options from actual resolution data. */
export function buildResolutionOptions(
  allResolutions: string[],
): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [{ value: 'any', label: 'Resolution: Any' }];
  for (const group of RESOLUTION_GROUPS) {
    if (allResolutions.some((r) => resolutionMatches(r, group.value))) options.push(group);
  }
  return options;
}
