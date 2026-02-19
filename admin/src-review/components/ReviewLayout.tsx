import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { ClipboardList, LogOut } from "lucide-react";
import { useReviewAuth } from "@review/hooks/use-review-auth";
import { Button } from "@/components/ui/button";

const NAV_ITEMS = [
  { to: "/queries", label: "Queries", icon: ClipboardList },
] as const;

export default function ReviewLayout() {
  const { logout, username } = useReviewAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-60 flex-col bg-sidebar">
        <div className="flex h-16 items-center px-5">
          <span className="text-base font-bold tracking-tight text-sidebar-primary">
            Evropuvefur <span className="font-normal text-sidebar-foreground/70">Review</span>
          </span>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-3">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-sm px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-bold border-l-[3px] border-sidebar-primary"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/20 hover:text-sidebar-foreground"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-1 p-3">
          {username && (
            <p className="px-4 py-1 text-xs text-sidebar-foreground/60">
              {username}
            </p>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start gap-3 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent/20"
            onClick={handleLogout}
          >
            <LogOut className="h-4 w-4" />
            Logout
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto bg-background">
        <div className="mx-auto max-w-6xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
