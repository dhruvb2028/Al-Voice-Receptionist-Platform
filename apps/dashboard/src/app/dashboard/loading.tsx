import { PageSkeleton } from "@/components/ui/skeleton";

export default function DashboardLoading() {
  return (
    <div className="mx-auto max-w-6xl">
      <PageSkeleton />
    </div>
  );
}
