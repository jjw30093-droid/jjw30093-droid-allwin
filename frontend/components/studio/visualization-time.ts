import type { Bundle, Event } from './visualization-data';

const phaseDefinitions = [
  { id: 1, label: '上半场', start: 0, end: 45 },
  { id: 2, label: '下半场', start: 45, end: 90 },
  { id: 3, label: '加时上半场', start: 90, end: 105 },
  { id: 4, label: '加时下半场', start: 105, end: 120 },
];
const PHASE_GAP = 60;

type Clock = Pick<Event, 'period' | 'minute' | 'second'>;
export type Phase = (typeof phaseDefinitions)[number] & { offset: number; duration: number };
export type MatchTime = { phases: Phase[]; end: number };
export type TimeRange = readonly [number, number];

export function matchTime(bundle: Pick<Bundle, 'events' | 'markers' | 'momentum' | 'periods'>): MatchTime {
  const observed = new Map<number, number>();
  for (const point of [...bundle.events, ...bundle.markers, ...bundle.momentum]) {
    observed.set(point.period, Math.max(observed.get(point.period) ?? 0, point.minute * 60 + point.second));
  }
  const recorded = new Map(bundle.periods?.map(period => [period.id as number, period.duration_seconds]));
  let offset = 0;
  const phases = phaseDefinitions.filter(({ id }) => (!recorded.size && id <= 2) || recorded.has(id) || observed.has(id)).map(phase => {
    const observedDuration = (observed.get(phase.id) ?? phase.start * 60) - phase.start * 60;
    const duration = Math.max(recorded.get(phase.id) ?? (phase.end - phase.start) * 60, observedDuration);
    const result = { ...phase, duration, offset };
    offset += duration + PHASE_GAP;
    return result;
  });
  return { phases, end: offset - PHASE_GAP };
}

export function axisPosition(time: MatchTime, point: Clock) {
  const phase = time.phases.find(item => item.id === point.period);
  if (!phase) return null;
  return phase.offset + Math.max(0, Math.min(phase.duration, (point.minute - phase.start) * 60 + point.second));
}

export function formatMatchTime(time: MatchTime, position: number, exact = false) {
  const phase = [...time.phases].reverse().find(item => item.offset <= position) ?? time.phases[0];
  const seconds = phase.start * 60 + Math.max(0, Math.min(phase.duration, position - phase.offset));
  const minute = Math.floor(seconds / 60);
  const extra = seconds > phase.end * 60;
  const label = extra ? `${phase.end}+${minute - phase.end}` : String(minute);
  return exact ? `${phase.label} ${label}:${String(seconds % 60).padStart(2, '0')}` : `${label}′`;
}

export function axisTicks(time: MatchTime) {
  return time.phases.flatMap(phase => {
    const ticks = phase.id === 1 ? [{ position: phase.offset, label: `${phase.start}′` }] : [];
    for (let minute = phase.start + 15; minute <= phase.end && (minute - phase.start) * 60 <= phase.duration; minute += 15) {
      ticks.push({ position: phase.offset + (minute - phase.start) * 60, label: `${minute}′` });
    }
    return ticks;
  });
}

export function snapToPlayingTime(time: MatchTime, position: number) {
  const rounded = Math.max(0, Math.min(time.end, Math.round(position)));
  const next = time.phases.findIndex(phase => phase.offset > rounded);
  if (next < 1) return rounded;
  const previousEnd = time.phases[next - 1].offset + time.phases[next - 1].duration;
  if (rounded <= previousEnd) return rounded;
  return rounded - previousEnd <= time.phases[next].offset - rounded ? previousEnd : time.phases[next].offset;
}

export function inTimeRange(time: MatchTime, range: TimeRange | null, point: Clock) {
  const position = axisPosition(time, point);
  return position !== null && (!range || position >= range[0] && position <= range[1]);
}
