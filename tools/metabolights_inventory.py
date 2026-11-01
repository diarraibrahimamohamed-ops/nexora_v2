#!/usr/bin/env python3
"""Metadata-only inventory of a public MetaboLights study; never downloads MS data."""
from __future__ import annotations
import argparse, csv, hashlib, json, re, sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin
from urllib.request import Request, urlopen

WS = "https://www.ebi.ac.uk/metabolights/ws"
ARCHIVE = "https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public"
USER_AGENT = "Nexora-Phase1K-Inventory/1.0 (metadata-only)"
STUDY_RE = re.compile(r"^MTBLS[0-9]+$", re.I)

class InventoryError(RuntimeError):
    pass

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

def get_bytes(url: str, max_bytes: int = 25 * 1024 * 1024) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=25) as response:
            payload = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise InventoryError(f"GET {url}: {exc}") from exc
    if len(payload) > max_bytes:
        raise InventoryError(f"Response exceeded {max_bytes} bytes: {url}")
    return payload

def safe_isa_filename(value: str) -> str:
    name = unquote(value).strip()
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) != 1 or ".." in path.parts or "\\" in name:
        raise InventoryError(f"Unsafe ISA-Tab filename: {value}")
    if not re.fullmatch(r"(i_Investigation|s_MTBLS[0-9]+|a_MTBLS[0-9]+.*)\\.txt", name, re.I):
        raise InventoryError(f"Unexpected ISA-Tab filename: {value}")
    return name

def api_file_listing(study_id: str) -> list[dict]:
    try:
        payload = json.loads(get_bytes(f"{WS}/studies/{study_id}/files?include_raw_data=false").decode("utf-8"))
    except (InventoryError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    rows = payload.get("study", payload.get("files", [])) if isinstance(payload, dict) else payload
    if isinstance(rows, dict):
        rows = rows.get("files", [])
    result = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, str):
            result.append({"name": row, "source": "metabolights_api"})
        elif isinstance(row, dict):
            name = row.get("file") or row.get("name") or row.get("filename") or row.get("path")
            if name:
                result.append({"name": str(name), "type": row.get("type", "unknown"), "source": "metabolights_api"})
    return result

def archive_listing(study_id: str) -> tuple[list[dict], list[str]]:
    root = f"{ARCHIVE}/{study_id}/"
    queue = [(root, 0)]
    visited, files, notes = set(), [], []
    max_directories, max_files, max_depth = 2000, 100000, 5
    while queue:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if len(visited) > max_directories:
            notes.append(f"Directory listing cap reached ({max_directories}).")
            break
        if depth > max_depth:
            notes.append(f"Directory depth cap reached ({max_depth}).")
            continue
        try:
            html = get_bytes(url, max_bytes=4 * 1024 * 1024).decode("utf-8", "replace")
        except InventoryError as exc:
            notes.append(str(exc))
            continue
        parser = LinkParser()
        parser.feed(html)
        for link in parser.links:
            if link in {"../", "./", "/"} or link.startswith(("?", "#")):
                continue
            full = urljoin(url, link)
            if not full.startswith(root):
                continue
            relative = unquote(full[len(root):]).split("?", 1)[0]
            if not relative:
                continue
            if link.endswith("/"):
                if depth < max_depth:
                    queue.append((full, depth + 1))
            elif not any(row["name"] == relative for row in files):
                if len(files) >= max_files:
                    notes.append(f"File listing cap reached ({max_files}).")
                    return files, notes
                files.append({"name": relative, "format": PurePosixPath(relative).suffix.lower().lstrip("."), "source": "public_https_archive"})
    return files, notes

def fetch_metadata(study_id: str, filename: str) -> dict:
    filename = safe_isa_filename(filename)
    urls = (
        f"{WS}/studies/{study_id}/{quote(filename)}",
        f"{WS}/studies/{study_id}/download?file={quote(filename)}",
        f"{ARCHIVE}/{study_id}/{quote(filename)}",
    )
    failures = []
    for url in urls:
        try:
            body = get_bytes(url)
            text = body.decode("utf-8-sig")
            if not text.strip():
                raise InventoryError("Empty metadata response")
            return {"filename": filename, "text": text, "url": url, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        except (InventoryError, UnicodeDecodeError) as exc:
            failures.append(str(exc))
    raise InventoryError(f"Cannot retrieve {filename}: {' | '.join(failures)}")

def parse_tsv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(text.splitlines(), delimiter="\\t")
    if not reader.fieldnames:
        return [], []
    headers = [str(value).strip() for value in reader.fieldnames]
    rows = [{str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None} for row in reader]
    return headers, rows

def sample_role(row: dict) -> str:
    text = " ".join(f"{key} {value}" for key, value in row.items()).lower()
    if any(term in text for term in ("pooled qc", "qc pooled", "pooled quality control", "quality control pool", "qc pool")):
        return "pooled_qc_candidate"
    if any(term in text for term in ("solvent blank", "solvent control", "injection blank")):
        return "solvent_blank_candidate"
    if any(term in text for term in ("extraction blank", "process blank")):
        return "extraction_blank_candidate"
    if "blank" in text or "negative control" in text:
        return "blank_or_control_unresolved"
    if any(term in text for term in ("standard", "calibrator", "reference material")):
        return "standard_candidate"
    return "sample_or_unclassified"

def column_value(row: dict, *needles: str):
    for key, value in row.items():
        normalised = re.sub(r"[^a-z0-9]+", " ", key.lower()).strip()
        if value and any(needle in normalised for needle in needles):
            return value
    return None

def parse_isa(metadata: dict, archive_files: list[dict]) -> dict:
    sample_name = next((name for name in metadata if name.lower().startswith("s_")), None)
    investigation_name = next((name for name in metadata if name.lower().startswith("i_")), None)
    assay_names = sorted(name for name in metadata if name.lower().startswith("a_"))
    samples, sample_headers = [], []
    if sample_name:
        sample_headers, sample_data = parse_tsv(metadata[sample_name]["text"])
        samples = [
            {"sample_name": row.get("Sample Name", ""), "role_candidate": sample_role(row),
             "classification": "heuristic_only_manual_confirmation_required"}
            for row in sample_data if row.get("Sample Name")
        ]
    sample_by_name = {row["sample_name"]: row for row in samples}
    archive_names = {unquote(row["name"]).casefold() for row in archive_files}
    assay_summaries, references = [], []
    for assay_name in assay_names:
        headers, rows = parse_tsv(metadata[assay_name]["text"])
        assay_summaries.append({"file": assay_name, "row_count": len(rows), "columns": headers})
        for row in rows:
            raw_file = column_value(row, "raw spectral data file")
            derived_file = column_value(row, "derived spectral data file")
            row_text = " ".join(f"{key} {value}" for key, value in row.items()).lower()
            positive = bool(re.search(r"positive|positive ion|\\bpos\\b", row_text))
            negative = bool(re.search(r"negative|negative ion|\\bneg\\b", row_text))
            polarity = "positive" if positive and not negative else "negative" if negative and not positive else "unknown"
            order = column_value(row, "injection order", "injection number", "acquisition order", "run order")
            for file_reference, role in ((raw_file, "raw"), (derived_file, "derived")):
                if file_reference:
                    sample = row.get("Sample Name", "")
                    references.append({
                        "assay_file": assay_name, "sample_name": sample,
                        "sample_role_candidate": sample_by_name.get(sample, {}).get("role_candidate", "unclassified"),
                        "file_reference": file_reference, "file_role": role,
                        "polarity_candidate": polarity, "injection_order_candidate": order,
                        "resolves_in_archive": unquote(file_reference).casefold() in archive_names,
                        "manual_confirmation_required": True,
                    })
    counts = {}
    for sample in samples:
        role = sample["role_candidate"]
        counts[role] = counts.get(role, 0) + 1
    return {
        "investigation_file": investigation_name, "sample_file": sample_name,
        "sample_headers": sample_headers, "sample_count": len(samples),
        "sample_role_candidate_counts": counts, "samples": samples,
        "assays": assay_summaries, "assay_file_references": references,
    }

def build_inventory(study_id: str) -> dict:
    study_id = study_id.upper()
    if not STUDY_RE.fullmatch(study_id):
        raise InventoryError("Accession must match MTBLS followed by digits.")
    api_files = api_file_listing(study_id)
    archive_files, listing_notes = archive_listing(study_id)
    merged = {row["name"]: row for row in api_files}
    for row in archive_files:
        merged[row["name"]] = {**merged.get(row["name"], {}), **row}
    files = sorted(merged.values(), key=lambda row: row["name"].casefold())
    basenames = {PurePosixPath(row["name"]).name for row in files}
    metadata_names = {name for name in basenames if re.fullmatch(r"(i_Investigation|s_MTBLS[0-9]+|a_MTBLS[0-9]+.*)\\.txt", name, re.I)}
    metadata_names.update({"i_Investigation.txt", f"s_{study_id}.txt"})
    metadata, failures = {}, []
    for name in sorted(metadata_names):
        try:
            metadata[name] = fetch_metadata(study_id, name)
        except InventoryError as exc:
            failures.append(str(exc))
    isa = parse_isa(metadata, files)
    mzml_archive = [row for row in files if row["name"].lower().endswith(".mzml")]
    mzml_references = [row for row in isa["assay_file_references"] if row["file_reference"].lower().endswith(".mzml")]
    references = isa["assay_file_references"]
    positive_count = sum(row["polarity_candidate"] == "positive" for row in references)
    negative_count = sum(row["polarity_candidate"] == "negative" for row in references)
    pooled_qc = isa["sample_role_candidate_counts"].get("pooled_qc_candidate", 0)
    blockers = ["Accession-specific licence and submitter terms have not been verified."]
    if not files:
        blockers.append("Public archive listing unavailable; exact file formats cannot be confirmed.")
    if listing_notes:
        blockers.append("Archive listing incomplete or capped; completeness requires manual review.")
    if failures:
        blockers.append("One or more required ISA-Tab metadata files could not be retrieved.")
    if not isa["sample_file"] or not isa["assays"]:
        blockers.append("Sample and Assay sheets are required for sample-to-file mapping.")
    if not mzml_archive:
        blockers.append("No mzML file has been confirmed in the archive listing.")
    if not mzml_references:
        blockers.append("No mzML reference was found in the Assay metadata.")
    unresolved = [row for row in references if not row["resolves_in_archive"]]
    if unresolved:
        blockers.append(f"{len(unresolved)} Assay file reference(s) do not resolve in the public archive listing.")
    if not pooled_qc:
        blockers.append("Pooled-QC roles are not confirmed by retrieved sample metadata.")
    if not positive_count or not negative_count:
        blockers.append("Positive and negative assay mappings are not both identifiable.")
    if any(row["injection_order_candidate"] is None for row in references):
        blockers.append("Injection order is absent or unresolved; it must not be inferred from filenames or row order.")
    return {
        "schema_version": "1.0.0", "study_id": study_id,
        "inventory_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "retrieval": {
            "metadata_only": True, "raw_data_downloaded": False, "remote_file_count": len(files),
            "listing_notes": listing_notes, "metadata_download_failures": failures,
            "metadata_files": [
                {"filename": name, "sha256": item["sha256"], "bytes": item["bytes"], "url": item["url"]}
                for name, item in sorted(metadata.items())
            ],
        },
        "archive_inventory": files, "isa_tab": isa,
        "format_counts": {
            fmt: sum(1 for row in files if row.get("format", PurePosixPath(row["name"]).suffix.lstrip(".")).lower() == fmt)
            for fmt in sorted({row.get("format", PurePosixPath(row["name"]).suffix.lstrip(".")).lower() for row in files})
        },
        "mzml_archive_count": len(mzml_archive), "mzml_assay_reference_count": len(mzml_references),
        "polarity_reference_counts": {"positive": positive_count, "negative": negative_count},
        "pooled_qc_sample_candidates": pooled_qc,
        "licence": {
            "accession_specific_license": None, "status": "not_verified_for_this_accession",
            "repository_policy_note": "General repository policy does not replace confirmation of this accession's submission date and submitter terms.",
        },
        "readiness": {
            "k0_status": "PENDING" if blockers else "REQUIRES_MANUAL_REVIEW",
            "eligible_for_raw_download": False, "eligible_for_openms_processing": False,
            "blockers": blockers,
            "manual_review_required": [
                "Verify accession-level licence and submitter terms.",
                "Confirm sample roles, QC/blank/standard classification, polarities and injection order against study records.",
                "Resolve every Assay file reference and validate the selected mzML files before K1.",
            ],
        },
        "scope_guard": "Metadata and remote file paths only. No RAW/mzML download, conversion, import or scientific processing.",
    }

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_id")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = build_inventory(args.study_id)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    from pathlib import Path
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
    print(json.dumps({
        "study_id": result["study_id"], "k0_status": result["readiness"]["k0_status"],
        "remote_file_count": result["retrieval"]["remote_file_count"],
        "mzml_archive_count": result["mzml_archive_count"],
        "blockers": result["readiness"]["blockers"], "output": args.output,
    }, indent=2, ensure_ascii=False))
    return 0 if result["readiness"]["k0_status"] != "PENDING" else 1

if __name__ == "__main__":
    raise SystemExit(main())
