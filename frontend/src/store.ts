import { create } from 'zustand';
import type { User } from './types';
import { tokenStore } from './api';

type AuthState = { user: User | null; hydrated: boolean; setSession: (user: User, access: string, refresh: string) => void; clear: () => void; hydrate: () => void };
export const useAuth = create<AuthState>((set) => ({
  user: null, hydrated: false,
  setSession: (user, access, refresh) => { tokenStore.set(access, refresh); localStorage.setItem('cs_user', JSON.stringify(user)); set({ user, hydrated: true }); },
  clear: () => { tokenStore.clear(); set({ user: null, hydrated: true }); },
  hydrate: () => { const raw = localStorage.getItem('cs_user'); set({ user: raw ? JSON.parse(raw) : null, hydrated: true }); },
}));
