import { useSyncExternalStore } from "react";
export type PageId =
  | "chat"
  | "history"
  | "tickets"
  | "handoff"
  | "knowledge"
  | "search"
  | "feedback"
  | "analytics"
  | "users"
  | "account";
export const adminPages: PageId[] = [
  "feedback",
  "handoff",
  "knowledge",
  "search",
  "analytics",
  "users",
];
export function navigate(page: PageId, id?: string) {
  window.location.hash = "/" + page + (id ? "/" + encodeURIComponent(id) : "");
}
const subscribe = (callback: () => void) => {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
};
export function useRoute() {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  const [page = "chat", encoded] = hash.replace(/^#\/?/, "").split("/");
  let id: string | undefined;
  try {
    id = encoded ? decodeURIComponent(encoded) : undefined;
  } catch {
    id = undefined;
  }
  return { page: (page || "chat") as PageId, id };
}
