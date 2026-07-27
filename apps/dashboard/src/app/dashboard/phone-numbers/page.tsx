import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Phone Numbers" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Phone Numbers</h1>
      <Card>
        <CardHeader>
          <CardTitle>Phone numbers</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="The numbers your receptionist answers. Number assignment is managed by your account manager and appears here once telephony is connected."
          />
        </CardContent>
      </Card>
    </div>
  );
}
