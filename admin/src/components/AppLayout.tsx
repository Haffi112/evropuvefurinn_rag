import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  Activity,
  ClipboardCheck,
  FileText,
  History,
  LayoutDashboard,
  LogOut,
  Terminal,
  Users,
} from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/articles", label: "Articles", icon: FileText },
  { to: "/query-log", label: "Query Log", icon: History },
  { to: "/playground", label: "Playground", icon: Terminal },
  { to: "/system", label: "System", icon: Activity },
  { to: "/reviewers", label: "Reviewers", icon: Users },
  { to: "/reviews", label: "Reviews", icon: ClipboardCheck },
] as const;

export default function AppLayout() {
  const { logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="flex w-60 flex-col bg-sidebar">
        <div className="flex h-16 items-center px-5">
          <Link to="/dashboard" className="text-base font-bold tracking-tight text-sidebar-primary">
            Evropuvefur <span className="font-normal text-sidebar-foreground/70">Admin</span>
          </Link>
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
        <div className="p-3">
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

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-background">
        <div className="mx-auto max-w-6xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
