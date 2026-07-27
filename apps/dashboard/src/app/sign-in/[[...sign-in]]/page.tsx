import { SignIn } from "@clerk/nextjs";

// Rendered per-request: Clerk components need runtime configuration.
export const dynamic = "force-dynamic";

export default function SignInPage() {
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <SignIn />
    </main>
  );
}
