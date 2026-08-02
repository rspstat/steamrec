import { useEffect, useState } from "react";
import AuthBar from "../components/AuthBar";
import SectionRow from "../components/SectionRow";
import { fetchCurrentUser, type CurrentUser } from "../api/auth";
import {
  fetchMyRecommendations,
  fetchSection,
  fetchSimilarGames,
  type Game,
  type SectionKey,
  type SimilarGame,
} from "../api/games";

const SECTIONS: { key: SectionKey; title: string }[] = [
  { key: "trending", title: "신규 인기 급상승" },
  { key: "new_release", title: "최신 출시, 반응 좋은" },
  { key: "indie", title: "인기 인디 게임" },
  { key: "multiplayer", title: "멀티플레이 게임" },
];

type SectionState = Record<SectionKey, Game[] | null>;

export default function Home() {
  const [sections, setSections] = useState<SectionState>(
    Object.fromEntries(SECTIONS.map((s) => [s.key, null])) as SectionState
  );
  const [sectionErrors, setSectionErrors] = useState<Partial<Record<SectionKey, boolean>>>({});
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [userChecked, setUserChecked] = useState(false);
  const [recommendations, setRecommendations] = useState<Game[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [similar, setSimilar] = useState<SimilarGame[] | null>(null);

  useEffect(() => {
    SECTIONS.forEach(({ key }) => {
      fetchSection(key)
        .then((games) => setSections((prev) => ({ ...prev, [key]: games })))
        .catch(() => setSectionErrors((prev) => ({ ...prev, [key]: true })));
    });
    fetchCurrentUser()
      .then(setUser)
      .finally(() => setUserChecked(true));
  }, []);

  useEffect(() => {
    if (!user) {
      setRecommendations(null);
      return;
    }
    fetchMyRecommendations()
      .then(setRecommendations)
      .catch(() => setRecommendations([]));
  }, [user]);

  useEffect(() => {
    if (selected === null) return;
    setSimilar(null);
    fetchSimilarGames(selected)
      .then(setSimilar)
      .catch(() => setSimilar([]));
  }, [selected]);

  return (
    <div className="home">
      <div className="home-header">
        <h1>steamrec</h1>
        {userChecked && <AuthBar user={user} onLoggedOut={() => setUser(null)} />}
      </div>

      {user && <SectionRow title="나를 위한 추천" games={recommendations} onSelect={setSelected} />}

      {SECTIONS.map(({ key, title }) => (
        <SectionRow
          key={key}
          title={title}
          games={sections[key]}
          error={sectionErrors[key]}
          onSelect={setSelected}
        />
      ))}

      {selected !== null && (
        <SectionRow title="이 게임과 비슷한 게임" games={similar} onSelect={setSelected} />
      )}
    </div>
  );
}
