import { apiFetch } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricCard } from "@/components/ui/metric-card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Overview" };

interface TenantResponse {
  id: string;
  name: string;
  status: string;
  timezone: string;
}

export default async function OverviewPage() {
  const tenant = await apiFetch<TenantResponse>("/tenant");

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          {tenant.ok
            ? `Live activity for ${tenant.data.name}.`
            : "Live activity for your business."}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Calls today" value="—" hint="Appears once calls begin" />
        <MetricCard label="Bookings this week" value="—" hint="Appears once calls begin" />
        <MetricCard label="Messages waiting" value="—" hint="Appears once calls begin" />
        <MetricCard label="Answer rate" value="—" hint="Appears once calls begin" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent activity</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="No activity yet"
            description="Once your receptionist starts answering calls, bookings, messages, and call summaries will appear here in real time."
          />
        </CardContent>
      </Card>
    </div>
  );
}
