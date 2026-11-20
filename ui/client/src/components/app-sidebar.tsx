import { useState } from "react";
import { LayoutGrid, Pencil, Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  label: string;
  icon: React.ElementType;
}

const navItems: NavItem[] = [
  { label: "AI Insights", icon: LayoutGrid },
];

interface AppSidebarProps {
  activeItem?: string;
}

const ORG_KEY = "sidebar.orgName";
const DEFAULT_ORG = "Jackson Insurance";

function loadOrgName(): string {
  try {
    return localStorage.getItem(ORG_KEY) || DEFAULT_ORG;
  } catch {
    return DEFAULT_ORG;
  }
}

export function AppSidebar({ activeItem = "AI Insights" }: AppSidebarProps) {
  const [orgName, setOrgName] = useState(loadOrgName);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(orgName);

  const startEdit = () => {
    setDraft(orgName);
    setEditing(true);
  };
  const save = () => {
    const next = draft.trim() || DEFAULT_ORG;
    setOrgName(next);
    setEditing(false);
    try {
      localStorage.setItem(ORG_KEY, next);
    } catch {
      /* storage unavailable: keep the name for this session only */
    }
  };

  return (
    <aside className="flex h-screen w-64 shrink-0 flex-col bg-sidebar text-sidebar-foreground border-r border-sidebar-border">
      <div className="flex items-center justify-between px-5 py-5">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary font-bold text-white">
            H
          </div>
          <span className="text-lg font-bold">
            Hexaware <span className="text-primary">Ops</span>
          </span>
        </div>
      </div>
      <p className="px-5 text-xs text-sidebar-foreground/50 -mt-3 mb-4">
        AI-Led Guidewire Support
      </p>

      <div className="px-4 mb-4">
        <div className="flex items-center justify-between rounded-md border border-sidebar-border bg-white/5 px-3 py-2 text-sm">
          {editing ? (
            <input
              autoFocus
              value={draft}
              maxLength={40}
              aria-label="Organization name"
              onChange={(e) => setDraft(e.target.value)}
              onBlur={save}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setEditing(false);
              }}
              className="min-w-0 flex-1 bg-transparent outline-none"
            />
          ) : (
            <span className="truncate">{orgName}</span>
          )}
          <button
            type="button"
            aria-label={editing ? "Save name" : "Edit name"}
            onMouseDown={(e) => e.preventDefault()}
            onClick={editing ? save : startEdit}
            className="ml-2 shrink-0 rounded p-0.5 text-sidebar-foreground/50 hover:text-sidebar-foreground"
          >
            {editing ? <Check className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      <div className="px-5 py-2">
        <span className="text-xs font-semibold tracking-wider text-sidebar-foreground/40">
          WORKSPACE
        </span>
      </div>

      <nav className="flex-1 px-3 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = item.label === activeItem;
          const isDisabled = item.label !== "AI Insights";
          return (
            <button
              key={item.label}
              disabled={isDisabled}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
                active
                  ? "bg-primary text-primary-foreground"
                  : isDisabled
                    ? "text-sidebar-foreground/30 cursor-not-allowed"
                    : "text-sidebar-foreground/80 hover:bg-white/5 hover:text-sidebar-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
