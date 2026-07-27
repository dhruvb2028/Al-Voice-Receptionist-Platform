import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Settings" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      <Card>
        <CardHeader>
          <CardTitle>Settings</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="Notification preferences and account settings arrive with the notifications milestone."
          />
        </CardContent>
      </Card>
    </div>
  );
}
