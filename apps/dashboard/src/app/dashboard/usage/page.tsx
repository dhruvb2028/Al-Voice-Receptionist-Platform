import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Usage" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Usage</h1>
      <Card>
        <CardHeader>
          <CardTitle>Usage</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="Monthly call volume and minutes for your business. Populates automatically once calls begin."
          />
        </CardContent>
      </Card>
    </div>
  );
}
