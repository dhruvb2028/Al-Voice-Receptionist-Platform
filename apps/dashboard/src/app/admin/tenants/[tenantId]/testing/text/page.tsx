import Link from "next/link";
import { SimulatorConsole } from "./simulator-console";

export const metadata = { title: "Text simulator" };

export default async function TextSimulatorPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link
            href={`/admin/tenants/${tenantId}/testing`}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← Testing
          </Link>
          <h2 className="mt-1 text-lg font-semibold tracking-tight">
            Browser text simulator
          </h2>
          <p className="text-sm text-muted-foreground">
            Runs the real conversation engine and tools against this tenant&apos;s
            approved configuration — no telephony or speech usage is consumed.
          </p>
        </div>
        <span className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800">
          Test environment — not a production call
        </span>
      </div>
      <SimulatorConsole tenantId={tenantId} />
    </div>
  );
}
