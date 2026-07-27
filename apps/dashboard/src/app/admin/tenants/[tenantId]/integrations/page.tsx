import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Integrations" };

export default function IntegrationsPage() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Integrations</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Google Calendar connection and phone-number assignment arrive with the
          calendar-integration and telephony milestones. Connection health will appear
          here once linked.
        </p>
      </CardContent>
    </Card>
  );
}
