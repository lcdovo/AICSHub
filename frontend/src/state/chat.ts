import { create } from "zustand";
import type { Room } from "../features/chat/protocol";
type ChatStore = {
  rooms: Record<string, Room>;
  drafts: Record<string, string>;
  setRoom: (id: string, value: Room | ((room?: Room) => Room)) => void;
  setDraft: (id: string, value: string) => void;
  clear: () => void;
};
export const useChat = create<ChatStore>((set) => ({
  rooms: {},
  drafts: {},
  setRoom: (id, value) =>
    set((state) => ({
      rooms: {
        ...state.rooms,
        [id]: typeof value === "function" ? value(state.rooms[id]) : value,
      },
    })),
  setDraft: (id, value) =>
    set((state) => ({ drafts: { ...state.drafts, [id]: value } })),
  clear: () => set({ rooms: {}, drafts: {} }),
}));
