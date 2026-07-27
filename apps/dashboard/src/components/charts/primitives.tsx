"use client";

/**
 * Chart primitives: one hue, thin marks, recessive axes.
 *
 * Every chart here plots a single series, so magnitude is the job and a
 * sequential single-hue encoding is correct — no categorical palette, no
 * legend (the card title names the series). Each chart ships a
 * screen-reader table so identity is never carried by the mark alone.
 */

import { motion, useReducedMotion } from "framer-motion";
import { useId, useState } from "react";

export interface Point {
  label: string;
  value: number;
}

const MARK = "var(--color-primary)";
const GRID = "var(--color-border)";
const AXIS_INK = "var(--color-muted-foreground)";

/** Screen-reader-only table — the accessible equivalent of the marks.
 *
 *  The wrapper carries `sr-only`, not the table: table layout ignores a
 *  1px width and expands to its content, so `sr-only` on the table
 *  itself leaves it visible on the page.
 */
function DataTable({
  caption,
  points,
  valueLabel,
  format,
}: {
  caption: string;
  points: Point[];
  valueLabel: string;
  format: (value: number) => string;
}) {
  return (
    <div className="sr-only">
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Period</th>
            <th scope="col">{valueLabel}</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.label}>
              <th scope="row">{point.label}</th>
              <td>{format(point.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Tooltip({
  x,
  label,
  value,
}: {
  x: number;
  label: string;
  value: string;
}) {
  return (
    <div
      className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border border-border bg-card px-2 py-1 text-xs shadow-sm"
      style={{ left: `${x}%` }}
      role="status"
    >
      <span className="font-medium">{value}</span>
      <span className="ml-1.5 text-muted-foreground">{label}</span>
    </div>
  );
}

const EMPTY = (
  <p className="py-8 text-center text-sm text-muted-foreground">
    Not enough data to chart yet.
  </p>
);

/** Trend over time. Crosshair + tooltip follow the pointer. */
export function TrendChart({
  points,
  caption,
  valueLabel = "Value",
  format = (v: number) => v.toLocaleString(),
  height = 140,
}: {
  points: Point[];
  caption: string;
  valueLabel?: string;
  format?: (value: number) => string;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const reduceMotion = useReducedMotion();
  const gradientId = useId();

  if (points.length < 2) return EMPTY;

  const max = Math.max(...points.map((p) => p.value), 1);
  const stepX = 100 / (points.length - 1);
  const toY = (value: number) => 100 - (value / max) * 100;
  const coords = points.map((p, i) => [i * stepX, toY(p.value)] as const);
  const line = coords.map(([x, y]) => `${x},${y}`).join(" ");
  const area = `0,100 ${line} 100,100`;
  const activePoint = active === null ? null : points[active];

  return (
    <figure className="m-0">
      <div
        className="relative"
        onPointerLeave={() => setActive(null)}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = (event.clientX - rect.left) / rect.width;
          setActive(
            Math.min(points.length - 1, Math.max(0, Math.round(ratio * (points.length - 1)))),
          );
        }}
      >
        {activePoint && active !== null && (
          <Tooltip
            x={Math.min(92, Math.max(8, active * stepX))}
            label={activePoint.label}
            value={format(activePoint.value)}
          />
        )}
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          style={{ height }}
          className="w-full"
          role="img"
          aria-label={caption}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={MARK} stopOpacity="0.18" />
              <stop offset="100%" stopColor={MARK} stopOpacity="0.01" />
            </linearGradient>
          </defs>
          {[0, 50, 100].map((y) => (
            <line
              key={y}
              x1="0"
              y1={y}
              x2="100"
              y2={y}
              stroke={GRID}
              strokeWidth="0.5"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <polygon points={area} fill={`url(#${gradientId})`} />
          {/* Geometry is static; motion only fades the mark in, so the
              chart is fully readable even if animation never runs. */}
          <motion.polyline
            points={line}
            fill="none"
            stroke={MARK}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
            initial={reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.35, ease: "easeOut" }}
          />
          {active !== null && (
            <>
              <line
                x1={active * stepX}
                y1="0"
                x2={active * stepX}
                y2="100"
                stroke={AXIS_INK}
                strokeWidth="1"
                strokeDasharray="3 3"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={active * stepX}
                cy={toY(points[active].value)}
                r="4"
                fill={MARK}
                stroke="var(--color-card)"
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}
        </svg>
      </div>
      <figcaption className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{points[0].label}</span>
        <span>{points[points.length - 1].label}</span>
      </figcaption>
      <DataTable
        caption={caption}
        points={points}
        valueLabel={valueLabel}
        format={format}
      />
    </figure>
  );
}

/** Magnitude across ordered bins (hours of the day). */
export function ColumnChart({
  points,
  caption,
  valueLabel = "Calls",
  format = (v: number) => v.toLocaleString(),
  height = 120,
}: {
  points: Point[];
  caption: string;
  valueLabel?: string;
  format?: (value: number) => string;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const reduceMotion = useReducedMotion();
  const max = Math.max(...points.map((p) => p.value), 1);

  if (points.length === 0) return EMPTY;

  return (
    <figure className="m-0">
      <div className="relative" style={{ height }}>
        {active !== null && (
          <Tooltip
            x={Math.min(92, Math.max(8, ((active + 0.5) / points.length) * 100))}
            label={`${points[active].label}:00`}
            value={format(points[active].value)}
          />
        )}
        {/* 2px surface gaps keep adjacent bars from fusing. Height comes
            from style, not the animation, so bars are correct even when
            animation is skipped or never ticks. */}
        <div className="flex h-full items-end gap-0.5">
          {points.map((point, index) => (
            <motion.div
              key={point.label}
              className="flex-1 rounded-t bg-primary"
              style={{
                height: `${(point.value / max) * 100}%`,
                minHeight: point.value > 0 ? 2 : 0,
              }}
              initial={reduceMotion ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, delay: Math.min(index * 0.01, 0.2) }}
              onPointerEnter={() => setActive(index)}
              onPointerLeave={() => setActive(null)}
            />
          ))}
        </div>
      </div>
      <figcaption className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>00:00</span>
        <span>12:00</span>
        <span>23:00</span>
      </figcaption>
      <DataTable
        caption={caption}
        points={points}
        valueLabel={valueLabel}
        format={format}
      />
    </figure>
  );
}

/** Named categories compared by magnitude, each directly labelled. */
export function BarList({
  points,
  caption,
  valueLabel = "Count",
  format = (v: number) => v.toLocaleString(),
}: {
  points: Point[];
  caption: string;
  valueLabel?: string;
  format?: (value: number) => string;
}) {
  const reduceMotion = useReducedMotion();
  const max = Math.max(...points.map((p) => p.value), 1);

  if (points.length === 0) return EMPTY;

  return (
    <figure className="m-0 space-y-2">
      {points.map((point, index) => (
        <div key={point.label}>
          <div className="flex justify-between text-sm">
            <span>{point.label.replaceAll("_", " ")}</span>
            <span className="tabular-nums text-muted-foreground">
              {format(point.value)}
            </span>
          </div>
          <div className="mt-1 h-2 rounded-full bg-muted">
            <motion.div
              className="h-2 rounded-full bg-primary"
              style={{ width: `${(point.value / max) * 100}%` }}
              initial={reduceMotion ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, delay: Math.min(index * 0.04, 0.2) }}
            />
          </div>
        </div>
      ))}
      <DataTable
        caption={caption}
        points={points}
        valueLabel={valueLabel}
        format={format}
      />
    </figure>
  );
}
