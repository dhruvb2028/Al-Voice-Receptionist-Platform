import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";

export const metadata = { title: "Team" };

export default function Page() {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Team</h1>
      <Card>
        <CardHeader>
          <CardTitle>Team</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Nothing here yet"
            description="Invite staff and manage their access. Team management arrives with a later milestone; your account manager can add teammates today."
          />
        </CardContent>
      </Card>
    </div>
  );
}
