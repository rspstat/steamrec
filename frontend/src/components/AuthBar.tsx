import { useEffect, useState } from "react";
import { fetchCurrentUser, loginUrl, logout, type CurrentUser } from "../api/auth";

export default function AuthBar() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchCurrentUser()
      .then(setUser)
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) return null;

  if (!user) {
    return (
      <a className="auth-bar login-button" href={loginUrl()}>
        Steam으로 로그인
      </a>
    );
  }

  return (
    <div className="auth-bar">
      {user.avatar_url && <img src={user.avatar_url} alt="" className="auth-avatar" />}
      <span>{user.persona_name ?? user.steamid}</span>
      <span className="auth-owned-count">보유 게임 {user.owned_games_count}개</span>
      <button
        onClick={async () => {
          await logout();
          setUser(null);
        }}
      >
        로그아웃
      </button>
    </div>
  );
}
