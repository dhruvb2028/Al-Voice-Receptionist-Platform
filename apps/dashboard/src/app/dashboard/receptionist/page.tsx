import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "AI Receptionist" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">AI Receptionist</h1>
      <Card>
        <CardHeader>
          <CardTitle>Your receptionist</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="Greeting, voice, and personality settings for your AI receptionist. Configuration editing arrives with the business-configuration milestone."
          />
        </CardContent>
      </Card>
    </div>
  );
}
