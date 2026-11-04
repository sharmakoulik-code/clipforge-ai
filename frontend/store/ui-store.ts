import { create } from "zustand";

type UiState = { sidebarOpen: boolean; setSidebarOpen: (open: boolean) => void };

export const useUiStore = create<UiState>((set) => ({
  sidebarOpen: true,
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
}));
