// All routes. Pages are lazy-loaded; each page folder is owned by one feature area.
import React, { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { BusyIndicator } from "@ui5/webcomponents-react";
import { AppShell } from "./AppShell";
import { RequirePerm } from "../components/shell/Access";

// After a new deployment, an open tab may ask for page files that no longer exist (new file
// names) — reload once to pick up the new version instead of showing a blank page.
const lazyPage = (load: () => Promise<any>) => lazy(() => load().then((m) => {
  try { sessionStorage.removeItem("evoke.reloaded"); } catch { /* ignore */ }
  return m;
}).catch((e) => {
  let again = false;
  try { again = !sessionStorage.getItem("evoke.reloaded"); sessionStorage.setItem("evoke.reloaded", "1"); } catch { /* ignore */ }
  if (again) { window.location.reload(); return new Promise(() => undefined); }
  throw e;
}));

const Dashboard = lazyPage(() => import("../pages/dashboard/DashboardPage"));
const Studio = lazyPage(() => import("../pages/studio/StudioPage"));
const Projects = lazyPage(() => import("../pages/projects/ProjectsPage"));
const ProjectDetail = lazyPage(() => import("../pages/projects/ProjectDetailPage"));
const Compare = lazyPage(() => import("../pages/projects/ComparePage"));
const Deploy = lazyPage(() => import("../pages/deploy/DeployPage"));
const Approvals = lazyPage(() => import("../pages/approvals/ApprovalsPage"));
const Audit = lazyPage(() => import("../pages/audit/AuditPage"));
const Settings = lazyPage(() => import("../pages/settings/SettingsPage"));
const Users = lazyPage(() => import("../pages/admin/UsersPage"));

const page = (el: React.ReactNode) => (
  <Suspense fallback={<div className="ev-page-loading"><BusyIndicator active size="L" /></div>}>{el}</Suspense>
);

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={page(<Dashboard />)} />
        <Route path="studio" element={page(<RequirePerm perm="project.create" what="Creating applications"><Studio /></RequirePerm>)} />
        <Route path="studio/:step" element={page(<RequirePerm perm="project.create" what="Creating applications"><Studio /></RequirePerm>)} />
        <Route path="projects" element={page(<Projects />)} />
        <Route path="projects/:pid" element={page(<ProjectDetail />)} />
        <Route path="compare/:a/:b" element={page(<Compare />)} />
        <Route path="deploy/:versionId" element={page(<RequirePerm perm={["deploy.dev", "deploy.test", "deploy.prod", "approval.decide"]} what="The release console"><Deploy /></RequirePerm>)} />
        <Route path="approvals" element={page(<RequirePerm perm={["approval.decide", "approval.request"]} what="Approvals"><Approvals /></RequirePerm>)} />
        <Route path="audit" element={page(<Audit />)} />
        <Route path="settings" element={page(<Settings />)} />
        <Route path="admin/users" element={page(<RequirePerm perm="admin.users" what="User management"><Users /></RequirePerm>)} />
        <Route path="*" element={page(<Dashboard />)} />
      </Route>
    </Routes>
  );
}
