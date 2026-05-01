import re
from pathlib import Path

DOMAIN_MAP = {
    "hackerrank": "HackerRank",
    "claude": "Claude",
    "visa": "Visa",
}


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)


def _extract_title(raw: str) -> str:
    body = _strip_frontmatter(raw)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lstrip("#").strip()
    return "Untitled"


def _clean_text(raw: str) -> str:
    text = _strip_frontmatter(raw)
    text = re.sub(r"<[^>]+>", "", text)                       # HTML tags
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # [text](url) -> text
    text = re.sub(r"https?://\S+", "", text)                   # bare URLs
    text = re.sub(r"[#*_`~|]", " ", text)                     # markdown punctuation
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _chunk(text: str, size: int = 250, overlap: int = 50) -> list[str]:
    words = text.split()
    if not words:
        return []
    stride = size - overlap
    return [
        " ".join(words[i : i + size])
        for i in range(0, len(words), stride)
        if words[i : i + size]
    ]


def load_corpus(data_dir: str) -> list[dict]:
    data_path = Path(data_dir)
    chunks: list[dict] = []
    stats: dict[str, tuple[int, int]] = {}

    for folder_name, domain in DOMAIN_MAP.items():
        domain_path = data_path / folder_name
        if not domain_path.exists():
            continue

        file_count = 0
        chunk_count = 0

        for md_file in sorted(domain_path.rglob("*.md")):
            rel_parts = md_file.relative_to(domain_path).parts
            # files directly under domain folder get subdir "general"
            subdir = rel_parts[0] if len(rel_parts) > 1 else "general"

            raw = md_file.read_text(encoding="utf-8", errors="replace")
            title = _extract_title(raw)
            body = _clean_text(raw)

            for chunk_text in _chunk(body):
                chunks.append(
                    {
                        "domain": domain,
                        "subdir": subdir,
                        "title": title,
                        "text": chunk_text,
                        "filepath": str(md_file),
                    }
                )
                chunk_count += 1
            file_count += 1

        stats[domain] = (file_count, chunk_count)

    total_files = sum(f for f, _ in stats.values())
    total_chunks = sum(c for _, c in stats.values())
    print("Corpus loaded:")
    for domain, (f, c) in stats.items():
        print(f"  {domain}: {f} files, {c} chunks")
    print(f"  Total: {total_files} files, {total_chunks} chunks")

    return chunks
