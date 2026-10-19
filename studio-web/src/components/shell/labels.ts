// Human-readable texts + icons for audit actions, modes, statuses.
import type { Tone } from "../ui";

export function actionInfo(action: string): { text: string; icon: string; tone: Tone } {
  const a = action || "";
  const map: [RegExp, string, string, Tone][] = [
    [/^project\.create/, "created the project", "add-document", "brand"],
    [/^project\.delete/, "deleted the project", "delete", "bad"],
    [/^project\.update/, "edited the project details", "edit", "neutral"],
    [/^version\.delete/, "deleted a version", "delete", "bad"],
    [/^deploy\.undeployed/, "removed the app from Cloud Foundry", "delete", "warn"],
    [/^deploy\.undeploy-failed/, "failed to remove the app from Cloud Foundry", "error", "bad"],
    [/^project\.rename/, "renamed the project", "edit", "neutral"],
    [/^project\.archive/, "archived / restored the project", "inbox", "neutral"],
    [/^project\.duplicate/, "duplicated the project", "duplicate", "neutral"],
    [/^project\.import/, "imported the project", "upload", "brand"],
    [/^project\.adopt/, "adopted an existing project", "inbox", "brand"],
    [/^robot\./, "made an AI change", "ai", "brand"],
    [/^project\.export/, "exported the project", "download", "neutral"],
    [/^member\./, "changed project members", "group", "neutral"],
    [/^version\.save/, "saved a new version", "save", "brand"],
    [/^version\.validated/, "validated a version", "shield", "good"],
    [/^version\.restore/, "opened a version", "past", "neutral"],
    [/^validation\.run/, "ran a validation", "shield", "info"],
    [/^app\.generate/, "generated the application", "source-code", "brand"],
    [/^approval\.request/, "requested a release approval", "approvals", "warn"],
    [/^approval\.approve/, "approved a release", "accept", "good"],
    [/^approval\.reject/, "rejected a release", "decline", "bad"],
    [/^approval\./, "decided an approval", "approvals", "info"],
    [/^deploy\.succeeded/, "deployed successfully", "paper-plane", "good"],
    [/^deploy\.failed/, "had a failed deployment", "error", "bad"],
    [/^deploy\./, "deployed", "paper-plane", "info"],
    [/^sign-in/, "signed in", "employee", "neutral"],
    [/^user\.roles/, "changed user roles", "key-user-settings", "warn"],
  ];
  for (const [re, text, icon, tone] of map) if (re.test(a)) return { text, icon, tone };
  return { text: a.replace(/[._]/g, " "), icon: "activity-2", tone: "neutral" };
}

export const MODE_INFO: Record<string, { label: string; icon: string; tone: Tone }> = {
  sap: { label: "SAP service", icon: "connected", tone: "info" },
  standalone: { label: "New app + CAP", icon: "database", tone: "brand" },
  import: { label: "Imported", icon: "upload", tone: "neutral" },
  adopted: { label: "Adopted", icon: "inbox", tone: "info" },
};

export function statusTone(s: string): Tone {
  return ({ succeeded: "good", passed: "good", approved: "good", done: "good", failed: "bad", rejected: "bad",
    error: "bad", pending: "warn", running: "info", skipped: "neutral", not_tested: "neutral", warning: "warn",
    imported: "neutral", undeployed: "warn", "undeploy-failed": "bad" } as Record<string, Tone>)[s] || "neutral";
}

export const ROLE_LABEL: Record<string, string> = {
  viewer: "Viewer", developer: "Developer", reviewer: "Reviewer", release_manager: "Release manager", admin: "Administrator",
};
