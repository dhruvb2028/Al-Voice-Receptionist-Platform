import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Configuration" };

export default function ConfigurationPage() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Receptionist configuration</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Greeting, persona, voice, services, prices, hours, and service-area editing
          arrives with the business-configuration milestone. Until then, configuration
          changes are applied by the platform operator directly.
        </p>
      </CardContent>
    </Card>
  );
}
