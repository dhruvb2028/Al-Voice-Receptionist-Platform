import { SignIn } from "@clerk/nextjs";
import { Card } from "@/components/ui/card";

// Rendered per-request: Clerk components need runtime configuration.
export const dynamic = "force-dynamic";

export default function SignInPage() {
  const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      {clerkEnabled ? (
        <SignIn />
      ) : (
        <Card className="max-w-md p-8 text-center">
          <p className="text-sm font-medium">Sign-in is not configured</p>
          <p className="mt-1 text-sm text-muted-foreground">
            This environment has no authentication keys. Configure Clerk to enable
            sign-in.
          </p>
        </Card>
      )}
    </main>
  );
}
