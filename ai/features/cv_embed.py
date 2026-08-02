"""게임 헤더 이미지에서 CLIP으로 게임별 비주얼 임베딩을 뽑는다.

appid만 알면 예측 가능한 구형 CDN URL(steam/apps/{appid}/header.jpg)로 먼저
시도한다 — Store API 호출 없이 빠르게 된다. 다만 Steam이 최근 게임들은
해시가 포함된 새 경로(store_item_assets/steam/apps/{appid}/{hash}/header.jpg)
로 옮겨서 예측이 불가능하다 (실측: appid가 대략 380만 이상인 최근 게임은
전부 이 케이스). 이 경우에만 Store appdetails를 호출해 실제 header_image
URL을 받아온다.

이미지를 로컬에 저장하지 않고 바로 임베딩만 뽑고 버린다 (필요한 건
벡터뿐). CLIP은 이미지-텍스트 멀티모달 모델이라 "장르/태그로는 안 잡히지만
비주얼 아트스타일이 비슷한" 케이스를 보완하는 게 목적 — 하이브리드 단계에서
장르/태그, NLP 리뷰 신호와 결합한다.

기존 ai/artifacts/cv_embeddings.npz에 이미 있는 appid는 건너뛰므로
재실행해도 이어서 처리된다 (resumable).
"""

import argparse
import io
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from sentence_transformers import SentenceTransformer

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "games.db"
ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "cv_embeddings.npz"
HEADER_IMAGE_URL = "https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
STORE_API = "https://store.steampowered.com/api/appdetails"
MODEL_NAME = "clip-ViT-B-32"
BATCH_SIZE = 32
STORE_FALLBACK_DELAY_SEC = 1.5

sys.stdout.reconfigure(encoding="utf-8")


def load_appids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT appid FROM games WHERE sections IS NOT NULL ORDER BY appid"
    ).fetchall()
    return [row[0] for row in rows]


def load_existing() -> tuple[list[int], list[np.ndarray]]:
    if not ARTIFACT_PATH.exists():
        return [], []
    data = np.load(ARTIFACT_PATH)
    return list(int(a) for a in data["appids"]), list(data["embeddings"])


def fetch_store_header_url(appid: int) -> str | None:
    try:
        resp = requests.get(STORE_API, params={"appids": appid, "cc": "us"}, timeout=15)
        entry = resp.json().get(str(appid))
        if entry and entry.get("success"):
            return entry["data"].get("header_image")
    except (requests.RequestException, ValueError):
        pass
    return None


def fetch_image(appid: int) -> Image.Image | None:
    try:
        resp = requests.get(HEADER_IMAGE_URL.format(appid=appid), timeout=15)
    except requests.RequestException:
        resp = None

    if resp is None or resp.status_code != 200:
        url = fetch_store_header_url(appid)
        time.sleep(STORE_FALLBACK_DELAY_SEC)
        if not url:
            return None
        try:
            resp = requests.get(url, timeout=15)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None

    try:
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def main(limit: int | None) -> None:
    conn = sqlite3.connect(DB_PATH)
    all_appids = load_appids(conn)
    conn.close()

    ok_appids, embeddings = load_existing()
    done = set(ok_appids)
    appids = [a for a in all_appids if a not in done]
    total_pending = len(appids)
    if limit:
        appids = appids[:limit]
    print(f"기존 {len(done)}개 재사용, 남은 대상 {total_pending}개 중 이번 실행에서 {len(appids)}개 처리")

    model = SentenceTransformer(MODEL_NAME)

    batch_imgs: list[Image.Image] = []
    batch_appids: list[int] = []

    def flush() -> None:
        if not batch_imgs:
            return
        vecs = model.encode(batch_imgs, batch_size=BATCH_SIZE, show_progress_bar=False)
        embeddings.extend(vecs)
        ok_appids.extend(batch_appids)
        batch_imgs.clear()
        batch_appids.clear()

    new_ok_count = 0
    for i, appid in enumerate(appids, start=1):
        img = fetch_image(appid)
        if img is not None:
            batch_imgs.append(img)
            batch_appids.append(appid)
            new_ok_count += 1
        if len(batch_imgs) >= BATCH_SIZE:
            flush()
        if i % 200 == 0 or i == len(appids):
            print(f"[{i}/{len(appids)}] 성공 {new_ok_count}개")
    flush()

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        ARTIFACT_PATH,
        appids=np.array(ok_appids),
        embeddings=np.array(embeddings, dtype=np.float32),
    )
    print(
        f"저장 완료: {ARTIFACT_PATH} (총 {len(ok_appids)}개 게임, "
        f"{embeddings[0].shape[0]}차원, 이번 실행 신규 {new_ok_count}개)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
