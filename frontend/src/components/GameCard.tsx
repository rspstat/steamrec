import { useState } from "react";
import type { Game } from "../api/games";

function formatPrice(price: number | null): string {
  if (price === null) return "";
  if (price === 0) return "무료";
  // price는 수집 시점 통화 기준 cent 단위 원본 값 (지역/통화 정규화는 아직 안 함).
  return `$${(price / 100).toFixed(2)}`;
}

// 예측 가능한 구형 CDN 경로 — 최근 게임(대략 appid 380만+)은 해시 포함 경로라
// 이 URL로는 404가 나기도 한다 (DATA_PIPELINE_DESIGN.md 참고). 그런 경우
// onError로 이미지 자리를 숨기고 장르 배경만 보여준다.
function headerImageUrl(appid: number): string {
  return `https://cdn.akamai.steamstatic.com/steam/apps/${appid}/header.jpg`;
}

interface Props {
  game: Game;
  onClick?: (appid: number) => void;
}

export default function GameCard({ game, onClick }: Props) {
  const [imageFailed, setImageFailed] = useState(false);

  return (
    <div className="game-card" onClick={() => onClick?.(game.appid)}>
      <div className="game-card-image">
        {!imageFailed && (
          <img
            src={headerImageUrl(game.appid)}
            alt=""
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        )}
      </div>
      <div className="game-card-body">
        <div className="game-card-name">{game.name}</div>
        <div className="game-card-genre">{game.genre}</div>
        <div className="game-card-meta">
          {game.review_pct !== null && (
            <span className="game-card-review">{game.review_pct}% 긍정</span>
          )}
          <span className="game-card-price">{formatPrice(game.price)}</span>
        </div>
      </div>
    </div>
  );
}
