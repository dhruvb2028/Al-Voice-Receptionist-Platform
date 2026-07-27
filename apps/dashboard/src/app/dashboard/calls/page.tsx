import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Calls" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Calls</h1>
      <Card>
        <CardHeader>
          <CardTitle>Call history</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="Every answered call — with transcript, recording, and outcome — appears here once your receptionist is live. Arrives with the calls milestone."
          />
        </CardContent>
      </Card>
    </div>
  );
}
