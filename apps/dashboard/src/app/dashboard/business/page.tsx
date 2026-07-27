import { apiFetch } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/ui/states";

export const metadata = { title: "Business Configuration" };

interface ConfigurationView {
  greeting: string | null;
  business_phone: string | null;
  address: string | null;
  website: string | null;
  timezone: string | null;
  services: Array<{
    name: string;
    description: string | null;
    duration_minutes: number;
    category: string | null;
  }>;
  hours: Array<{
    weekday: number;
    closed: boolean;
    opens_at: string | null;
    closes_at: string | null;
  }>;
  configuration_version: number | null;
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function formatTime(value: string | null): string {
  if (!value) return "";
  const [hours, minutes] = value.split(":");
  const hour = Number(hours);
  const suffix = hour >= 12 ? "PM" : "AM";
  const display = hour % 12 === 0 ? 12 : hour % 12;
  return `${display}:${minutes} ${suffix}`;
}

export default async function BusinessConfigurationPage() {
  const result = await apiFetch<ConfigurationView>("/tenant/configuration");

  if (!result.ok) {
    return (
      <div className="mx-auto max-w-6xl space-y-4">
        <h1 className="text-xl font-semibold tracking-tight">Business Configuration</h1>
        <ErrorState description={result.message} retryHref="/dashboard/business" />
      </div>
    );
  }

  const config = result.data;
  const configured = Boolean(config.greeting || config.services.length);

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Business Configuration</h1>
        <p className="text-sm text-muted-foreground">
          What your receptionist knows about your business. To request changes, contact
          your account manager — updates go through a review before going live.
        </p>
      </div>

      {!configured ? (
        <EmptyState
          title="Not configured yet"
          description="Your account manager is setting up your receptionist. Your services, hours, and greeting will appear here once configured."
        />
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Greeting</CardTitle>
            </CardHeader>
            <CardContent>
              <blockquote className="border-l-2 border-primary pl-3 text-sm italic">
                {config.greeting ?? "Not set"}
              </blockquote>
            </CardContent>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Services ({config.services.length})</CardTitle>
              </CardHeader>
              <CardContent>
                {config.services.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No services configured.</p>
                ) : (
                  <ul className="divide-y divide-border">
                    {config.services.map((service) => (
                      <li key={service.name} className="py-2 first:pt-0 last:pb-0">
                        <p className="text-sm font-medium">{service.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {service.duration_minutes} min
                          {service.category ? ` · ${service.category}` : ""}
                          {service.description ? ` — ${service.description}` : ""}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Business hours</CardTitle>
              </CardHeader>
              <CardContent>
                {config.hours.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No hours configured.</p>
                ) : (
                  <dl className="space-y-1.5">
                    {config.hours.map((day) => (
                      <div key={day.weekday} className="flex justify-between text-sm">
                        <dt className="font-medium">{WEEKDAYS[day.weekday]}</dt>
                        <dd className="text-muted-foreground">
                          {day.closed
                            ? "Closed"
                            : `${formatTime(day.opens_at)} – ${formatTime(day.closes_at)}`}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Contact details</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    Phone
                  </dt>
                  <dd className="mt-0.5 text-sm">{config.business_phone ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    Address
                  </dt>
                  <dd className="mt-0.5 text-sm">{config.address ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    Website
                  </dt>
                  <dd className="mt-0.5 text-sm">{config.website ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    Timezone
                  </dt>
                  <dd className="mt-0.5 text-sm">{config.timezone ?? "—"}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
