"""매일 자동으로 데이터 파이프라인을 재실행하는 스케줄러.

Celery는 브로커(Redis 등)가 필요해서 지금 규모엔 과하다 — APScheduler로
충분하다 (외부 인프라 없이 이 프로세스 하나만 계속 떠 있으면 됨).

각 단계 스크립트는 이미 resumable하게 짜여 있어서(이미 처리된 appid는
자동으로 건너뜀) 매일 다시 돌려도 그날 새로 생긴 것만 처리한다:
  1) discover_sections.py — Steam 스토어 검색으로 트렌딩/신작/인디/멀티
     섹션을 다시 긁어서 새로 등장한 게임을 games 테이블에 추가
  2) enrich_selected.py / enrich_genre_fallback.py — 1)에서 새로 잡힌
     게임 중 장르/태그 없는 것만 보강
  3) fetch_reviews.py / cv_embed.py / nlp_embed.py — 새 게임의 리뷰/이미지/
     텍스트 임베딩 수집
  4) hybrid.py / collaborative.py — 콘텐츠 유사도 + CF 추천 전체 재계산
     (몇천~만 개 규모라 매번 통째로 다시 계산해도 몇 분 내로 끝남 — 증분
     갱신 로직은 아직 없음)

expand_catalog.py는 넣지 않았다 — "리뷰 상위 1만 개"라는 고정 목표치를
가진 1회성 확장이라 매일 돌 필요가 없다 (필요하면 수동으로 재실행).

실행: `python scheduler.py` (계속 떠 있으면서 매일 04:00에 실행) 또는
`python scheduler.py --run-now`로 즉시 1회 실행 후 스케줄 대기.
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

ROOT = Path(__file__).resolve().parent
COLLECT_DIR = ROOT / "collect"
FEATURES_DIR = ROOT / "features"
MODELS_DIR = ROOT / "models"
PYTHON = sys.executable

PIPELINE = [
    (COLLECT_DIR, "discover_sections.py"),
    (COLLECT_DIR, "enrich_selected.py"),
    (COLLECT_DIR, "enrich_genre_fallback.py"),
    (COLLECT_DIR, "fetch_reviews.py"),
    (FEATURES_DIR, "cv_embed.py"),
    (FEATURES_DIR, "nlp_embed.py"),
    (MODELS_DIR, "hybrid.py"),
    (MODELS_DIR, "collaborative.py"),
]

sys.stdout.reconfigure(encoding="utf-8")


def run_script(script_dir: Path, script: str) -> None:
    print(f"[{datetime.now().isoformat()}] 실행: {script}")
    result = subprocess.run([PYTHON, "-u", script], cwd=script_dir)
    status = "성공" if result.returncode == 0 else f"실패(exit {result.returncode})"
    print(f"[{datetime.now().isoformat()}] {script} {status}")


def daily_pipeline() -> None:
    print(f"===== 파이프라인 시작: {datetime.now().isoformat()} =====")
    for script_dir, script in PIPELINE:
        run_script(script_dir, script)
    print(f"===== 파이프라인 완료: {datetime.now().isoformat()} =====")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true", help="시작하자마자 1회 즉시 실행")
    args = parser.parse_args()

    scheduler = BlockingScheduler()
    scheduler.add_job(daily_pipeline, "cron", hour=4, minute=0)
    print("스케줄러 시작 — 매일 04:00에 파이프라인 재실행")

    if args.run_now:
        daily_pipeline()

    scheduler.start()


if __name__ == "__main__":
    main()
