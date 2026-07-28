import { useEffect, useState } from "react";
import AuthBar from "../components/AuthBar";
import GameCard from "../components/GameCard";
import {
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

export default function Home() {
  const [sections, setSections] = useState<Record<SectionKey, Game[]>>(
    {} as Record<SectionKey, Game[]>
  );
  const [selected, setSelected] = useState<number | null>(null);
  const [similar, setSimilar] = useState<SimilarGame[]>([]);

  useEffect(() => {
    SECTIONS.forEach(({ key }) => {
      fetchSection(key).then((games) => {
        setSections((prev) => ({ ...prev, [key]: games }));
      });
    });
  }, []);

  useEffect(() => {
    if (selected === null) return;
    fetchSimilarGames(selected).then(setSimilar);
  }, [selected]);

  return (
    <div className="home">
      <div className="home-header">
        <h1>steamrec</h1>
        <AuthBar />
      </div>

      {SECTIONS.map(({ key, title }) => (
        <section key={key} className="section-row">
          <h2>{title}</h2>
          <div className="game-row">
            {(sections[key] ?? []).map((game) => (
              <GameCard key={game.appid} game={game} onClick={setSelected} />
            ))}
          </div>
        </section>
      ))}

      {selected !== null && (
        <section className="section-row">
          <h2>이 게임과 비슷한 게임</h2>
          <div className="game-row">
            {similar.map((game) => (
              <GameCard key={game.appid} game={game} onClick={setSelected} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
