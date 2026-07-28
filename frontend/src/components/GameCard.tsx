import type { Game } from "../api/games";

function formatPrice(price: number | null): string {
  if (price === null) return "";
  if (price === 0) return "무료";
  // price는 수집 시점 통화 기준 cent 단위 원본 값 (지역/통화 정규화는 아직 안 함).
  return `$${(price / 100).toFixed(2)}`;
}

interface Props {
  game: Game;
  onClick?: (appid: number) => void;
}

export default function GameCard({ game, onClick }: Props) {
  return (
    <div className="game-card" onClick={() => onClick?.(game.appid)}>
      <div className="game-card-name">{game.name}</div>
      <div className="game-card-genre">{game.genre}</div>
      <div className="game-card-meta">
        {game.review_pct !== null && (
          <span className="game-card-review">{game.review_pct}% 긍정</span>
        )}
        <span className="game-card-price">{formatPrice(game.price)}</span>
      </div>
    </div>
  );
}
