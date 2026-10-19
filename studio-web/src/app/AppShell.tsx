// Application frame: top bar (brand, search / command palette, AI status, jobs, notifications, theme,
// profile) + collapsible side navigation + animated page outlet.
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "@ui5/webcomponents-react";
import { attachBoot } from "@ui5/webcomponents-base/dist/Boot.js";
import { getTheme, setTheme } from "@ui5/webcomponents-base/dist/config/Theme.js";
import { useMe } from "./MeProvider";
import { CommandPalette } from "../components/shell/CommandPalette";
import { JobsMenu, NotificationsMenu, ProfileMenu, ThemeToggle } from "../components/shell/TopBarMenus";
import { ToastProvider } from "../components/shell/Toasts";
import { useApi } from "../components/shell/useApi";
import { NoRolePage } from "../components/shell/Access";
import "../components/shell/shell.css";

interface NavItem { path: string; text: string; icon: string; badge?: number; group?: string; perm?: string[] }

export function AppShell() {
  const { me, dark, can } = useMe();
  const loc = useLocation();
  const nav = useNavigate();
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("evoke.nav") === "1"; } catch { return false; } });
  const [palette, setPalette] = useState(false);
  const { data: appr } = useApi<{ approvals: any[] }>("api/approvals?status=pending", { poll: 60000 });
  const pending = (appr?.approvals || []).filter((a) => a.can_decide).length;

  const NAV: NavItem[] = [
    { path: "/", text: "Dashboard", icon: "home", group: "Workspace" },
    { path: "/studio", text: "New application", icon: "add-document", group: "Workspace", perm: ["project.create"] },
    { path: "/adopt", text: "Adopt project", icon: "inbox", group: "Workspace", perm: ["project.create"] },
    { path: "/projects", text: "Projects", icon: "folder-blank", group: "Workspace" },
    { path: "/approvals", text: "Approvals", icon: "approvals", badge: pending, group: "Governance", perm: ["approval.decide", "approval.request"] },
    { path: "/audit", text: "Audit log", icon: "history", group: "Governance" },
    { path: "/admin/users", text: "Users & roles", icon: "group", group: "Governance", perm: ["admin.users"] },
    { path: "/settings", text: "Settings", icon: "settings", group: "Governance" },
  ].filter((n) => !n.perm || !me || n.perm.some((p) => can(p)));
  const active = NAV.slice().reverse().find((n) =>
    n.path === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.path) ||
      (n.path === "/projects" && (loc.pathname.startsWith("/compare") || loc.pathname.startsWith("/deploy"))));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { try { localStorage.setItem("evoke.nav", collapsed ? "1" : "0"); } catch { /* ignore */ } }, [collapsed]);
  // UI5's boot applies its configured (light) theme after the first render — re-apply the saved one.
  useEffect(() => {
    // (getTheme() may already report the saved theme while the light one is rendered, so force a switch)
    attachBoot(async () => {
      const want = dark ? "sap_horizon_dark" : "sap_horizon";
      if (document.documentElement.getAttribute("data-sap-theme") === want) return;
      if (getTheme() === want) await setTheme(want === "sap_horizon" ? "sap_horizon_dark" : "sap_horizon");
      await setTheme(want);
    });
  }, []); // eslint-disable-line

  if (me && me.user.source === "xsuaa" && me.user.roles.length === 0) return <NoRolePage />;
  let lastGroup = "";
  return (
    <ToastProvider>
      <div className="ev-shell">
        <header className="ev-tb">
          <button className="ev-tb__btn ev-tb__menu" onClick={() => setCollapsed(!collapsed)} title={collapsed ? "Expand navigation" : "Collapse navigation"}>
            <Icon name="menu2" />
          </button>
          <div className="ev-tb__brand" onClick={() => nav("/")}>
            <img src="./evoke_logo.png" alt="" />
            <span className="ev-tb__name">Evoke</span>
            <span className="ev-tb__product">Fiori App Studio</span>
          </div>
          <button className="ev-tb__search" onClick={() => setPalette(true)}>
            <Icon name="search" /><span>Search or jump to…</span><kbd>Ctrl K</kbd>
          </button>
          <div className="ev-tb__right">
            <span className={"ev-ai-chip" + (me?.ai.available ? " is-on" : "")}
              title={me?.ai.available ? `SAP AI Core · ${me.ai.model}` : (me?.ai.error || "No AI model — rule-based design")}>
              <span className="ev-ai-chip__dot" />{me?.ai.available ? (me.ai.model || "AI").replace(/^anthropic--/, "") : "Rule-based"}
            </span>
            <JobsMenu />
            <NotificationsMenu />
            <ThemeToggle />
            <ProfileMenu />
          </div>
        </header>
        <div className="ev-body">
          <motion.nav className={"ev-nav" + (collapsed ? " is-collapsed" : "")} initial={false} animate={{ width: collapsed ? 64 : 236 }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}>
            {NAV.map((n) => {
              const head = !collapsed && n.group !== lastGroup ? (lastGroup = n.group || "") : "";
              const isActive = active?.path === n.path;
              return (
                <div key={n.path}>
                  {head && <div className="ev-nav__group">{head}</div>}
                  <button className={"ev-nav__item" + (isActive ? " is-active" : "")} onClick={() => nav(n.path)} title={collapsed ? n.text : undefined}>
                    {isActive && <motion.span layoutId="nav-active" className="ev-nav__active" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                    <Icon name={n.icon} className="ev-nav__icon" />
                    {!collapsed && <span className="ev-nav__text">{n.text}</span>}
                    {!!n.badge && <span className={"ev-nav__badge" + (collapsed ? " is-dot" : "")}>{collapsed ? "" : n.badge}</span>}
                  </button>
                </div>
              );
            })}
            <div className="ev-nav__spacer" />
            {!collapsed && (
              <div className="ev-nav__foot">
                <Icon name={me?.store.kind === "hana" ? "database" : "save"} />
                <span>{me?.store.kind === "hana" ? "SAP HANA Cloud workspace" : "Local workspace"}</span>
              </div>
            )}
          </motion.nav>
          <main className="ev-main">
            <AnimatePresence mode="wait">
              <motion.div key={loc.pathname.split("/")[1] || "home"} className="ev-page"
                initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.22, ease: "easeOut" }}>
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
        <CommandPalette open={palette} onClose={() => setPalette(false)} />
      </div>
    </ToastProvider>
  );
}
