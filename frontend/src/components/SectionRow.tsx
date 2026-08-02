import GameCard from "./GameCard";
import type { Game } from "../api/games";

interface Props {
  title: string;
  games: Game[] | null; // null = 로딩 중
  error?: boolean;
  onSelect: (appid: number) => void;
}

export default function SectionRow({ title, games, error, onSelect }: Props) {
  return (
    <section className="section-row">
      <h2>{title}</h2>
      {error ? (
        <div className="section-status">불러오지 못했습니다.</div>
      ) : games === null ? (
        <div className="section-status">불러오는 중...</div>
      ) : games.length === 0 ? (
        <div className="section-status">표시할 게임이 없습니다.</div>
      ) : (
        <div className="game-row">
          {games.map((game) => (
            <GameCard key={game.appid} game={game} onClick={onSelect} />
          ))}
        </div>
      )}
    </section>
  );
}
