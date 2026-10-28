"""One-time local model download; runtime search is configured to use local files only."""
from pathlib import Path

from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
OUTPUT = ROOT / "backend" / "data" / "models" / "all-MiniLM-L6-v2"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(MODEL_ID, device="cpu", local_files_only=False, trust_remote_code=False)
    model.save(str(OUTPUT))
    print(f"Saved {MODEL_ID} to {OUTPUT}")
    print("Set EMBEDDING_MODEL_NAME=backend/data/models/all-MiniLM-L6-v2 in .env")


if __name__ == "__main__":
    main()
