import axios from "axios";

const client = axios.create({ baseURL: "/api", withCredentials: true });

export interface CurrentUser {
  steamid: string;
  persona_name: string | null;
  avatar_url: string | null;
  owned_games_count: number;
}

export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  const res = await client.get<CurrentUser | null>("/auth/me");
  return res.data;
}

export function loginUrl(): string {
  return "/api/auth/login";
}

export async function logout(): Promise<void> {
  await client.post("/auth/logout");
}
