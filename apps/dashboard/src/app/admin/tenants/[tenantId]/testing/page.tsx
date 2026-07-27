import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Testing" };

export default function TestingPage() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Test calls</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          The browser text simulator arrives with the conversation-engine milestone;
          real phone test calls arrive with telephony. Their pass/fail results feed the
          activation checklist automatically.
        </p>
      </CardContent>
    </Card>
  );
}
