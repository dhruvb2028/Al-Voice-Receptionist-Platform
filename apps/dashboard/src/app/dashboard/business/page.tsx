import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Business Configuration" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Business Configuration</h1>
      <Card>
        <CardHeader>
          <CardTitle>Business configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="Services, prices, business hours, and service area. Editing arrives with the business-configuration milestone; changes are currently handled by your account manager."
          />
        </CardContent>
      </Card>
    </div>
  );
}
