import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";

export const metadata = { title: "Onboarding report" };

interface OnboardingReport {
  tenant_id: string;
  tenant_name: string;
  generated_at: string;
  title: string;
  sections: { heading: string; items: unknown[] }[];
}

/** Report items are either a plain line or a record of fields; render
 *  both without inventing a schema per report. */
function Item({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return <li>{value}</li>;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
    return (
      <li className="rounded-md border border-border p-2">
        <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-0.5 text-sm">
          {entries.map(([key, val]) => (
            <div key={key} className="contents">
              <dt className="text-muted-foreground">{key.replaceAll("_", " ")}</dt>
              <dd>{String(val)}</dd>
            </div>
          ))}
        </dl>
      </li>
    );
  }
  return <li>{String(value)}</li>;
}

export default async function OnboardingReportPage({
  params,
}: {
  params: Promise<{ tenantId: string; report: string }>;
}) {
  const { tenantId, report } = await params;
  const result = await apiFetch<OnboardingReport>(
    `/admin/tenants/${tenantId}/onboarding/reports/${report}`,
  );
  const back = `/admin/tenants/${tenantId}/onboarding`;

  if (!result.ok) {
    return <ErrorState description={result.message} retryHref={back} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link href={back} className="text-sm text-muted-foreground hover:text-foreground">
          ← Onboarding
        </Link>
        <h2 className="text-lg font-semibold tracking-tight">{result.data.title}</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        {result.data.tenant_name} · generated{" "}
        {new Date(result.data.generated_at).toLocaleString()}
      </p>

      {result.data.sections.map((section) => (
        <Card key={section.heading}>
          <CardHeader>
            <CardTitle>{section.heading}</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm">
              {section.items.map((item, index) => (
                <Item key={index} value={item} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
