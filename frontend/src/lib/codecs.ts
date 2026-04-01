// Shared codec group definitions and matching for contextual filter dropdowns.
// Used by both Movies and Shows pages for identical filter behavior.

export interface CodecGroup {
  value: string;
  label: string;
}

export const AUDIO_CODEC_GROUPS: CodecGroup[] = [
  { value: 'dts', label: 'DTS (all)' },
  { value: 'dts_x', label: 'DTS:X' },
  { value: 'dts_hd', label: 'DTS-HD MA' },
  { value: 'truehd', label: 'TrueHD' },
  { value: 'atmos', label: 'Atmos' },
  { value: 'eac3', label: 'EAC3 / DD+' },
  { value: 'ac3', label: 'AC3 / DD' },
  { value: 'aac', label: 'AAC' },
  { value: 'flac', label: 'FLAC' },
  { value: 'opus', label: 'Opus' },
  { value: 'pcm', label: 'PCM' },
];

export const VIDEO_CODEC_GROUPS: CodecGroup[] = [
  { value: 'hevc', label: 'HEVC / H.265' },
  { value: 'h264', label: 'H.264' },
  { value: 'av1', label: 'AV1' },
  { value: 'mpeg2', label: 'MPEG-2' },
  { value: 'vc1', label: 'VC-1' },
];

/** Test if an audio codec string matches a filter group value. */
export function audioCodecMatches(codec: string, filterValue: string, hasDtsX?: boolean): boolean {
  const ac = codec.toUpperCase();
  switch (filterValue) {
    case 'dts':
      return ac.includes('DTS');
    case 'dts_x':
      return !!hasDtsX || ac === 'DTS:X';
    case 'dts_hd':
      return ac.includes('DTS-HD') || ac.includes('DTS HD');
    case 'truehd':
      return ac.includes('TRUEHD');
    case 'atmos':
      return ac.includes('ATMOS');
    case 'eac3':
      return ac.includes('EAC3');
    case 'ac3':
      return (ac.includes('AC3') || ac === 'DD') && !ac.includes('EAC3');
    case 'aac':
      return ac.includes('AAC');
    case 'flac':
      return ac.includes('FLAC');
    case 'opus':
      return ac.includes('OPUS');
    case 'pcm':
      return ac.includes('PCM');
    default:
      return false;
  }
}

/** Test if a video codec string matches a filter group value. */
export function videoCodecMatches(codec: string, filterValue: string): boolean {
  const vc = codec.toUpperCase();
  switch (filterValue) {
    case 'hevc':
      return ['HEVC', 'H265', 'X265'].some((c) => vc.includes(c));
    case 'h264':
      return ['H264', 'H.264', 'X264', 'AVC'].some((c) => vc.includes(c));
    case 'av1':
      return vc.includes('AV1');
    case 'mpeg2':
      return vc.includes('MPEG2');
    case 'vc1':
      return vc.includes('VC1') || vc.includes('VC-1');
    default:
      return false;
  }
}

/** Build contextual audio format dropdown options from actual codec data. */
export function buildAudioOptions(
  allCodecs: string[],
  anyHasDtsX: boolean,
): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [{ value: 'any', label: 'Audio: Any' }];
  for (const group of AUDIO_CODEC_GROUPS) {
    const present =
      group.value === 'dts_x'
        ? anyHasDtsX || allCodecs.some((c) => c.toUpperCase() === 'DTS:X')
        : allCodecs.some((c) => audioCodecMatches(c, group.value));
    if (present) options.push(group);
  }
  return options;
}

/** Build contextual video format dropdown options from actual codec data. */
export function buildVideoOptions(allCodecs: string[]): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [{ value: 'any', label: 'Video: Any' }];
  for (const group of VIDEO_CODEC_GROUPS) {
    if (allCodecs.some((c) => videoCodecMatches(c, group.value))) options.push(group);
  }
  return options;
}
