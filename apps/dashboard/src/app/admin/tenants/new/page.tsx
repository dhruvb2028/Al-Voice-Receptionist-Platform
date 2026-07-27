import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NewTenantForm } from "./tenant-form";

export const metadata = { title: "New tenant" };

export default function NewTenantPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div>
        <Link
          href="/admin/tenants"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Tenants
        </Link>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Create tenant</h1>
        <p className="text-sm text-muted-foreground">
          Creates the tenant record, its authentication organization, and sends the
          owner invitation. The tenant starts in <strong>onboarding</strong> — nothing
          goes live until the activation checklist passes.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Business details</CardTitle>
        </CardHeader>
        <CardContent>
          <NewTenantForm />
        </CardContent>
      </Card>
    </div>
  );
}
