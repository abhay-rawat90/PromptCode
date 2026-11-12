import { useEffect } from "react";

/** Sets the browser tab title for the current page ("Leaderboard · PromptCode"). */
export function useTitle(title?: string | null) {
  useEffect(() => {
    document.title = title ? `${title} · PromptCode` : "PromptCode — Prompt Engineering Practice";
  }, [title]);
}
