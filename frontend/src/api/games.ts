import axios from "axios";

export interface Game {
  appid: number;
  name: string;
  genre: string | null;
  tags: string[];
  price: number | null;
  positive: number | null;
  negative: number | null;
  review_pct: number | null;
  review_count: number | null;
  release_date: string | null;
  sections: string[];
}

export interface SimilarGame extends Game {
  score: number;
}

export type SectionKey = "trending" | "new_release" | "indie" | "multiplayer";

const client = axios.create({ baseURL: "/api", withCredentials: true });

export async function fetchSection(section: SectionKey, limit = 12): Promise<Game[]> {
  const res = await client.get<Game[]>(`/games/sections/${section}`, {
    params: { limit },
  });
  return res.data;
}

export async function fetchGame(appid: number): Promise<Game> {
  const res = await client.get<Game>(`/games/${appid}`);
  return res.data;
}

export async function fetchSimilarGames(appid: number, limit = 10): Promise<SimilarGame[]> {
  const res = await client.get<SimilarGame[]>(`/games/${appid}/similar`, {
    params: { limit },
  });
  return res.data;
}

export async function fetchMyRecommendations(limit = 12): Promise<Game[]> {
  const res = await client.get<Game[]>("/users/me/recommendations", {
    params: { limit },
  });
  return res.data;
}
