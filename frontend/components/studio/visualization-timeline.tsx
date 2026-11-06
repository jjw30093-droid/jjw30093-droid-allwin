'use client';

import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import type { Event } from './visualization-data';
import { axisPosition, axisTicks, formatMatchTime, inTimeRange, snapToPlayingTime, type MatchTime, type TimeRange } from './visualization-time';
import s from '@/app/studio/visualization/visualization.module.css';

export const momentumGrid = { left: 44, right: 20, top: 28, bottom: 28 };
const ordered = (a: number, b: number): TimeRange => [Math.min(a, b), Math.max(a, b)];
const incidentFilters = [['all', '全部'], ['goal', '进球'], ['card', '牌'], ['substitution', '换人'], ['shot', '点球未进']] as const;
type IncidentFilter = typeof incidentFilters[number][0];

function incidentKind(event: Event) {
  if (event.kind === 'substitution') {
    if (event.label === '换上') return 'sub-on';
    if (event.label === '换下') return 'sub-off';
    return 'substitution';
  }
  return event.goal || event.own_goal ? 'goal' : event.kind;
}

function IncidentIcon({ kind, label }: { kind: string; label: string }) {
  if (kind === 'goal') return <svg className={s.incidentIcon} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
    <circle cx="8" cy="8" r="6.1"/><path d="m8 5 2.8 2-.9 3.1H6.1L5.2 7 8 5Z" fill="currentColor"/>
    <path d="M8 5V2m2.8 5 2.9-1m-3.8 4.1 1.8 2.4m-5.6-2.4-1.8 2.4M5.2 7 2.3 6"/>
  </svg>;
  if (kind === 'sub-on' || kind === 'sub-off' || kind === 'substitution') return <svg className={s.incidentIcon} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === 'sub-on' ? <path d="M8 13V3M4 7l4-4 4 4"/> : kind === 'sub-off' ? <path d="M8 3v10M4 9l4 4 4-4"/> : <path d="M2 5h12m-3-3 3 3-3 3M14 11H2m3-3-3 3 3 3"/>}
  </svg>;
  if (kind === 'card') return <svg className={s.incidentIcon} viewBox="0 0 16 16" aria-hidden="true">
    <rect x="4" y="2" width="8" height="12" rx="1" fill={label.includes('红') ? '#c12d3a' : '#efbf36'} stroke="#7c5414" strokeWidth=".7"/>
  </svg>;
  return <svg className={s.incidentIcon} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
    <circle cx="8" cy="8" r="5.8"/><path d="m6 6 4 4m0-4-4 4"/>
  </svg>;
}

export function MomentumBrush({ time, range, onRange, children }: {
  time: MatchTime; range: TimeRange | null; onRange: (range: TimeRange) => void; children: ReactNode;
}) {
  const start = useRef<{ x: number; position: number } | null>(null);
  const [preview, setPreview] = useState<TimeRange | null>(null);
  const position = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return snapToPlayingTime(time, time.end * (event.clientX - box.left - momentumGrid.left) / (box.width - momentumGrid.left - momentumGrid.right));
  };
  const highlight = preview ?? range;
  return <div className={s.momentumPlot}
    onPointerDown={event => {
      const box = event.currentTarget.getBoundingClientRect();
      if (event.pointerType === 'touch' || event.button !== 0 || event.clientX < box.left + momentumGrid.left || event.clientX > box.right - momentumGrid.right || event.clientY < box.top + momentumGrid.top || event.clientY > box.bottom - momentumGrid.bottom) return;
      start.current = { x: event.clientX, position: position(event) };
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={event => {
      if (start.current && Math.abs(event.clientX - start.current.x) >= 4) setPreview(ordered(start.current.position, position(event)));
    }}
    onPointerUp={event => {
      if (start.current && Math.abs(event.clientX - start.current.x) >= 4) onRange(ordered(start.current.position, position(event)));
      start.current = null;
      setPreview(null);
    }}
    onPointerCancel={() => { start.current = null; setPreview(null); }}>
    {children}
    {highlight && <div className={s.brushPlot} style={momentumGrid} aria-hidden="true"><i style={{ left: `${100 * highlight[0] / time.end}%`, width: `${100 * (highlight[1] - highlight[0]) / time.end}%` }} /></div>}
  </div>;
}

export function MatchTimeline({ time, range, markers, homeId, awayId, selected, onRange, onEvent }: {
  time: MatchTime; range: TimeRange | null; markers: Event[]; homeId: string; awayId: string;
  selected?: string;
  onRange: (range: TimeRange | null) => void; onEvent: (event: Event) => void;
}) {
  const [preview, setPreview] = useState<TimeRange | null>(null);
  const [eventFilter, setEventFilter] = useState<IncidentFilter>('all');
  const drag = useRef<{ endpoint: number | null; initial: TimeRange; start: number } | null>(null);
  const displayed = preview ?? range ?? [0, time.end];
  const percent = (position: number) => `${100 * position / time.end}%`;
  const position = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return snapToPlayingTime(time, (event.clientX - box.left) / box.width * time.end);
  };
  function update(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return null;
    const { endpoint, initial, start } = drag.current;
    const next = position(event);
    if (endpoint === 0) return [Math.min(next, initial[1]), initial[1]] as const;
    if (endpoint === 1) return [initial[0], Math.max(next, initial[0])] as const;
    return ordered(start, next);
  }
  function adjust(event: KeyboardEvent<HTMLButtonElement>, endpoint: number) {
    const value = displayed[endpoint];
    const step = event.shiftKey ? 5 * 60 : event.altKey ? 1 : 60;
    const target = { ArrowLeft: value - step, ArrowRight: value + step, ArrowDown: value - step, ArrowUp: value + step, Home: 0, End: time.end }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    const next = snapToPlayingTime(time, target);
    onRange(endpoint === 0 ? [Math.min(next, displayed[1]), displayed[1]] : [displayed[0], Math.max(next, displayed[0])]);
  }
  const shootout = markers.filter(event => event.period === 5 && (event.kind === 'shot' || event.goal));
  const matchMarkers = markers.filter(event => event.period < 5).sort((a, b) => a.period - b.period || a.minute - b.minute || a.second - b.second);
  const category = (event: Event) => event.kind === 'substitution' ? 'substitution' : incidentKind(event);
  const visibleMarkers = eventFilter === 'all' ? matchMarkers : matchMarkers.filter(event => category(event) === eventFilter);
  const side = (event: Event) => event.team_id === homeId ? 'home' : event.team_id === awayId ? 'away' : undefined;
  const label = (event: Event) => event.own_goal ? '乌龙球' : event.goal ? '进球' : event.penalty ? '点球未进' : event.label;
  const title = (event: Event) => [event.clock, label(event), event.player_name].filter(Boolean).join(' ');
  return <section className={s.timeline} aria-label="比赛时间轴">
    <div className={s.timelineTop}><div className={s.presets}>
      <button type="button" aria-pressed={!range} onClick={() => onRange(null)}>全场</button>
      {time.phases.map(phase => <button type="button" key={phase.id} aria-pressed={range?.[0] === phase.offset && range[1] === phase.offset + phase.duration} onClick={() => onRange([phase.offset, phase.offset + phase.duration])}>{phase.label}</button>)}
    </div><output aria-live="polite">{range ? `${formatMatchTime(time, range[0], true)} – ${formatMatchTime(time, range[1], true)}` : '全场比赛时段'}</output></div>
    {!!matchMarkers.length && <div className={s.incidentSpace} aria-label="时间轴上的关键事件">{visibleMarkers.map(event => {
      const position = axisPosition(time, event);
      return position !== null && <button key={event.id} type="button" tabIndex={-1} data-kind={incidentKind(event)} data-side={side(event)}
        aria-label={title(event)} aria-pressed={selected===event.id} data-in-range={inTimeRange(time, range, event)} title={title(event)} onClick={() => onEvent(event)} style={{ left: percent(position) }}>
        <IncidentIcon kind={incidentKind(event)} label={event.label}/>
      </button>;
    })}</div>}
    <div className={s.timelineScales} aria-hidden="true">
      {axisTicks(time).map(tick => <span key={tick.position} style={{ left: percent(tick.position) }}>{tick.label}</span>)}
    </div>
    <div className={s.timeTrack} onPointerDown={event => {
      if (event.button !== 0 || (event.pointerType === 'touch' && event.target === event.currentTarget)) return;
      const handle = (event.target as HTMLElement).closest<HTMLElement>('[data-endpoint]');
      drag.current = { endpoint: handle ? Number(handle.dataset.endpoint) : null, initial: displayed, start: position(event) };
      event.currentTarget.setPointerCapture(event.pointerId);
    }} onPointerMove={event => { const value = update(event); if (value) setPreview(value); }} onPointerUp={event => {
      const value = update(event);
      if (value) onRange(value);
      drag.current = null;
      setPreview(null);
    }} onPointerCancel={() => { drag.current = null; setPreview(null); }}>
      {time.phases.map(phase => {
        const regulation = Math.min((phase.end - phase.start) * 60, phase.duration);
        const end = phase.offset + phase.duration;
        return <div className={s.phase} data-extra={phase.id > 2} key={phase.id} style={{ left: percent(phase.offset), width: percent(phase.duration) }}>
          <span>{phase.label}</span>{phase.id > 2 && <span className={s.phaseShort}>{phase.id === 3 ? '加时上' : '加时下'}</span>}{phase.duration > regulation && <i style={{ width: `${100 * (phase.duration - regulation) / phase.duration}%` }} title={formatMatchTime(time, end, true)} />}
        </div>;
      })}
      <div className={s.timeSelection} aria-hidden="true" style={{ left: percent(displayed[0]), width: percent(displayed[1] - displayed[0]) }} />
      {displayed.map((value, endpoint) => <button type="button" className={s.timeHandle} key={endpoint} data-endpoint={endpoint} role="slider" aria-label={endpoint === 0 ? '区间起点' : '区间终点'} aria-valuemin={endpoint === 0 ? 0 : displayed[0]} aria-valuemax={endpoint === 0 ? displayed[1] : time.end} aria-valuenow={value} aria-valuetext={formatMatchTime(time, value, true)} style={{ left: percent(value) }} onKeyDown={event => adjust(event, endpoint)} />)}
    </div>
    {time.phases.some(phase => phase.duration > (phase.end - phase.start) * 60) && <div className={s.stoppage}>{time.phases.flatMap(phase => phase.duration > (phase.end - phase.start) * 60 ? [<span key={phase.id}>{formatMatchTime(time, phase.offset + phase.duration, true)}</span>] : [])}</div>}
    {!!matchMarkers.length && <>
      <div className={s.timelineEventFilters} role="group" aria-label="关键事件类型">
        <span>关键事件</span>
        {incidentFilters.filter(([type]) => type === 'all' || matchMarkers.some(event => category(event) === type))
          .map(([type, name]) => <button type="button" key={type} aria-pressed={eventFilter === type} onClick={() => setEventFilter(type)}>{name}</button>)}
      </div>
      <div className={s.incidents} aria-label="关键比赛事件">{visibleMarkers.map(event => <button key={event.id} type="button" data-side={side(event)} data-kind={incidentKind(event)}
        data-in-range={inTimeRange(time, range, event)} aria-pressed={selected===event.id} onClick={() => onEvent(event)} title={title(event)}>
        <IncidentIcon kind={incidentKind(event)} label={event.label}/><strong>{event.clock}</strong>{' '}{label(event)}
      </button>)}</div>
    </>}
    {!!shootout.length && <details className={s.shootout}><summary>点球大战</summary><div className={s.incidents}>{shootout.map((event, index) => <button key={event.id} type="button" onClick={() => onEvent(event)}>{index + 1}. {event.player_name ?? '球员未提供'} · {event.goal ? '罚进' : event.label}</button>)}</div></details>}
  </section>;
}
