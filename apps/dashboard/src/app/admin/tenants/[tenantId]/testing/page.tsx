import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Testing" };

export default async function TestingPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Browser text simulator</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Talk to this tenant&apos;s receptionist by typing as a caller. Uses the real
            conversation engine, tools, and guardrails against the approved
            configuration — turns and tool calls are persisted like a phone call, with
            no telephony or speech usage.
          </p>
          <Link href={`/admin/tenants/${tenantId}/testing/text`}>
            <Button size="sm">Open text simulator</Button>
          </Link>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Real phone test call</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Arrives with the telephony milestone. Pass/fail results feed the activation
            checklist automatically; the phone test can be waived with justification.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
