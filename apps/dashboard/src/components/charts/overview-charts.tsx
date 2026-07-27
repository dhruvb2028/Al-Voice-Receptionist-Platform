"use client";

import type { OverviewSeries } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BarList, ColumnChart, TrendChart } from "./primitives";

function shortDay(label: string): string {
  const parsed = new Date(label);
  return Number.isNaN(parsed.valueOf())
    ? label
    : parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function withShortLabels(points: { label: string; value: number }[]) {
  return points.map((p) => ({ ...p, label: shortDay(p.label) }));
}

export function OverviewCharts({ series }: { series: OverviewSeries }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Calls over time</CardTitle>
        </CardHeader>
        <CardContent>
          <TrendChart
            points={withShortLabels(series.calls_over_time)}
            caption="Calls answered per day"
            valueLabel="Calls"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bookings over time</CardTitle>
        </CardHeader>
        <CardContent>
          <TrendChart
            points={withShortLabels(series.bookings_over_time)}
            caption="Confirmed bookings per day"
            valueLabel="Bookings"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Call outcomes</CardTitle>
        </CardHeader>
        <CardContent>
          <BarList
            points={series.outcomes}
            caption="Calls by outcome"
            valueLabel="Calls"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Calls by hour</CardTitle>
        </CardHeader>
        <CardContent>
          <ColumnChart
            points={series.calls_by_hour}
            caption="Calls answered by hour of day"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Message urgency</CardTitle>
        </CardHeader>
        <CardContent>
          <BarList
            points={series.urgency_distribution}
            caption="Messages by urgency"
            valueLabel="Messages"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Response latency</CardTitle>
        </CardHeader>
        <CardContent>
          <TrendChart
            points={withShortLabels(series.latency_trend)}
            caption="Median response latency per day, in milliseconds"
            valueLabel="Median latency (ms)"
            format={(v) => `${Math.round(v)} ms`}
          />
        </CardContent>
      </Card>
    </div>
  );
}

export function UsageChart({ series }: { series: OverviewSeries }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Minutes used per day</CardTitle>
      </CardHeader>
      <CardContent>
        <TrendChart
          points={withShortLabels(series.usage_trend)}
          caption="Call minutes recorded per day"
          valueLabel="Minutes"
          format={(v) => `${v.toLocaleString()} min`}
        />
      </CardContent>
    </Card>
  );
}
