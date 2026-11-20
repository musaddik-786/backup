import { cn } from "@/lib/utils";

interface StatCardProps {
  icon: React.ElementType;
  value: number | string;
  label: string;
  iconColorClass: string;
  iconBgClass: string;
}

export function StatCard({ icon: Icon, value, label, iconColorClass, iconBgClass }: StatCardProps) {
  return (
    <div className="flex items-center gap-4 rounded-lg border border-border bg-card p-5 shadow-sm">
      <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-lg", iconBgClass)}>
        <Icon className={cn("h-5 w-5", iconColorClass)} />
      </div>
      <div>
        <div className="text-2xl font-bold leading-tight">{value}</div>
        <div className="text-sm text-muted-foreground">{label}</div>
      </div>
    </div>
  );
}
