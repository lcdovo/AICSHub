import { create } from "zustand";
import { api } from "../api";
import { clearCredentials, tokens } from "../api/client";
import { useChat } from "./chat";
import type { Credentials, User } from "../api/types";
export function publicUser(data: User): User {
  return {
    id: data.id,
    username: data.username,
    nickname: data.nickname,
    role: data.role,
    status: data.status,
    level: data.level,
  };
}
type Auth = {
  user: User | null;
  ready: boolean;
  expired: boolean;
  error: unknown;
  accept: (data: Credentials) => void;
  clear: (expired?: boolean) => void;
  bootstrap: () => Promise<void>;
};
let generation = 0;
let boot: Promise<void> | undefined;
export const useAuth = create<Auth>((set) => ({
  user: null,
  ready: false,
  expired: false,
  error: null,
  accept: (data) => {
    generation++;
    const user = publicUser(data);
    tokens.set(data.access_token, data.refresh_token);
    localStorage.setItem("cs_user", JSON.stringify(user));
    set({ user, ready: true, expired: false, error: null });
  },
  clear: (expired = false) => {
    generation++;
    clearCredentials();
    useChat.getState().clear();
    set({ user: null, ready: true, expired, error: null });
  },
  bootstrap: async () => {
    if (boot) return boot;
    const current = generation;
    boot = (async () => {
      try {
        const raw = localStorage.getItem("cs_user");
        if (!raw || !tokens.access) {
          set({ ready: true, user: null });
          return;
        }
        const stored = JSON.parse(raw) as User;
        if (!Number.isInteger(stored.id)) throw new Error("本地登录状态无效");
        const user = await api.me(stored.id);
        if (generation !== current) return;
        localStorage.setItem("cs_user", JSON.stringify(publicUser(user)));
        set({ user: publicUser(user), ready: true, error: null });
      } catch (error) {
        if (generation === current) set({ ready: true, user: null, error });
      }
    })().finally(() => {
      boot = undefined;
    });
    return boot;
  },
}));
